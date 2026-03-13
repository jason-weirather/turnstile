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

## Docker Smoke Test

The canonical end-to-end Docker verification path lives in [docs/smoke-test.md](docs/smoke-test.md). That document is the primary source of truth for the clean-checkout path, the exact `curl` commands, and the expected job/result shapes.

Quick path from a fresh checkout:

```bash
cp .env.example .env
make smoke-docker
```

If you want the stack left running after the smoke test:

```bash
make smoke-docker-keepalive
```

## API Surface

Static routes:

- `GET /healthz`
- `GET /readyz`
- `GET /v1/services`
- `GET /v1/jobs/{job_id}`
- `POST /v1/jobs/{job_id}/cancel`
- `GET /ops/readiness`
- `GET /ops/runtime`
- `GET /ops/jobs`
- `GET /ops/services`
- `GET /ops/capabilities`
- `GET /ops/queues`
- `POST /ops/queues/{lane}/cancel`

YAML-defined example capability routes shipped in this repo:

- `POST /v1/example/http/echo`
- `POST /v1/example/command/run`

OpenAPI reflects both the static routes and all loaded capability routes at `GET /openapi.json`.

## Execution Modes

- `async` capabilities enqueue a Celery job and return `202 Accepted` with a job record.
- `sync` capabilities run inline and return the final normalized response directly.
- By default, async submissions fail fast with `503 queue_unavailable` unless the target lane is submission-ready.
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
make build-example-backends
docker compose up -d --build
```

The shipped Compose file starts:

- `api` on [http://localhost:8000](http://localhost:8000)
- `worker-gpu` subscribed to `gpu`
- `worker-cpu` subscribed to `cpu`
- `flower` on [http://localhost:5555](http://localhost:5555)
- `redis` on `localhost:6379`

Compose declares an explicit `turnstile` bridge network. Managed warm containers join that same network and are addressed by their generated container name from within the control plane.

Do not submit async jobs unless `GET /readyz` returns success and `GET /ops/readiness` shows the target lane as `submission_ready: true`.

For the exact first-run smoke sequence, use [docs/smoke-test.md](docs/smoke-test.md).

## Manual Run

```bash
conda activate turnstile_env
pip install -e '.[dev]'
cp .env.example .env
make build-example-backends
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
The canonical Docker smoke test lives in [docs/smoke-test.md](docs/smoke-test.md).
Example backend details live in [docs/testing-backends.md](docs/testing-backends.md).

## Example Testing Backends

Turnstile now ships two reusable example backend images inside this repo:

- `turnstile/mock-http-tool:latest`
  - source: `examples/backends/mock_http_tool/`
  - used by `mock-http-alpha` and `mock-http-beta`
- `turnstile/mock-command-tool:latest`
  - source: `examples/backends/mock_command_tool/`
  - used by `mock-command-alpha` and `mock-command-beta`

Build them with:

```bash
make build-example-backends
```

These are normal backend services. They are not special-cased in FastAPI or the adapter layer.

## Generic Examples

Generic capability definitions:

- `config/capabilities/example_http_echo.yaml`
- `config/capabilities/example_command_run.yaml`

Generic service definitions:

- `config/services/mock_http_alpha.yaml`
- `config/services/mock_http_beta.yaml`
- `config/services/mock_command_alpha.yaml`
- `config/services/mock_command_beta.yaml`

The two HTTP services use the same image and differ only by `service_id` plus env:

```yaml
service_id: mock-http-alpha
image: turnstile/mock-http-tool:latest
adapter_config:
  env:
    MOCK_INSTANCE_ID: alpha
    MOCK_RESPONSE_PREFIX: "alpha:"
```

```yaml
service_id: mock-http-beta
image: turnstile/mock-http-tool:latest
adapter_config:
  env:
    MOCK_INSTANCE_ID: beta
    MOCK_RESPONSE_PREFIX: "beta:"
```

That is the intended scaling model. To add another instance, copy a service YAML, keep the same image, and change only `service_id` and env.

## Adding a Capability

1. Add a request schema in `config/requests/`.
2. Add a response schema in `config/responses/`.
3. Add a capability definition in `config/capabilities/`.
4. Set `execution_mode`, `queue_lane`, `adapter_type`, and `default_service_selection`.
5. Restart the API and confirm the route appears in `GET /openapi.json` and `GET /ops/capabilities`.

Shipped capability examples:

- `config/capabilities/example_http_echo.yaml`
- `config/capabilities/example_command_run.yaml`

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

## Examples And Verification

Use [docs/smoke-test.md](docs/smoke-test.md) for the exact Docker smoke-test sequence, including:

- `GET /readyz`
- `POST /v1/example/http/echo`
- `POST /v1/example/command/run`
- `GET /v1/jobs/{job_id}`
- `/healthz`
- `/ops/readiness`
- `/ops/capabilities`
- `/ops/services`
- `/ops/runtime`
- `/ops/queues`
- optional Flower checks
- queued-job recovery with `POST /ops/queues/{lane}/cancel`

For direct backend debugging outside Turnstile, use:

```bash
make run-mock-http-alpha
make run-mock-http-beta
docker compose -f docker-compose.examples.yml up --build
```

For deployment-specific troubleshooting, see [docs/deployment.md](docs/deployment.md).
