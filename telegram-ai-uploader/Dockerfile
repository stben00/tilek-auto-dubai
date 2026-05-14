FROM python:3.13-slim

# System deps: ffmpeg for video poster extraction
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Cache pip layer
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir -r requirements.txt

# Source
COPY . .

# Temp dir for downloaded media before publishing
RUN mkdir -p /app/data/temp

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Bot uses Telegram long-polling — no HTTP port needs to be exposed.
CMD ["python", "bot.py"]
