# AGENTS.md

## Repo Layout

- `app/main.py`: FastAPI app factory and route registration
- `app/api/`: HTTP routes
- `app/core/`: settings and Celery wiring
- `app/models/`: typed API and domain models
- `app/services/`: in-memory registry, job store, runtime abstraction
- `worker.py`: Celery worker entrypoint
- `tests/`: API tests

## Local Commands

- `python3 -m venv .venv && source .venv/bin/activate`
- `pip install -e '.[dev]'`
- `make dev`
- `make worker`
- `make flower`
- `make test`
- `make lint`
- `make typecheck`

## Constraints

- Keep the public API typed and capability-specific.
- Do not add auth, database persistence, Kubernetes, or generic passthrough proxying.
- Keep service registry and job metadata in memory for Milestone 1.
- Docker integration stays behind abstractions; no real container launching yet.

## Done Criteria

- FastAPI app, Celery worker, Redis, and Flower are wired.
- `POST /v1/image/generate` enqueues onto the `gpu` queue and job status is observable.
- Tests pass locally.
- README documents exact startup commands and expected endpoints.

## Environment
- Use the local mamba environment `turnstile_env`
- Never install Python packages globally
- Python setup for worktrees is handled by the Local Environment setup script
- Run tests before finishing
