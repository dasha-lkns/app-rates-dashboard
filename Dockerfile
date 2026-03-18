# Dashboard web server — lightweight image for Cloud Run service
FROM python:3.11-slim

WORKDIR /app

# Install only what the web server needs
RUN pip install --no-cache-dir google-cloud-storage

COPY setapp_monitor/ ./setapp_monitor/
COPY docs/ ./docs/

EXPOSE 8080
ENV PORT=8080

CMD ["python", "-m", "setapp_monitor.main", "--serve"]
