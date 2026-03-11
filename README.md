# Turnstile

One API, one scarce GPU, many tools.

Turnstile Milestone 1 is a minimal runnable scaffold for a resource-aware API
broker. It includes:

- FastAPI app with typed endpoints
- Celery worker wired to a dedicated `gpu` queue
- Redis-backed broker/result backend configuration
- Flower in `docker-compose`
- in-memory service registry and job metadata store
- Docker SDK runtime abstraction scaffold, without real container launching

## Local Development

### 1. Create an environment

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
cp .env.example .env
```

### 2. Run with Docker Compose

```bash
docker compose up --build
```

### 3. Run components manually

Terminal 1:

```bash
make dev
```

Terminal 2:

```bash
make worker
```

Terminal 3:

```bash
make flower
```

## Local Endpoints

- API: `http://localhost:8000`
- OpenAPI docs: `http://localhost:8000/docs`
- ReDoc: `http://localhost:8000/redoc`
- Health: `GET http://localhost:8000/healthz`
- Services: `GET http://localhost:8000/v1/services`
- Job status: `GET http://localhost:8000/v1/jobs/{job_id}`
- Submit image generation: `POST http://localhost:8000/v1/image/generate`
- Ops runtime snapshot: `GET http://localhost:8000/ops/runtime`
- Flower: `http://localhost:5555`

## Example Request

```bash
curl -X POST http://localhost:8000/v1/image/generate \
  -H 'Content-Type: application/json' \
  -d '{"prompt":"studio portrait"}'
```

Expected response:

```json
{
  "job_id": "c7c7fe5f-7bf9-45e0-850f-f0935b334fe8",
  "status": "queued"
}
```

Then query:

```bash
curl http://localhost:8000/v1/jobs/c7c7fe5f-7bf9-45e0-850f-f0935b334fe8
```

## Commands

- `make dev`
- `make worker`
- `make flower`
- `make test`
- `make lint`
- `make format`
- `make typecheck`
