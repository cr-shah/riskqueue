FROM python:3.12.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

RUN groupadd --system --gid 10001 riskqueue \
    && useradd --system --uid 10001 --gid riskqueue --create-home riskqueue

WORKDIR /app
COPY pyproject.toml README.md /tmp/riskqueue-build/
COPY riskqueue /tmp/riskqueue-build/riskqueue
RUN pip install /tmp/riskqueue-build \
    && rm -rf /tmp/riskqueue-build

USER riskqueue

EXPOSE 8000
CMD ["uvicorn", "riskqueue.api.main:app", "--host", "0.0.0.0", "--port", "8000"]

