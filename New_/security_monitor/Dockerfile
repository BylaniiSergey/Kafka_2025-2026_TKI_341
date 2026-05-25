FROM python:3.11-slim
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Размещаем код как пакет security_monitor, чтобы заработал `python -m`
RUN mkdir -p /app/security_monitor
COPY __init__.py /app/security_monitor/__init__.py
COPY __main__.py /app/security_monitor/__main__.py
COPY policies.py /app/security_monitor/policies.py
COPY consumer.py /app/security_monitor/consumer.py
COPY producer.py /app/security_monitor/producer.py

ENV PYTHONUNBUFFERED=1
ENV MODULE_NAME=security_monitor

CMD ["python", "-m", "security_monitor"]
