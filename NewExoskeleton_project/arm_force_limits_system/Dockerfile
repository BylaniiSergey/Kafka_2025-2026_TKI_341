FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY arm_force_limits_system/ .
EXPOSE 7106
CMD ["python", "main.py"]
