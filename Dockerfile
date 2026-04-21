FROM python:3.11-slim

RUN apt-get update && apt-get install -y \
    ffmpeg git && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

RUN python -c "from demucs.pretrained import get_model; get_model('htdemucs')"

COPY bot.py .

CMD ["python", "bot.py"]