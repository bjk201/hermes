#!/bin/bash
# Erstelle ein ZIP des aktuellen Stands für Portainer-Deployment
cd /tmp/hermes
rm -f /tmp/pv-rechner.zip
zip -r /tmp/pv-rechner.zip \
    docker-compose.yml \
    Dockerfile \
    requirements.txt \
    .env.example \
    app/ \
    deploy.sh \
    -x "app/__pycache__/*" \
    -x "app/*.pyc" \
    -x ".git/*" \
    -x "*.db"
echo "ZIP erstellt: /tmp/pv-rechner.zip"
ls -lh /tmp/pv-rechner.zip
