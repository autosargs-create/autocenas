FROM python:3.11-slim-bookworm

# Instalējam chromium un curl datu nolasīšanai (IC24 un Dipex)
RUN apt-get update && apt-get install -y --no-install-recommends \
    chromium \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY . /app

EXPOSE 7777

CMD ["python3", "server.py"]
