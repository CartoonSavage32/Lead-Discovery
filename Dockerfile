FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    DATA_DIR=/app/data \
    CONFIG_PATH=/app/config.yaml

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && playwright install --with-deps chromium

COPY app ./app
COPY config.yaml .
COPY data/countries.yaml data/cities.yaml data/industries.yaml ./data/

VOLUME ["/app/data"]

CMD ["python", "-m", "app", "run"]
