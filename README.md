# Turnstile

Turnstile is a typed capability broker for a single server with scarce local compute. Public endpoints are defined by capability YAML, backend implementations are defined by service YAML, and adapters handle the execution path for warm HTTP backends or ephemeral command containers.

Turnstile is not a generic passthrough proxy, a Kubernetes control plane, or a system for arbitrary user-supplied containers.

## Architecture

- `config/capabilities/*.yaml` defines the public API contract: HTTP method, path, request schema, response schema, execution mode, queue lane, adapter type, and default service.
- `config/services/*.yaml` defines backend implementations: container image, warm vs ephemeral mode, health behavior, cancellation strategy, and adapter-specific config.
- `config/requests/*.json` and `config/responses/*.json` define the typed request/response shapes that feed OpenAPI.
- `http_forward_json` manages warm HTTP backends and forwards normalized JSON requests to them.
- `container_command` runs one-shot containers and exchanges input/output through Docker archive copy APIs. This avoids bind-mount path mismatches when Turnstile talks to a host Docker daemon through `/var/run/docker.sock`.
- Redis stores transient job state and GPU arbitration state.
- Celery executes async work on the `gpu` and `cpu` lanes.
- Flower exposes worker/task visibility.

## Runtime Modes

- `TURNSTILE_RUNTIME_MODE=stub`: deterministic local/test mode with no Docker calls.
- `TURNSTILE_RUNTIME_MODE=docker`: real Docker mode for warm services and ephemeral jobs.

## API Surface

Static routes:

- `GET /healthz`
- `GET /v1/services`
- `GET /v1/jobs/{job_id}`
- `POST /v1/jobs/{job_id}/cancel`
- `GET /ops/runtime`
- `GET /ops/jobs`
- `GET /ops/services`
- `GET /ops/capabilities`
- `GET /ops/queues`

YAML-defined example capability routes shipped in this repo:

- `POST /v1/image/generate`
- `POST /v1/audio/transcribe`

OpenAPI reflects both the static routes and all loaded capability routes at `GET /openapi.json`.

## Execution Modes

- `async` capabilities enqueue a Celery job and return `202 Accepted` with a job record.
- `sync` capabilities run inline and return the final normalized response directly.
- The shipped example capabilities are both `async`; `sync` support exists for future config-defined capabilities.

## Single-Server Deployment

Host prerequisites:

- Docker Engine running on the target host
- Redis reachable by the API and workers
- If GPU-backed service containers are used: NVIDIA drivers, NVIDIA Container Toolkit, and Docker GPU support on the host
- Docker access for Turnstile through either:
  - `/var/run/docker.sock` mounted into the API and worker containers, or
  - `DOCKER_HOST` / `TURNSTILE_DOCKER_HOST` pointing at a reachable daemon

Start the full stack:

```bash
cp .env.example .env
docker compose up --build
```

The shipped Compose file starts:

