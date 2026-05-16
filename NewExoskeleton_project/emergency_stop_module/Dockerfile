FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY emergency_stop_module/ .

CMD ["python", "main.py"]
