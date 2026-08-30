FROM python:3.11-slim

# Install FFmpeg for audio processing and curl for Deno install
RUN apt-get update && apt-get install -y ffmpeg curl unzip && rm -rf /var/lib/apt/lists/*

# Install Deno — yt-dlp's preferred JS runtime for YouTube signature deciphering
RUN curl -fsSL https://deno.land/install.sh | DENO_INSTALL=/usr/local sh
ENV DENO_DIR=/tmp/deno

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Expose web dashboard port
EXPOSE 8000
ENV PORT=8000
ENV HOST=0.0.0.0

CMD ["python", "main.py"]
