FROM python:3.11-slim

# Install FFmpeg, curl (for Deno), and ca-certificates
RUN apt-get update && apt-get install -y ffmpeg curl unzip ca-certificates && rm -rf /var/lib/apt/lists/*

# Install Deno — yt-dlp's required JS runtime for YouTube signature deciphering
RUN curl -fsSL https://deno.land/install.sh | DENO_INSTALL=/usr/local sh
ENV DENO_DIR=/tmp/deno

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# CRITICAL: Always upgrade yt-dlp to absolute latest on every deploy.
# This step runs AFTER "COPY . ." so Docker cache never masks it.
# YouTube changes its API frequently; stale yt-dlp = broken extraction.
RUN pip install --no-cache-dir --upgrade yt-dlp

# Expose web dashboard port
EXPOSE 8000
ENV PORT=8000
ENV HOST=0.0.0.0

CMD ["python", "main.py"]
