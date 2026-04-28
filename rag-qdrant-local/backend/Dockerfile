FROM python:3.11-slim

# LibreOffice is optional but required for legacy .doc / .xls
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libreoffice \
        curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY app /app/app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    SQLITE_DB_PATH=/app/storage/rag.sqlite

RUN mkdir -p /app/storage/converted /app/storage/temp

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
