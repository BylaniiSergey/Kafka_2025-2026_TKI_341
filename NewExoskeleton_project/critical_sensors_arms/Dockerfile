FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY critical_sensors_arms/ .
EXPOSE 7101
CMD ["python", "main.py"]
