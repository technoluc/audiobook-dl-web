FROM python:3.14-slim

# Install system dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        ffmpeg \
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

RUN pip install --no-cache-dir python-dateutil

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
