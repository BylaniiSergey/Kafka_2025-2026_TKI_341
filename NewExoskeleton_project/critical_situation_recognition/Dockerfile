FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY kafka_bus.py .
COPY critical_situation_recognition/ .

CMD ["python", "main.py"]
