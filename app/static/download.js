// Download page functionality
// Handles form submission, progress updates, and task management

// Constants
const STATUS_ICONS = {
    pending: 'clock',
    downloading: 'arrow-down-circle',
    transferring: 'arrow-left-right',
    completed: 'check-circle',
    failed: 'x-circle',
    cancelled: 'dash-circle'
};

const STATUS_COLORS = {
    pending: 'secondary',
    downloading: 'primary',
    transferring: 'info',
    completed: 'success',
    failed: 'danger',
    cancelled: 'warning'
};

let updateInterval = null;
let collapsedTasks = new Set(); // Track which tasks are collapsed
let urlSelections = new Map();
let availabilityRequests = new Set();

// Initialize on page load
document.addEventListener('DOMContentLoaded', function () {
    // Load existing tasks
    loadTasks();

    const form = document.getElementById('downloadForm');
    const urls = document.getElementById('urls');
    const selectionList = document.getElementById('urlSelectionList');
    if (form) form.addEventListener('submit', handleFormSubmit);
    if (urls) urls.addEventListener('input', renderUrlSelections);
    if (selectionList) selectionList.addEventListener('change', rememberUrlSelection);

    // Setup clear completed button
    const clearBtn = document.getElementById('clearCompleted');
    if (clearBtn) {
        clearBtn.addEventListener('click', clearCompleted);
    }

    // Start polling for updates
    startPolling();
});

// Handle form submission
async function handleFormSubmit(e) {
    e.preventDefault();

    const form = e.target;
    const formData = new FormData(form);
    const urls = getParsedUrls();
    const selections = urls.map((url, index) => ({
        url,
        audiobook: document.querySelector(`[data-url-index="${index}"][data-type="audiobook"]`)?.checked === true,
        ebook: document.querySelector(`[data-url-index="${index}"][data-type="ebook"]`)?.checked === true
    }));
    if (!selections.some(item => item.audiobook || item.ebook)) {
        showNotification('Select at least one audiobook or e-book download.', 'warning');
        return;
    }
    formData.set('selections', JSON.stringify(selections));

    try {
        const response = await fetch('/api/download', {
            method: 'POST',
            body: formData
        });

        if (!response.ok) {
            const error = await response.json().catch(() => ({}));
            throw new Error(error.detail || 'Failed to start download');
        }

        const data = await response.json();

        // Clear the form
        form.reset();
        urlSelections.clear();
        renderUrlSelections();

        // Show success message with warning count if applicable
        if (data.warnings && data.warnings.length > 0) {
            showNotification(
                `${data.tasks.length} download(s) started. ${data.warnings.length} warning(s).`,
                'warning'
            );
            // Display warnings in the queue
            displayWarnings(data.warnings);
        } else {
            showNotification('Downloads started successfully!', 'success');
        }

        // Immediately load tasks to show the new downloads
        await loadTasks();

        // Ensure polling is active for new downloads
        if (!updateInterval) {
            startPolling();
        }

    } catch (error) {
        console.error('Error starting download:', error);
        showNotification('Failed to start downloads: ' + error.message, 'danger');
    }
}

function getParsedUrls() {
    const value = document.getElementById('urls')?.value || '';
    return [...new Set(value.split(/\r?\n/).map(url => url.trim()).filter(Boolean))];
}

function recognisedFormats(url) {
    let host = '';
    try { host = new URL(url).hostname.toLowerCase(); } catch (_) { return { audiobook: false, ebook: false }; }
    const shared = ['storytel.', 'mofibo.', 'saxo.', 'ereolen.'];
    const ebookOnly = ['royalroad.', 'fanfiction.net', 'webtoons.', 'marvel.', 'mangaplus.', 'archive.org'];
    if (shared.some(domain => host.includes(domain))) return { audiobook: true, ebook: true };
    if (ebookOnly.some(domain => host.includes(domain))) return { audiobook: false, ebook: true };
    return { audiobook: true, ebook: false };
}

