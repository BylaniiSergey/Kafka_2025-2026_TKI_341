FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY kafka_bus.py .
COPY temperature_measurement_system/ .

EXPOSE 7105

CMD ["python", "main.py"]
