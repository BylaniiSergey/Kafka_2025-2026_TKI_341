FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY kafka_bus.py .
COPY control_system/ .

EXPOSE 8000

CMD ["python", "main.py"]
