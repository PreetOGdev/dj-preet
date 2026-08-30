FROM python:3.11-slim

# Install system FFmpeg audio tools and Node.js for YouTube JS signature deciphering
RUN apt-get update && apt-get install -y ffmpeg nodejs && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Expose web dashboard port
EXPOSE 8000
ENV PORT=8000
ENV HOST=0.0.0.0

CMD ["python", "main.py"]
