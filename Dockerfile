FROM python:3.11-slim

# FFmpeg for audio, curl+unzip for Deno, ca-certificates for HTTPS
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg curl unzip ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Deno — required by yt-dlp to solve YouTube's JS n-challenge on datacenter IPs
# (cookies handle auth; Deno handles the JavaScript obfuscation separately)
RUN curl -fsSL https://deno.land/install.sh | DENO_INSTALL=/usr/local sh
ENV DENO_DIR=/tmp/deno
ENV PATH="/usr/local/bin:${PATH}"

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Always upgrade yt-dlp + EJS solver to latest on every deploy
RUN pip install --no-cache-dir --upgrade "yt-dlp[default]" yt-dlp-ejs

# Expose web dashboard port
EXPOSE 8000
ENV PORT=8000
ENV HOST=0.0.0.0

CMD ["python", "main.py"]