function isNextoryBookUrl(url) {
    try {
        const parsed = new URL(url);
        return ['nextory.com', 'www.nextory.com'].includes(parsed.hostname.toLowerCase())
            && parsed.pathname.includes('/book/');
    } catch (_) {
        return false;
    }
}

async function checkUrlAvailability(url) {
    if (availabilityRequests.has(url)) return;
    availabilityRequests.add(url);
    try {
        const response = await fetch(`/api/url-availability?url=${encodeURIComponent(url)}`);
        if (!response.ok) throw new Error('Availability check failed');
        const formats = await response.json();
        urlSelections.set(url, {
            audiobook: formats.audiobook === true,
            ebook: formats.ebook === true,
            checking: false,
            verified: formats.verified === true
        });
    } catch (error) {
        urlSelections.set(url, {
            audiobook: false,
            ebook: false,
            checking: false,
            verified: false,
            error: 'Could not verify formats; choose manually.'
        });
    } finally {
        availabilityRequests.delete(url);
        renderUrlSelections();
    }
}

function rememberUrlSelection(event) {
    const checkbox = event.target.closest('[data-url-index][data-type]');
    if (!checkbox) return;
    const url = getParsedUrls()[Number(checkbox.dataset.urlIndex)];
    if (!url) return;
    const selected = urlSelections.get(url) || recognisedFormats(url);
    selected[checkbox.dataset.type] = checkbox.checked;
    urlSelections.set(url, selected);
}

function renderUrlSelections() {
    const urls = getParsedUrls();
    const wrapper = document.getElementById('urlSelection');
    const list = document.getElementById('urlSelectionList');
    if (!wrapper || !list) return;
    wrapper.classList.toggle('d-none', urls.length === 0);

    urls.forEach((url, index) => {
        if (!urlSelections.has(url)) {
            urlSelections.set(url, isNextoryBookUrl(url)
                ? { audiobook: false, ebook: false, checking: true, verified: false }
                : recognisedFormats(url));
        }
    });

    list.innerHTML = urls.map((url, index) => {
        const selected = urlSelections.get(url);
        const disabled = selected.checking ? 'disabled' : '';
        const availability = selected.checking
            ? '<div class="form-text"><span class="spinner-border spinner-border-sm me-1"></span>Checking availability…</div>'
            : selected.verified
                ? '<div class="form-text text-success"><i class="bi bi-check-circle"></i> Availability verified by Nextory</div>'
                : selected.error
                    ? `<div class="form-text text-warning"><i class="bi bi-exclamation-triangle"></i> ${escapeHtml(selected.error)}</div>`
                    : '';
        return `<div class="list-group-item" data-selection-row="${index}">
            <div class="text-break small mb-2"><i class="bi bi-link-45deg"></i> ${escapeHtml(url)}</div>
            <div class="d-flex flex-wrap gap-3">
                <div class="form-check"><input class="form-check-input" type="checkbox" data-url-index="${index}" data-type="audiobook" id="audio-${index}" ${selected.audiobook ? 'checked' : ''} ${disabled}><label class="form-check-label" for="audio-${index}"><i class="bi bi-headphones"></i> Download audiobook</label></div>
                <div class="form-check"><input class="form-check-input" type="checkbox" data-url-index="${index}" data-type="ebook" id="ebook-${index}" ${selected.ebook ? 'checked' : ''} ${disabled}><label class="form-check-label" for="ebook-${index}"><i class="bi bi-book"></i> Download e-book</label></div>
            </div>${availability}</div>`;
    }).join('');

    urls.forEach(url => {
        if (urlSelections.get(url)?.checking) checkUrlAvailability(url);
    });
}

// Load all tasks from the server
let previousDownloadingCount = 0;
let lastTasksJson = '';
let metadataWaitStart = null; // Track when we started waiting for metadata

