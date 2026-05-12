FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY leg_force_limits_system/ .
EXPOSE 9105
CMD ["python", "main.py"]
