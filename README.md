# Turnstile

One API, one scarce GPU, many tools.

Turnstile is a typed capability broker for scarce local compute. Public
endpoints are registered from YAML capability definitions, validated by JSON
Schema, and dispatched through adapter types rather than hardcoded one-off
routes or a generic passthrough proxy.

## How It Works

- Capabilities live in `config/capabilities/*.yaml`
- Service definitions live in `config/services/*.yaml`
- Config formats are validated by JSON Schema in `config/schemas/*.json`
- Request and response payloads are backed by JSON Schemas in
  `config/requests/*.json` and `config/responses/*.json`
- FastAPI routes are generated from capability definitions at startup
- Jobs and GPU arbitration state live in Redis with TTLs
- Celery executes async jobs on named lanes like `gpu` and `cpu`
- Backend invocation is selected by adapter type:
  - `noop_stub`
  - `http_forward_json`
  - `container_command`

## Current Endpoints

- `GET /healthz`
- `GET /v1/services`
- `GET /v1/jobs/{job_id}`
- `POST /v1/jobs/{job_id}/cancel`
- `POST /v1/image/generate`
- `POST /v1/audio/transcribe`
- `GET /ops/runtime`
- `GET /docs`
- `GET /redoc`
- `GET /openapi.json`

## Local Development

```bash
conda activate turnstile_env
pip install -e '.[dev]'
cp .env.example .env
```

Run everything with Docker Compose:

```bash
docker compose up --build
```

Or run components manually:

```bash
make dev
make worker
make flower
```

## Example Requests

Image generation:

```bash
curl -X POST http://localhost:8000/v1/image/generate \
  -H 'Content-Type: application/json' \
  -d '{"prompt":"studio portrait","style":"cinematic"}'
```

Audio transcription:

```bash
curl -X POST http://localhost:8000/v1/audio/transcribe \
  -H 'Content-Type: application/json' \
  -d '{"audio_url":"https://example.com/clip.wav","language":"en"}'
```

Cancel a queued job:

```bash
curl -X POST http://localhost:8000/v1/jobs/<job_id>/cancel
```

## Adding a Capability

1. Add a request schema in `config/requests/`
2. Add or reuse a response schema in `config/responses/`
3. Add a capability definition in `config/capabilities/`
4. Point it at an adapter type and default service
5. Add or update a service entry in `config/services/`
6. Restart the API and verify the route appears in `/openapi.json`

## Adding a Service

Each service definition declares:

- supported `capabilities`
- `mode` (`warm` or `ephemeral`)
- `adapter_type`
- `adapter_config`
- GPU and lifecycle metadata

The API surface stays stable; only the capability and service definitions
change.

## Commands

- `make dev`
- `make worker`
- `make flower`
- `make test`
- `make lint`
- `make format`
- `make typecheck`
