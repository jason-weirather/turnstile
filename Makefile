.PHONY: dev worker worker-gpu worker-cpu flower test lint format typecheck \
	build-example-backends build-mock-http-tool build-mock-command-tool \
	run-mock-http-alpha run-mock-http-beta test-integration test-gpu-eviction \
	smoke-docker smoke-docker-keepalive

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
	pytest -m "not integration"

test-integration:
	bash scripts/run_integration_tests.sh

test-gpu-eviction:
	bash scripts/run_integration_tests.sh --gpu-eviction-only

lint:
	ruff check .

format:
	ruff format .

typecheck:
	mypy app tests worker.py

build-example-backends: build-mock-http-tool build-mock-command-tool

build-mock-http-tool:
	docker build -t turnstile/mock-http-tool:latest examples/backends/mock_http_tool

build-mock-command-tool:
	docker build -t turnstile/mock-command-tool:latest examples/backends/mock_command_tool

run-mock-http-alpha:
	docker run --rm -p 18080:8000 \
		-e MOCK_INSTANCE_ID=alpha \
		-e MOCK_RESPONSE_PREFIX=alpha: \
		-e MOCK_TAG_COLOR=amber \
		turnstile/mock-http-tool:latest

run-mock-http-beta:
	docker run --rm -p 18081:8000 \
		-e MOCK_INSTANCE_ID=beta \
		-e MOCK_RESPONSE_PREFIX=beta: \
		-e MOCK_TAG_COLOR=blue \
		turnstile/mock-http-tool:latest

smoke-docker:
	bash scripts/smoke_test.sh

smoke-docker-keepalive:
	TURNSTILE_SMOKE_KEEP_RUNNING=1 bash scripts/smoke_test.sh --keep-running
