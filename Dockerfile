FROM python:3.14-slim

# Install system dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        ffmpeg \
        rsync \
        ca-certificates \
        curl \
        tar \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy project files and application code
COPY pyproject.toml ./
COPY app/ ./app/

# Install application dependencies
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir .

# Install latest audiobook-dl from GitHub master
RUN curl -L \
        https://github.com/jo1gi/audiobook-dl/archive/refs/heads/master.tar.gz \
        -o /tmp/audiobook-dl.tar.gz && \
    mkdir -p /tmp/audiobook-dl-src && \
    tar -xzf /tmp/audiobook-dl.tar.gz \
        -C /tmp/audiobook-dl-src \
        --strip-components=1 \
        --no-same-owner \
        --no-same-permissions && \
    pip install --no-cache-dir --no-deps \
        /tmp/audiobook-dl-src && \
    cp -a \
        /tmp/audiobook-dl-src/audiobookdl/assets \
        /usr/local/lib/python3.14/site-packages/audiobookdl/ && \
    rm -rf /tmp/audiobook-dl.tar.gz /tmp/audiobook-dl-src

# audiobook-dl is installed with --no-deps; Nextory still needs dateutil.
RUN pip install --no-cache-dir python-dateutil

# Install the patched Grawlix fork for e-book downloads. The released PyPI
# package is retained as a normal dependency for non-Docker installations.
RUN curl -L \
        https://github.com/technoluc/grawlix/archive/refs/heads/master.tar.gz \
        -o /tmp/grawlix.tar.gz && \
    mkdir -p /tmp/grawlix-src && \
    tar -xzf /tmp/grawlix.tar.gz \
        -C /tmp/grawlix-src \
        --strip-components=1 \
        --no-same-owner \
        --no-same-permissions && \
    pip install --no-cache-dir --no-deps --force-reinstall \
        /tmp/grawlix-src && \
    cp -a \
        /tmp/grawlix-src/grawlix/assets \
        /usr/local/lib/python3.14/site-packages/grawlix/ && \
    rm -rf /tmp/grawlix.tar.gz /tmp/grawlix-src

# Create directories for volumes
RUN mkdir -p /app/config /app/downloads /app/logs

# Set environment variables
ENV CONFIG_DIR=/app/config
ENV DOWNLOADS_DIR=/app/downloads
ENV HOST=0.0.0.0
ENV PORT=8000
ENV DEBUG=false

# Expose port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"

# Run the application
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
