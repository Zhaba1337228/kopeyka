FROM node:22-slim@sha256:e9f565d32859f0cbfb52125eafe8be39390f24dac302dc3cdf566e8ba3ab21ee AS frontend
WORKDIR /frontend
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.11-slim-bookworm@sha256:a83ba18aef0f865b1a4a611c2cf460fe446f2795673bbb9fe43be570439e3b39

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    STREAMLIT_BROWSER_GATHER_USAGE_STATS=false \
    OMP_THREAD_LIMIT=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr tesseract-ocr-rus tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt ./
COPY requirements.lock ./
RUN pip install uv==0.9.9 \
    && uv pip install --system --no-cache -r requirements.lock \
    && pip uninstall -y uv
COPY kfz_core/ ./kfz_core/
COPY config/ ./config/
COPY ui/ ./ui/
COPY .streamlit/ ./.streamlit/
COPY app.py backend.py README.md ./
COPY --from=frontend /frontend/dist ./frontend/dist
RUN useradd --create-home --uid 10001 appuser && mkdir -p /app/runtime && chown -R appuser:appuser /app
USER appuser
EXPOSE 8501
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8501/')"
CMD ["uvicorn", "backend:app", "--host", "0.0.0.0", "--port", "8501"]