- `api` on [http://localhost:8000](http://localhost:8000)
- `worker-gpu` subscribed to `gpu`
- `worker-cpu` subscribed to `cpu`
- `flower` on [http://localhost:5555](http://localhost:5555)
- `redis` on `localhost:6379`

Compose declares an explicit `turnstile` bridge network. Managed warm containers join that same network and are addressed by their generated container name from within the control plane.

## Manual Run

```bash
conda activate turnstile_env
pip install -e '.[dev]'
cp .env.example .env
make dev
make worker-gpu
make worker-cpu
make flower
```

If you want one combined worker locally:

```bash
make worker
```

## Docker Behavior

Warm HTTP services:

- If `TURNSTILE_DOCKER_NETWORK` is set, Turnstile starts warm containers on that network and reaches them by generated container name.
- If `TURNSTILE_DOCKER_NETWORK` is empty, Turnstile publishes the backend port and reaches it through `TURNSTILE_DOCKER_SERVICE_HOST`.

Ephemeral command services:

- Turnstile injects `TURNSTILE_JOB_ID`, `TURNSTILE_REQUEST_JSON`, `TURNSTILE_REQUEST_FILE`, and `TURNSTILE_OUTPUT_DIR`.
- Turnstile uploads `/turnstile/input/request.json` into the ephemeral container before start.
- After completion, Turnstile downloads `/turnstile/output` back out of the container and records artifacts from that extracted output.

More deployment notes live in [docs/deployment.md](docs/deployment.md).

## Adding a Capability

1. Add a request schema in `config/requests/`.
2. Add a response schema in `config/responses/`.
3. Add a capability definition in `config/capabilities/`.
4. Set `execution_mode`, `queue_lane`, `adapter_type`, and `default_service_selection`.
5. Restart the API and confirm the route appears in `GET /openapi.json` and `GET /ops/capabilities`.

Shipped capability examples:

- `config/capabilities/image_generate.yaml`
- `config/capabilities/audio_transcribe.yaml`

Warm HTTP capability/service example:

```yaml
# config/capabilities/text_summarize.yaml
capability_id: text.summarize
method: POST
path: /text/summarize
summary: Summarize text
request_schema: requests/text_summarize.request.json
response_schema: responses/job_accepted.response.json
execution_mode: async
queue_lane: cpu
adapter_type: http_forward_json
default_service_selection: summarizer-service
```

```yaml
# config/services/summarizer_service.yaml
service_id: summarizer-service
capabilities:
  - text.summarize
mode: warm
image: ghcr.io/example/summarizer:latest
gpu_required: false
estimated_vram_mb: 0
startup_timeout_s: 30
idle_ttl_s: 300
healthcheck:
  type: http
  path: /healthz
adapter_type: http_forward_json
adapter_config:
  container_port: 8080
  path: /summaries
  method: POST
```

Ephemeral command capability/service example:

```yaml
# config/capabilities/audio_detect_language.yaml
capability_id: audio.detect_language
method: POST
path: /audio/detect-language
summary: Detect spoken language
request_schema: requests/audio_detect_language.request.json
response_schema: responses/job_accepted.response.json
execution_mode: async
queue_lane: cpu
adapter_type: container_command
default_service_selection: language-detector
```

```yaml
# config/services/language_detector.yaml
service_id: language-detector
capabilities:
  - audio.detect_language
mode: ephemeral
image: python:3.12-slim
gpu_required: false
estimated_vram_mb: 0
startup_timeout_s: 30
idle_ttl_s: 300
healthcheck:
  type: none
adapter_type: container_command
adapter_config:
  command:
    - python
    - -c
    - |
      import json, os
      from pathlib import Path
      request = json.loads(Path(os.environ["TURNSTILE_REQUEST_FILE"]).read_text())
      Path(os.environ["TURNSTILE_OUTPUT_DIR"]).mkdir(parents=True, exist_ok=True)
      print(json.dumps({"language": request["hint"]}))
```

## Examples

Submit a warm HTTP-backed image job:

```bash
curl -X POST http://localhost:8000/v1/image/generate \
  -H 'Content-Type: application/json' \
  -d '{"prompt":"studio portrait","style":"cinematic"}'
```

Submit an ephemeral command-backed audio job:

```bash
curl -X POST http://localhost:8000/v1/audio/transcribe \
  -H 'Content-Type: application/json' \
  -d '{"audio_url":"https://example.com/clip.wav","language":"en"}'
```

Inspect loaded capabilities:

```bash
curl http://localhost:8000/ops/capabilities
```

Cancel a job:

```bash
curl -X POST http://localhost:8000/v1/jobs/<job_id>/cancel
```

## Troubleshooting

- `ModuleNotFoundError: No module named 'httpx'`:
  - Rebuild the image after updating runtime dependencies. The production image installs `pip install -e .`, not `.[dev]`.
- `celery ... flower` fails with `No such command 'flower'`:
  - Rebuild the image after adding the `flower` runtime dependency.
- `/healthz` says Docker is unreachable:
  - Check the socket mount or `TURNSTILE_DOCKER_HOST`.
- Ephemeral command jobs cannot see uploaded files or produced artifacts:
  - Confirm the worker can reach the Docker daemon. Turnstile now uses Docker archive copy APIs, so child containers no longer depend on worker-container temp paths being host-visible.
- Warm services are unreachable in Compose:
  - Confirm `TURNSTILE_DOCKER_NETWORK=turnstile` and that the worker/api containers are attached to the same explicit `turnstile` network.
- Flower shows no workers:
  - Confirm the workers start with `celery -A worker:celery_app ...` and that Redis is reachable.

## Verification

```bash
make test
make lint
make typecheck
```
