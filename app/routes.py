"""
FastAPI route handlers for audiobook-dl-web
"""

import json
import logging
import tomllib
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.services import SUPPORTED_SERVICES
from app.utils import is_valid_url

# Setup logger
logger = logging.getLogger(__name__)

# Setup templates
templates = Jinja2Templates(directory="app/templates")

# Create router
router = APIRouter()


# Read version from pyproject.toml
def get_app_version() -> str:
    """Read application version from pyproject.toml"""
    try:
        pyproject_path = Path("pyproject.toml")
        if pyproject_path.exists():
            with open(pyproject_path, "rb") as f:
                data = tomllib.load(f)
                return data.get("project", {}).get("version", "0.0.0")
    except Exception:
        pass
    return "0.0.0"


APP_VERSION = get_app_version()
GITHUB_REPO = "https://github.com/bartekmp/audiobook-dl-web"


def get_base_context(request: Request, config_manager) -> dict:
    """Get base template context used across all pages"""
    return {
        "request": request,
        "services": SUPPORTED_SERVICES,
        "configured_services": config_manager.list_configured_sources(),
        "version": APP_VERSION,
        "github_repo": GITHUB_REPO,
        "cache_bust": APP_VERSION.replace(".", ""),  # For static file cache busting
    }


def init_routes(config_manager, download_manager, config_dir: str, downloads_dir: str):
    """
    Initialize routes with dependency injection

    Args:
        config_manager: ConfigManager instance
        download_manager: DownloadManager instance
        config_dir: Configuration directory path
        downloads_dir: Downloads directory path
    """

    @router.get("/", response_class=HTMLResponse)
    async def index(request: Request):
        """Home page"""
        return templates.TemplateResponse(
            request=request,
            name="index.html",
            context=get_base_context(request, config_manager),
        )

    @router.get("/configure", response_class=HTMLResponse)
    async def configure_page(request: Request, service: str | None = None):
        """Configuration page for setting up service credentials"""
        if service and service not in SUPPORTED_SERVICES:
            raise HTTPException(status_code=404, detail="Service not found")

        context = get_base_context(request, config_manager)
        context["selected_service"] = None
        context["current_config"] = None

        if service:
            context["selected_service"] = {**SUPPORTED_SERVICES[service], "id": service}
            context["current_config"] = config_manager.get_source_config(service)

        return templates.TemplateResponse(
            request=request,
            name="configure.html",
            context=context,
        )

    @router.post("/configure/{service}")
    async def configure_service(
        service: str,
        username: str | None = Form(None),
        password: str | None = Form(None),
        library: str | None = Form(None),
    ):
        """Save service configuration"""
        if service not in SUPPORTED_SERVICES:
            raise HTTPException(status_code=404, detail="Service not found")

        success = config_manager.update_source_config(
            source_name=service, username=username, password=password, library=library
        )

        if success:
            # Log configuration change (don't log passwords)
            fields_updated = []
            if username:
                fields_updated.append("username")
            if password:
                fields_updated.append("password")
            if library:
                fields_updated.append("library")
            logger.info(
                f"Configuration updated - service: {service}, fields: {', '.join(fields_updated)}"
            )

            return RedirectResponse(
                url=f"/configure?service={service}&success=true",
                status_code=status.HTTP_303_SEE_OTHER,
            )
        else:
            logger.error(f"Failed to update configuration for service: {service}")
            raise HTTPException(status_code=500, detail="Failed to save configuration")

    @router.post("/configure/{service}/delete")
    async def delete_service_config(service: str):
        """Delete service configuration"""
        if service not in SUPPORTED_SERVICES:
            raise HTTPException(status_code=404, detail="Service not found")

        success = config_manager.remove_source_config(service)

        if success:
            logger.info(f"Configuration deleted - service: {service}")
            return RedirectResponse(url="/configure", status_code=status.HTTP_303_SEE_OTHER)
        else:
            logger.error(f"Failed to delete configuration for service: {service}")
            raise HTTPException(status_code=500, detail="Failed to delete configuration")

    @router.get("/download", response_class=HTMLResponse)
    async def download_page(request: Request):
        """Download page for audiobook-dl and Grawlix downloads."""
        return templates.TemplateResponse(
            request=request,
            name="download.html",
            context=get_base_context(request, config_manager),
        )

    @router.post("/api/download")
    async def start_download(
        urls: str = Form(...),
        combine: bool = Form(False),
        no_chapters: bool = Form(False),
        output_format: str | None = Form(None),
        output_template: str | None = Form(None),
        media_type: str = Form("audiobook"),
        selections: str | None = Form(None),
        audiobook_output_format: str | None = Form(None),
        audiobook_output_template: str | None = Form(None),
        ebook_output_format: str | None = Form(None),
        ebook_output_template: str | None = Form(None),
    ):
        """
        Start downloading audiobooks from provided URLs

        Args:
            urls: Newline-separated list of audiobook URLs
            combine: Whether to combine files into a single file
            no_chapters: Whether to exclude chapter information
            output_format: Output file format (mp3, m4b, etc.)
            output_template: Output path template
        """
        url_list = list(dict.fromkeys(url.strip() for url in urls.split("\n") if url.strip()))

        if not url_list:
            raise HTTPException(status_code=400, detail="No URLs provided")
        if media_type not in {"audiobook", "ebook"}:
            raise HTTPException(status_code=400, detail="Invalid media type")

        selected_types = {url: [media_type] for url in url_list}
        if selections:
            try:
                parsed = json.loads(selections)
                selected_types = {
                    item["url"]: [
                        kind for kind in ("audiobook", "ebook") if item.get(kind) is True
                    ]
                    for item in parsed
                    if isinstance(item, dict) and item.get("url") in url_list
                }
            except (json.JSONDecodeError, TypeError):
                raise HTTPException(status_code=400, detail="Invalid URL selections") from None

        logger.info(f"Adding {len(url_list)} URL(s) to download queue")

        tasks = []
        warnings = []

        for url in url_list:
            # Validate URL format
            if not is_valid_url(url):
                warning = {
                    "url": url,
                    "warning": "Invalid URL format",
                    "message": f"Skipped invalid URL: {url[:100]}",
                }
                warnings.append(warning)
                logger.warning(f"Invalid URL skipped: {url}")
                continue

            for selected_type in selected_types.get(url, []):
                task_id = str(uuid.uuid4())
                is_ebook = selected_type == "ebook"
                task = await download_manager.add_download(
                    url=url,
                    task_id=task_id,
                    output_template=(ebook_output_template if is_ebook else audiobook_output_template)
                    or output_template,
                    combine=combine if not is_ebook else False,
                    no_chapters=no_chapters if not is_ebook else False,
                    output_format=(ebook_output_format if is_ebook else audiobook_output_format)
                    or output_format,
                    media_type=selected_type,
                )
                tasks.append(task.to_dict())
                logger.info(
                    f"Task added to queue - ID: {task_id}, Type: {selected_type}, URL: {url}"
                )

        if not tasks and not warnings:
            raise HTTPException(status_code=400, detail="Select at least one download format")

        return JSONResponse(content={"tasks": tasks, "warnings": warnings})

    @router.get("/api/tasks")
    async def get_tasks():
        """Get all download tasks"""
        tasks = download_manager.get_all_tasks()
        return JSONResponse(content={"tasks": [task.to_dict() for task in tasks]})

    @router.get("/api/tasks/{task_id}")
    async def get_task(task_id: str):
        """Get specific download task status"""
        task = download_manager.get_task(task_id)

        if not task:
            raise HTTPException(status_code=404, detail="Task not found")

        return JSONResponse(content=task.to_dict())

    @router.post("/api/tasks/{task_id}/cancel")
    async def cancel_task(task_id: str):
        """Cancel a download task"""
        success = download_manager.cancel_task(task_id)

        if not success:
            raise HTTPException(status_code=400, detail="Cannot cancel task")

        return JSONResponse(content={"status": "cancelled"})

    @router.delete("/api/tasks/{task_id}")
    async def remove_task(task_id: str):
        """Remove a completed, failed, or cancelled task"""
        success = download_manager.remove_task(task_id)

        if not success:
            raise HTTPException(status_code=400, detail="Cannot remove task")

        return JSONResponse(content={"status": "removed"})

    @router.post("/api/tasks/clear")
    async def clear_completed_tasks():
        """Clear all completed, failed, and cancelled tasks"""
        download_manager.clear_completed()
        return JSONResponse(content={"status": "cleared"})

    @router.get("/settings", response_class=HTMLResponse)
    async def settings_page(request: Request):
        """Settings page for global audiobook-dl options"""
        context = get_base_context(request, config_manager)
        context["config"] = config_manager.load_config()
        context["config_file_path"] = config_manager.get_config_file_path()
        context["downloads_dir"] = downloads_dir

        return templates.TemplateResponse(
            request=request,
            name="settings.html",
            context=context,
        )

    @router.post("/settings")
    async def update_settings(
        output_template: str | None = Form(None),
        skip_downloaded: bool = Form(False),
        max_concurrent_downloads: int = Form(2),
        create_folder: bool = Form(False),
        group_by_author: bool = Form(False),
        move_after_completion: bool = Form(False),
        destination_path: str | None = Form(None),
        ebook_output_template: str | None = Form(None),
        move_ebooks_after_completion: bool = Form(False),
        ebook_destination_path: str | None = Form(None),
    ):
        """Update global settings"""
        destination_path = destination_path.strip() if destination_path else ""
        if move_after_completion and not destination_path:
            raise HTTPException(
                status_code=400,
                detail="A destination path is required when move after completion is enabled",
            )
        if move_after_completion and not Path(destination_path).is_absolute():
            raise HTTPException(
                status_code=400,
                detail="The destination path must be absolute",
            )

        ebook_destination_path = (
            ebook_destination_path.strip() if ebook_destination_path else ""
        )
        if move_ebooks_after_completion and not ebook_destination_path:
            raise HTTPException(
                status_code=400,
                detail="An ebook destination path is required when ebook transfer is enabled",
            )
        if move_ebooks_after_completion and not Path(ebook_destination_path).is_absolute():
            raise HTTPException(
                status_code=400,
                detail="The ebook destination path must be absolute",
            )

        success = config_manager.update_global_settings(
            output_template=output_template if output_template else None,
            skip_downloaded=skip_downloaded,
            max_concurrent_downloads=max_concurrent_downloads,
            create_folder=create_folder,
            group_by_author=group_by_author,
            move_after_completion=move_after_completion,
            destination_path=destination_path,
            ebook_output_template=ebook_output_template
            if ebook_output_template
            else "{authors}/{title}.{ext}",
            move_ebooks_after_completion=move_ebooks_after_completion,
            ebook_destination_path=ebook_destination_path,
        )

        if success:
            # Reload download manager config to apply new max concurrent downloads
            download_manager.reload_config()
            logger.info(
                f"Settings updated - output_template: {output_template}, skip_downloaded: {skip_downloaded}, max_concurrent_downloads: {max_concurrent_downloads}, create_folder: {create_folder}, group_by_author: {group_by_author}, move_after_completion: {move_after_completion}, destination_path: {destination_path}"
            )
            return RedirectResponse(
                url="/settings?success=true", status_code=status.HTTP_303_SEE_OTHER
            )
        else:
            logger.error("Failed to update settings")
            raise HTTPException(status_code=500, detail="Failed to update settings")

    @router.get("/health")
    async def health_check():
        """Health check endpoint"""
        return {
            "status": "healthy",
            "timestamp": datetime.now().isoformat(),
            "config_dir": config_dir,
            "downloads_dir": downloads_dir,
        }

    return router
