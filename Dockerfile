FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml README.md LICENSE ./
COPY app ./app
COPY config ./config
COPY docs ./docs
COPY worker.py ./

RUN pip install --no-cache-dir --upgrade pip && pip install --no-cache-dir -e .

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
