# AGENTS.md

## Repo Layout

- `app/main.py`: FastAPI app factory and route registration
- `app/api/`: static HTTP routes plus dynamic capability route registration
- `app/core/`: settings and Celery wiring
- `app/models/`: typed API and domain models
- `app/services/`: definition loading, registries, orchestration, runtime abstraction
- `config/capabilities/`: public API contracts loaded into FastAPI routes
- `config/services/`: backend service definitions
- `worker.py`: Celery worker entrypoint
- `tests/`: API, runtime, packaging, and definition tests

## Local Commands

- `python3 -m venv .venv`
- `. .venv/bin/activate`
- `python -m pip install -U pip`
- `python -m pip install -e '.[dev]'`
- `make dev`
- `make worker`
- `make worker-gpu`
- `make worker-cpu`
- `make flower`
- `make test`
- `make lint`
- `make typecheck`

## Constraints

- Keep the public API typed and capability-specific.
- Keep capabilities separate from services: capabilities define public routes, services define implementations.
- Do not add auth, database persistence, Kubernetes, or generic passthrough proxying.
- Keep service definitions config-driven and adapter-specific.
- Docker integration stays behind abstractions, but the Docker runtime path is real and must work in containerized deployment.

## Done Criteria

- FastAPI app, Celery worker, Redis, and Flower are wired.
- Capability routes are loaded from `config/capabilities/*.yaml` and visible in OpenAPI.
- `POST /v1/example/http/echo` and `POST /v1/example/command/run` exercise async routing and job status is observable.
- `GET /ops/capabilities` reflects loaded capabilities.
- Tests pass locally.
- README documents exact startup commands and expected endpoints.

## Environment
- Use the current shell's Python environment or a local `.venv`
- Never install Python packages globally
- Python setup for worktrees is handled by the Local Environment setup script
- Run tests before finishing
