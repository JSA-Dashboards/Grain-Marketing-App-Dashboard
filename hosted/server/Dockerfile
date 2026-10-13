# Built from the repository root (see hosted/docker-compose.yml) so the image carries the same
# grain-desk.html the Claude version runs.
FROM python:3.12-slim
WORKDIR /app
COPY hosted/server/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY hosted/server/app ./app
COPY hosted/server/web ./web
COPY grain-desk.html ./web/grain-desk.html
RUN useradd --create-home --uid 1000 grain && mkdir -p /data && chown grain /data
USER grain
ENV DATA_DIR=/data APP_HTML=/app/web/grain-desk.html PYTHONUNBUFFERED=1
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
