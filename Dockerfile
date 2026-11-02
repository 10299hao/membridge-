FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 MEMBRIDGE_HOME=/tmp/membridge
WORKDIR /app
COPY pyproject.toml requirements.lock ./
COPY membridge ./membridge
RUN pip install --no-cache-dir -c requirements.lock '.[postgres]' \
    && useradd --create-home --uid 10001 membridge
USER membridge
EXPOSE 8000
CMD ["membridge", "serve", "gateway", "--host", "0.0.0.0", "--port", "8000"]
