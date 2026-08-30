FROM python:3.11-slim

# Install FFmpeg, curl (for Deno), and ca-certificates
RUN apt-get update && apt-get install -y ffmpeg curl unzip ca-certificates && rm -rf /var/lib/apt/lists/*

# Install Deno — yt-dlp's required JS runtime for YouTube signature deciphering
RUN curl -fsSL https://deno.land/install.sh | DENO_INSTALL=/usr/local sh
ENV DENO_DIR=/tmp/deno
ENV PATH="/usr/local/bin:${PATH}"

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# CRITICAL: Always upgrade yt-dlp with EJS solver to latest on every deploy
RUN pip install --no-cache-dir --upgrade "yt-dlp[default]" yt-dlp-ejs

# Expose web dashboard port
EXPOSE 8000
ENV PORT=8000
ENV HOST=0.0.0.0

CMD ["python", "main.py"]