async function loadTasks() {
    try {
        const response = await fetch('/api/tasks');

        if (!response.ok) {
            throw new Error('Failed to load tasks');
        }

        const data = await response.json();

        // Check if data actually changed
        const currentTasksJson = JSON.stringify(data.tasks);
        if (currentTasksJson === lastTasksJson) {
            return; // No changes, skip update
        }
        lastTasksJson = currentTasksJson;

        // Check if all downloads just finished
        const downloadingCount = data.tasks.filter(t =>
            t.status === 'downloading' || t.status === 'transferring' || t.status === 'pending'
        ).length;
        const completedCount = data.tasks.filter(t => t.status === 'completed').length;
        const totalCount = data.tasks.length;

        // If we had downloading tasks before and now none, and we have completed tasks
        if (previousDownloadingCount > 0 && downloadingCount === 0 && completedCount > 0 && totalCount > 0) {
            const failedCount = data.tasks.filter(t => t.status === 'failed').length;
            const message = failedCount > 0
                ? `All downloads finished! ${completedCount} completed, ${failedCount} failed.`
                : `All downloads completed successfully! (${completedCount} total)`;
            showNotification(message, failedCount > 0 ? 'warning' : 'success');

            // Start tracking time for metadata extraction
            if (!metadataWaitStart) {
                metadataWaitStart = Date.now();
            }
        }

        // Reset timer if downloads are active
        if (downloadingCount > 0) {
            metadataWaitStart = null;
        }

        previousDownloadingCount = downloadingCount;
        displayTasks(data.tasks);

        // Adjust polling based on active tasks
        adjustPolling(downloadingCount, data.tasks);

    } catch (error) {
        console.error('Error loading tasks:', error);
    }
}

// Display warnings for invalid URLs
function displayWarnings(warnings) {
    if (!warnings || warnings.length === 0) return;

    const taskList = document.getElementById('taskList');
    const warningCards = warnings.map(warning => createWarningCard(warning)).join('');

    // Prepend warnings to the task list
    taskList.insertAdjacentHTML('afterbegin', warningCards);

    // Auto-dismiss warnings after 10 seconds
    setTimeout(() => {
        document.querySelectorAll('.warning-card').forEach(card => {
            card.style.transition = 'opacity 0.5s';
            card.style.opacity = '0';
            setTimeout(() => card.remove(), 500);
        });
    }, 10000);
}

// Create HTML for a warning card
function createWarningCard(warning) {
    const warningLabel = warning.warning === 'Invalid URL format' ? 'INVALID URL' : 'WARNING';
    return `
        <div class="card mb-3 border-warning warning-card">
            <div class="card-body">
                <div class="d-flex align-items-start">
                    <div class="flex-grow-1">
                        <h6 class="mb-1">
                            <i class="bi bi-exclamation-triangle text-warning"></i>
                            <span class="badge bg-warning text-dark">${warningLabel}</span>
                        </h6>
                        <p class="mb-1 text-muted small">
                            ${escapeHtml(warning.url)}
                        </p>
                        <small class="text-danger">
                            <i class="bi bi-x-circle"></i> ${escapeHtml(warning.warning)}
                        </small>
                    </div>
                    <button class="btn btn-sm btn-outline-secondary" onclick="this.closest('.warning-card').remove()" title="Dismiss">
                        <i class="bi bi-x"></i>
                    </button>
                </div>
            </div>
        </div>
    `;
}

