# API + dashboard image. Build from the repository root (docker-compose.yml does this).
FROM python:3.12-slim

WORKDIR /app
COPY twin_iot/requirements.txt twin_iot/requirements.txt
RUN pip install --no-cache-dir -r twin_iot/requirements.txt

COPY twin_iot twin_iot
COPY dashboard dashboard

WORKDIR /app/twin_iot
ENV TWIN_DATABASE_URL=postgresql://twin@db:5432/twin_iot \
    PYTHONUNBUFFERED=1
EXPOSE 8020
CMD ["python", "-m", "uvicorn", "app.api.main:app", "--host", "0.0.0.0", "--port", "8020"]
