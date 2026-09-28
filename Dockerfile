FROM python:3.11-slim

WORKDIR /app

# Build deps for lxml and other C extensions
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libxml2-dev \
    libxslt-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Run as an unprivileged user; data/, logs/ and config/ are its to write
RUN useradd --create-home --uid 1000 app \
    && mkdir -p data logs config \
    && chown -R app:app /app
USER app

CMD ["python", "main.py", "run"]