// Display tasks in the UI
function displayTasks(tasks) {
    const taskList = document.getElementById('taskList');

    if (!tasks || tasks.length === 0) {
        taskList.innerHTML = `
            <div class="text-center text-muted py-4">
                <i class="bi bi-inbox display-4"></i>
                <p class="mt-2">No downloads in queue</p>
            </div>
        `;
        return;
    }

    // Sort tasks: active first, then by start time
    tasks.sort((a, b) => {
        const statusOrder = { transferring: 0, downloading: 1, pending: 2, completed: 3, failed: 4, cancelled: 5 };
        const statusDiff = (statusOrder[a.status] ?? 6) - (statusOrder[b.status] ?? 6);
        if (statusDiff !== 0) return statusDiff;

        return new Date(b.started_at || 0) - new Date(a.started_at || 0);
    });

    // Add collapse all button and task cards
    const collapseAllBtn = `
        <div class="mb-3 d-flex justify-content-end">
            <button class="btn btn-sm btn-outline-secondary" id="toggleAllCards" onclick="toggleAllCards()">
                <i class="bi bi-chevron-up"></i> Collapse All
            </button>
        </div>
    `;

    taskList.innerHTML = collapseAllBtn + tasks.map(task => createTaskCard(task)).join('');

    // Restore collapse state after regenerating HTML
    collapsedTasks.forEach(taskId => {
        const card = document.querySelector(`[data-task-id="${taskId}"]`);
        if (card) {
            setTaskCollapsed(card, true);
        }
    });

    // Update button state based on collapsed tasks
    updateToggleAllButton();
}

// Update the toggle all button text based on state
function updateToggleAllButton() {
    const btn = document.getElementById('toggleAllCards');
    if (btn) {
        const hasCollapsed = collapsedTasks.size > 0;
        btn.innerHTML = hasCollapsed
            ? '<i class="bi bi-chevron-down"></i> Show All'
            : '<i class="bi bi-chevron-up"></i> Collapse All';
    }
}

// Set collapse state for a task card
function setTaskCollapsed(card, collapsed) {
    const detailsDiv = card.querySelector('.task-details');
    const chevron = card.querySelector('.collapse-chevron');

    detailsDiv.style.display = collapsed ? 'none' : 'block';
    chevron.classList.toggle('bi-chevron-down', collapsed);
    chevron.classList.toggle('bi-chevron-up', !collapsed);
}

// Toggle all task cards
function toggleAllCards() {
    const isExpanding = collapsedTasks.size > 0;

    document.querySelectorAll('.task-card').forEach(card => {
        const taskId = card.getAttribute('data-task-id');
        setTaskCollapsed(card, !isExpanding);

        if (isExpanding) {
            collapsedTasks.delete(taskId);
        } else {
            collapsedTasks.add(taskId);
        }
    });

    updateToggleAllButton();
}

// Toggle individual task card
function toggleTaskCard(taskId) {
    const card = document.querySelector(`[data-task-id="${taskId}"]`);
    const isCollapsed = collapsedTasks.has(taskId);

    setTaskCollapsed(card, !isCollapsed);

    if (isCollapsed) {
        collapsedTasks.delete(taskId);
    } else {
        collapsedTasks.add(taskId);
    }
}

// Extract service name from URL
function extractServiceName(url) {
    try {
        const urlObj = new URL(url);
        const hostname = urlObj.hostname;
        // Extract main domain (e.g., storytel.com -> storytel)
        const parts = hostname.split('.');
        if (parts.length >= 2) {
            return parts[parts.length - 2].toUpperCase();
        }
        return hostname.toUpperCase();
    } catch {
        return 'UNKNOWN';
    }
}

// Create progress bar HTML
function createProgressBar(task) {
    if (task.status !== 'downloading' && task.status !== 'transferring' && task.status !== 'completed') {
        return '';
    }

    const progressWidth = task.status === 'completed' ? 100 : task.progress;
    const color = STATUS_COLORS[task.status];
    const animated = task.status === 'downloading' || task.status === 'transferring'
        ? 'progress-bar-animated'
        : '';

    return `
        <div class="mb-2">
            <div class="d-flex justify-content-between align-items-center mb-1">
                <small class="text-muted">Progress</small>
                <small class="text-muted"><strong>${progressWidth}%</strong></small>
            </div>
            <div class="progress" style="height: 8px;">
                <div class="progress-bar progress-bar-striped ${animated} bg-${color}"
                     role="progressbar" style="width: ${progressWidth}%" 
                     aria-valuenow="${progressWidth}" aria-valuemin="0" aria-valuemax="100"></div>
            </div>
        </div>
    `;
}

