FROM python:3.12-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

COPY pyproject.toml README.md LICENSE ./
COPY app ./app
COPY config ./config
COPY docs ./docs
COPY worker.py ./

RUN python -m pip install --upgrade pip && python -m pip install -e .

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
