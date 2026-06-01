FROM python:3.12-slim AS codestage

WORKDIR /tmp/code
RUN apt-get update && apt-get install -y --no-install-recommends wget unzip && \
    wget -q "https://codeload.github.com/bjk201/hermes/zip/refs/heads/feature/pv-rechner" -O code.zip && \
    unzip -q code.zip && \
    mv hermes-feature-pv-rechner/* . && \
    rm -rf hermes-feature-pv-rechner code.zip && \
    rm -rf /var/lib/apt/lists/*

FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev gcc \
    && rm -rf /var/lib/apt/lists/*

COPY --from=codestage /tmp/code/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY --from=codestage /tmp/code/app/ ./app/

WORKDIR /app/app

EXPOSE 5000

# Use gunicorn with preload to create DB connections before forking workers
# --preload: load app code before forking worker processes (fixes SQLAlchemy pool issue)
# --workers 2: sufficient for personal use
# --timeout 120: allow long-running imports
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "2", "--preload", "--timeout", "120", "main:app"]
