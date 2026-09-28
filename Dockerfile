FROM python:3.14-slim

LABEL org.opencontainers.image.title="Quantary" \
      org.opencontainers.image.description="Evidence-grounded AI quantitative factor research workspace" \
      org.opencontainers.image.licenses="Apache-2.0"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /workspace

COPY pyproject.toml README.md ./
COPY app ./app
RUN python -m pip install .

RUN addgroup --system agent && adduser --system --ingroup agent agent \
    && mkdir -p data artifacts workspace \
    && chown -R agent:agent /workspace

USER agent

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=3)"]

CMD ["quant-factor-web", "--host", "0.0.0.0", "--port", "8000"]
