FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY pyproject.toml README.md ./
COPY exoskeleton ./exoskeleton/

RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir ".[api]"

EXPOSE 8000

CMD ["uvicorn", "exoskeleton.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