// Create error message HTML
function createErrorDisplay(error) {
    if (!error) return '';

    const formattedError = escapeHtml(error).replace(/\n/g, '<br>');
    return `
        <div class="alert alert-danger mb-2">
            <strong><i class="bi bi-exclamation-triangle"></i> Error:</strong><br>
            <small style="white-space: pre-wrap;">${formattedError}</small>
        </div>
    `;
}

// Create file path display HTML
function createFilePathDisplay(task) {
    if (!task.output_file) return '';

    const isRetainedAfterFailure = task.status === 'failed' && task.transfer_destination;
    if (task.status !== 'completed' && !isRetainedAfterFailure) return '';

    const label = isRetainedAfterFailure ? 'Local file retained:' : 'Final file:';

    const files = task.output_files && task.output_files.length > 1
        ? task.output_files.map(file => `<code class="file-path-code d-block">${escapeHtml(file)}</code>`).join('')
        : `<code class="file-path-code">${escapeHtml(task.output_file)}</code>`;

    return `
        <div class="alert alert-info mt-2 mb-2 file-path-alert">
            <div class="d-flex align-items-start">
                <i class="bi bi-file-earmark-check fs-4 me-2 flex-shrink-0"></i>
                <div class="flex-grow-1">
                    <strong class="d-block mb-1">${label}</strong>
                    ${files}
                </div>
            </div>
        </div>
    `;
}

function createTransferDestinationDisplay(task) {
    if (!task.transfer_destination || task.status !== 'transferring') return '';
    return `
        <div class="alert alert-info mt-2 mb-2">
            <strong><i class="bi bi-hdd-network"></i> Copying to:</strong><br>
            <code class="file-path-code">${escapeHtml(task.transfer_destination)}</code>
        </div>
    `;
}

// Create HTML for a single task card
function createTaskCard(task) {
    const icon = STATUS_ICONS[task.status] || 'circle';
    const color = STATUS_COLORS[task.status] || 'secondary';
    const serviceName = extractServiceName(task.url);
    const mediaType = task.media_type === 'ebook' ? 'E-BOOK' : 'AUDIOBOOK';
    const mediaIcon = task.media_type === 'ebook' ? 'book' : 'headphones';
    const downloadingClass = task.status === 'downloading' || task.status === 'transferring'
        ? 'downloading-indicator'
        : '';

    return `
        <div class="card mb-3 task-card status-${task.status}" data-task-id="${task.task_id}">
            <div class="card-body">
                <div class="d-flex justify-content-between align-items-start mb-2">
                    <div class="flex-grow-1">
                        <h6 class="mb-1 d-flex align-items-center gap-2">
                            <i class="bi bi-chevron-up collapse-chevron" onclick="toggleTaskCard('${task.task_id}')" style="cursor: pointer;" title="Collapse/Expand"></i>
                            <i class="bi bi-${icon} text-${color} ${downloadingClass}"></i>
                            <span class="status-badge badge bg-${color}">${task.status.toUpperCase()}</span>
                            <span class="badge bg-dark"><i class="bi bi-${mediaIcon}"></i> ${mediaType}</span>
                            <span class="badge bg-secondary">${serviceName}</span>
                        </h6>
                        <p class="task-url mb-1 url-clickable" 
                           data-url="${escapeHtml(task.url)}" 
                           onclick="copyUrlToInput('${escapeHtml(task.url)}', '${task.media_type || 'audiobook'}')"
                           title="Click to re-add this URL to the download form">
                            ${escapeHtml(truncateUrl(task.url, 80))}
                        </p>
                        ${task.metadata ? createMetadataDisplay(task.metadata) : ''}
                    </div>
                    <div class="d-flex gap-1 task-actions">
                        ${task.status === 'downloading' ? `
                            <button class="btn btn-sm btn-outline-danger" onclick="cancelTask('${task.task_id}')" title="Cancel download">
                                <i class="bi bi-x"></i>
                            </button>
                        ` : ''}
                        ${task.status === 'completed' || task.status === 'failed' || task.status === 'cancelled' ? `
                            <button class="btn btn-sm btn-outline-danger remove-task-btn" onclick="removeTask('${task.task_id}')" title="Remove from list">
                                <i class="bi bi-x"></i>
                            </button>
                        ` : ''}
                        ${task.status === 'failed' ? `
                            <button class="btn btn-sm btn-outline-primary" onclick="retryTask('${escapeHtml(task.url)}', '${task.media_type || 'audiobook'}')" title="Retry download">
                                <i class="bi bi-arrow-clockwise"></i> Retry
                            </button>
                        ` : ''}
                    </div>
                </div>
                <div class="task-details">
                    <small class="text-muted d-block mb-2">${escapeHtml(task.message)}</small>
                    ${createProgressBar(task)}
                    ${createErrorDisplay(task.error)}
                    ${createTransferDestinationDisplay(task)}
                    ${createFilePathDisplay(task)}
                    ${formatTimestamp(task)}
                </div>
            </div>
        </div>
    `;
}

