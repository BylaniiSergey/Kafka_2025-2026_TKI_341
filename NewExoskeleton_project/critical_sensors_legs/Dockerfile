FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY critical_sensors_legs/ .
EXPOSE 7102
CMD ["python", "main.py"]
