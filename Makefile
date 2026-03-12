.PHONY: dev worker flower test lint format typecheck

dev:
	uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

worker:
	celery -A worker:celery_app worker -Q gpu,cpu --concurrency=1 --loglevel=info

worker-gpu:
	celery -A worker:celery_app worker -Q gpu --concurrency=1 --loglevel=info

worker-cpu:
	celery -A worker:celery_app worker -Q cpu --concurrency=1 --loglevel=info

flower:
	celery -A worker:celery_app flower --port=5555

test:
	pytest

lint:
	ruff check .

format:
	ruff format .

typecheck:
	mypy app tests worker.py