// Cancel a task
async function cancelTask(taskId) {
    try {
        const response = await fetch(`/api/tasks/${taskId}/cancel`, {
            method: 'POST'
        });

        if (!response.ok) {
            throw new Error('Failed to cancel task');
        }

        showNotification('Download cancelled', 'warning');
        await loadTasks();

    } catch (error) {
        console.error('Error cancelling task:', error);
        showNotification('Failed to cancel download', 'danger');
    }
}

// Create metadata display
function createMetadataDisplay(metadata) {
    if (!metadata || Object.keys(metadata).length === 0) {
        return '';
    }

    const items = [];

    if (metadata.title) {
        items.push(`<strong>${escapeHtml(metadata.title)}</strong>`);
    }
    if (metadata.author) {
        items.push(`by ${escapeHtml(metadata.author)}`);
    }
    if (metadata.narrator) {
        items.push(`narrated by ${escapeHtml(metadata.narrator)}`);
    }
    if (metadata.year) {
        items.push(`(${escapeHtml(metadata.year)})`);
    }
    if (metadata.duration) {
        items.push(`<i class="bi bi-clock"></i> ${escapeHtml(metadata.duration)}`);
    }
    if (metadata.size) {
        items.push(`<i class="bi bi-file-earmark"></i> ${escapeHtml(metadata.size)}`);
    }

    return items.length > 0 ? `<div class="task-metadata mb-1"><small class="text-muted">${items.join(' • ')}</small></div>` : '';
}

// Add URL to input field with optional message
function addUrlToInput(url, mediaType = 'audiobook', message = 'URL added to download form') {
    const urlsTextarea = document.getElementById('urls');
    if (!urlsTextarea) return;

    // Add the URL to the textarea
    const currentValue = urlsTextarea.value.trim();
    urlsTextarea.value = currentValue ? currentValue + '\n' + url : url;
    urlSelections.set(url, {
        audiobook: mediaType !== 'ebook',
        ebook: mediaType === 'ebook'
    });
    renderUrlSelections();

    // Scroll to the form
    urlsTextarea.scrollIntoView({ behavior: 'smooth', block: 'center' });

    // Highlight the textarea briefly
    urlsTextarea.classList.add('highlight-input');
    setTimeout(() => urlsTextarea.classList.remove('highlight-input'), 1500);

    showNotification(message, 'info');
}

// Retry a failed download
function retryTask(url, mediaType = 'audiobook') {
    addUrlToInput(url, mediaType, 'URL added with its previous format selected.');
}

// Copy URL to input field
function copyUrlToInput(url, mediaType = 'audiobook') {
    addUrlToInput(url, mediaType, 'URL added to the download form');
}

// Clear completed tasks
async function clearCompleted() {
    try {
        const response = await fetch('/api/tasks/clear', {
            method: 'POST'
        });

        if (!response.ok) {
            throw new Error('Failed to clear tasks');
        }

        showNotification('Cleared completed tasks', 'info');
        await loadTasks();

    } catch (error) {
        console.error('Error clearing tasks:', error);
        showNotification('Failed to clear tasks', 'danger');
    }
}

