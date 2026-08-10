FROM python:3.11-slim

# Install ffmpeg for video/audio processing and clean up cache
RUN apt-get update && \
    apt-get install -y ffmpeg && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .

# Run the bot
CMD ["python", "main.py"]
