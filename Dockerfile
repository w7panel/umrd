FROM python:3.11-slim

LABEL maintainer="w7panel"
LABEL description="Userspace Memory Reclaimer Daemon"

RUN apt-get update && apt-get install -y --no-install-recommends \
    kmod \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY dist/umrd-*.whl /app/
RUN pip install --no-cache-dir umrd-*.whl

RUN mkdir -p /run/umrd

ENV PYTHONUNBUFFERED=1

ENTRYPOINT ["python3", "-m", "umrd"]
CMD ["--help"]
