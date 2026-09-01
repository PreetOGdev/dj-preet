FROM python:3.11-slim

# Install FFmpeg and ca-certificates (needed for HTTPS)
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg ca-certificates && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Always upgrade yt-dlp to latest on every deploy to stay ahead of YouTube changes
RUN pip install --no-cache-dir --upgrade "yt-dlp[default]"

# Expose web dashboard port
EXPOSE 8000
ENV PORT=8000
ENV HOST=0.0.0.0

CMD ["python", "main.py"]
