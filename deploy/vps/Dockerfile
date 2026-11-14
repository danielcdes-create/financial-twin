FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates tzdata \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 appuser \
    && useradd --uid 10001 --gid 10001 --create-home --shell /usr/sbin/nologin appuser

WORKDIR /app
COPY backend/requirements.txt backend/requirements.lock.txt /app/backend/
RUN pip install --no-cache-dir -r backend/requirements.txt -c backend/requirements.lock.txt

COPY backend/ /app/backend/
RUN mkdir -p /app/data/media /app/logs /app/apk \
    && chown -R appuser:appuser /app

USER 10001:10001
EXPOSE 8000
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONPATH=/app/backend
ENTRYPOINT ["python", "/app/backend/run_server.py"]
