FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    wget \
    gnupg \
    && rm -rf /var/lib/apt/lists/*

# Install ngrok (required for OpenAI webhooks - they require HTTPS)
RUN wget https://bin.equinox.io/c/bNyj1mQVY4c/ngrok-v3-stable-linux-amd64.tgz && \
    tar -xvzf ngrok-v3-stable-linux-amd64.tgz && \
    mv ngrok /usr/local/bin/ && \
    rm ngrok-v3-stable-linux-amd64.tgz

# Install Playwright dependencies (required for PDF generation)
RUN apt-get update && apt-get install -y \
    libnss3 \
    libnspr4 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libxkbcommon0 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libasound2 \
    && rm -rf /var/lib/apt/lists/*

# Copy application code
COPY pyproject.toml README.md ./
COPY src/ ./src/
COPY merge_new_orgs.py retrieve_response.py ./

# Install Python dependencies (editable mode so package points to source dir)
RUN pip install --no-cache-dir -e .

# Install Playwright browsers
RUN playwright install chromium

WORKDIR /app/src/neighbor

# Set environment for Python
ENV PYTHONUNBUFFERED=1

ENTRYPOINT ["python", "test_live_regrid.py"]
CMD ["--lat", "43.081409", "--lon", "-79.029438"]
