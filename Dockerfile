FROM python:3.11-slim

LABEL maintainer="w7panel"
LABEL description="Userspace Memory Reclaimer Daemon"

ARG VERSION=2.0.0
ARG BUILD_DATE

RUN apt-get update && apt-get install -y --no-install-recommends \
    kmod \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY dist/umrd-*.whl /app/
RUN pip install --no-cache-dir umrd-*.whl

RUN mkdir -p /run/umrd

COPY service/umrd.service /etc/systemd/system/ 2>/dev/null || true

ENV PYTHONUNBUFFERED=1

ENTRYPOINT ["python3", "-m", "umrd"]
CMD ["--help"]
