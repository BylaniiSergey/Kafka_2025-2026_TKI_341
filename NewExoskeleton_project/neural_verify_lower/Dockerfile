FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY neural_verify_lower/ .
EXPOSE 7104
CMD ["python", "main.py"]