// Remove individual task
async function removeTask(taskId) {
    try {
        const response = await fetch(`/api/tasks/${taskId}`, {
            method: 'DELETE'
        });

        if (!response.ok) {
            throw new Error('Failed to remove task');
        }

        showNotification('Task removed', 'info');
        await loadTasks();

    } catch (error) {
        console.error('Error removing task:', error);
        showNotification('Failed to remove task', 'danger');
    }
}

// Show notification (temporary alert)
function showNotification(message, type = 'info') {
    const alertDiv = document.createElement('div');
    alertDiv.className = `alert alert-${type} alert-dismissible fade show position-fixed top-0 start-50 translate-middle-x mt-3`;
    alertDiv.style.zIndex = '9999';
    alertDiv.style.minWidth = '300px';
    alertDiv.innerHTML = `
        ${message}
        <button type="button" class="btn-close" data-bs-dismiss="alert"></button>
    `;

    document.body.appendChild(alertDiv);

    // Auto-dismiss after 5 seconds
    setTimeout(() => {
        alertDiv.remove();
    }, 5000);
}

// Start polling for task updates
function startPolling() {
    if (updateInterval) {
        clearInterval(updateInterval);
    }

    // Poll every 2 seconds
    updateInterval = setInterval(async () => {
        await loadTasks();
    }, 2000);
}

// Adjust polling interval based on active tasks
function adjustPolling(activeTaskCount, tasks = []) {
    // Check if any completed tasks are missing metadata
    const completedWithoutMetadata = tasks.some(task =>
        task.status === 'completed' && task.media_type !== 'ebook' && !task.metadata
    );

    // Stop polling if:
    // 1. No active tasks AND no missing metadata, OR
    // 2. We've been waiting for metadata for more than 30 seconds
    const metadataTimeout = metadataWaitStart && (Date.now() - metadataWaitStart > 30000);

    if (metadataTimeout) {
        console.log('Metadata extraction timeout reached, stopping polling');
        metadataWaitStart = null;
    }

    if (activeTaskCount === 0 && (!completedWithoutMetadata || metadataTimeout)) {
        // Stop polling when no active tasks and all completed tasks have metadata (or timeout)
        stopPolling();
        metadataWaitStart = null;
    } else if (!updateInterval) {
        // Resume polling if we have active tasks but polling stopped
        startPolling();
    }
}

// Stop polling (cleanup)
function stopPolling() {
    if (updateInterval) {
        clearInterval(updateInterval);
        updateInterval = null;
    }
}

// Format timestamp display
function formatTimestamp(task) {
    if (!task.started_at) return '';

    const started = new Date(task.started_at);
    let html = `<small class="text-muted">Started: ${started.toLocaleString()}</small>`;

    if (task.completed_at) {
        const completed = new Date(task.completed_at);
        const duration = Math.round((completed - started) / 1000);
        html += ` <small class="text-muted">| Duration: ${formatDuration(duration)}</small>`;
    }

    return `<div class="mt-2">${html}</div>`;
}

// Format duration in human-readable format
function formatDuration(seconds) {
    if (seconds < 60) return `${seconds}s`;

    const minutes = Math.floor(seconds / 60);
    const remainingSeconds = seconds % 60;

    if (minutes < 60) return `${minutes}m ${remainingSeconds}s`;

    const hours = Math.floor(minutes / 60);
    const remainingMinutes = minutes % 60;
    return `${hours}h ${remainingMinutes}m`;
}

// Truncate URL for display
function truncateUrl(url, maxLength) {
    if (url.length <= maxLength) return url;
    return url.substring(0, maxLength - 3) + '...';
}

// Escape HTML to prevent XSS
function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// Cleanup on page unload
window.addEventListener('beforeunload', function () {
    stopPolling();
});
