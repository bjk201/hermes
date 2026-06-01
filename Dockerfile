FROM python:3.12-slim AS codestage

# Download code via wget (no git needed)
WORKDIR /tmp/code
ARG REPO=https://github.com/bjk201/hermes.git
ARG BRANCH=feature/pv-rechner

RUN apt-get update && apt-get install -y --no-install-recommends wget unzip && \
    wget -q "https://codeload.github.com/bjk201/hermes/zip/refs/heads/feature/pv-rechner" -O code.zip && \
    unzip -q code.zip && \
    mv hermes-feature-pv-rechner/* . && \
    rm -rf hermes-feature-pv-rechner code.zip && \
    rm -rf /var/lib/apt/lists/*

# ── Final Stage ──
FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev gcc \
    && rm -rf /var/lib/apt/lists/*

# Copy code from download stage
COPY --from=codestage /tmp/code/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY --from=codestage /tmp/code/app/ ./app/

WORKDIR /app/app

EXPOSE 5000

CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "2", "main:app"]
