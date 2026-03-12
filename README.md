# Turnstile

Turnstile is a typed capability broker for a single server with scarce local compute. It accepts JSON requests on stable API routes, validates them against JSON Schema, resolves them to a configured capability and service, and runs that work through warm HTTP backends or ephemeral command containers.

Turnstile is not a generic passthrough proxy, a Kubernetes control plane, a multi-host scheduler, or a system for arbitrary user-supplied containers. Extension happens through capability definitions, service definitions, and adapter types.

## Architecture

- Capabilities live in `config/capabilities/*.yaml`. They define the public route, request schema, response schema, queue lane, adapter type, and default service.
- Services live in `config/services/*.yaml`. They define the execution mode (`warm` or `ephemeral`), image, resource requirements, health behavior, cancellation strategy, and adapter config.
- Request and response schemas live in `config/requests/*.json` and `config/responses/*.json`.
- `http_forward_json` runs against warm HTTP backends. Turnstile can start the service container on demand, wait for readiness, reuse it, and evict it when a conflicting GPU resident is needed.
- `container_command` runs one-shot containers. Turnstile passes normalized request data through env vars and mounted files, captures stdout/stderr, reads output files, and records artifacts.
- Redis stores transient job records, GPU arbitration state, warm-service state, and recent-job indexes.
- Celery executes async work on named lanes. This repo ships `gpu` and `cpu` workers.
- Flower exposes worker/task visibility on a separate port.

## Runtime Modes

- `TURNSTILE_RUNTIME_MODE=stub`: local/unit-test mode. Adapters return deterministic fake results without Docker.
- `TURNSTILE_RUNTIME_MODE=docker`: deployment mode. Warm services and ephemeral jobs use the Docker Engine reachable through `/var/run/docker.sock` or `DOCKER_HOST`.

## Endpoints

- `GET /healthz`
- `GET /v1/services`
- `GET /v1/jobs/{job_id}`
- `POST /v1/jobs/{job_id}/cancel`
- `POST /v1/image/generate`
- `POST /v1/audio/transcribe`
- `GET /ops/runtime`
- `GET /ops/jobs`
- `GET /ops/services`
- `GET /ops/queues`
- `GET /docs`
- `GET /redoc`
- `GET /openapi.json`

## Single-Server Deployment

Host prerequisites:

- Docker Engine running on the target host
- Redis reachable by the API and workers
- If GPU-backed service containers are used: NVIDIA drivers, NVIDIA Container Toolkit, and Docker GPU support on the host
- Access to the Docker daemon from Turnstile through either:
  - `/var/run/docker.sock` bind-mounted into the API and worker containers, or
  - `DOCKER_HOST` / `TURNSTILE_DOCKER_HOST` pointing at a reachable Docker endpoint

The shipped Compose file is the production-shaped single-server deployment:

```bash
cp .env.example .env
docker compose up --build
```

That starts:

- `api` on `http://localhost:8000`
- `worker-gpu` subscribed to `gpu`
- `worker-cpu` subscribed to `cpu`
- `flower` on `http://localhost:5555`
- `redis` on `localhost:6379`

Compose also mounts `/var/run/docker.sock` into the API and workers so Turnstile can launch and cancel managed containers.

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

If you prefer one worker process locally:

```bash
make worker
```

## Docker Daemon Access

- Default local deployment: mount `/var/run/docker.sock` and leave `TURNSTILE_DOCKER_HOST` empty.
- Remote Docker daemon: set `DOCKER_HOST` and `TURNSTILE_DOCKER_HOST` to the same endpoint, and remove the socket mount if not needed.
- Warm-service networking:
  - In Compose, `TURNSTILE_DOCKER_NETWORK=turnstile_default` lets managed warm containers join the same bridge network as the control plane.
  - Outside Compose, leave `TURNSTILE_DOCKER_NETWORK` empty and set `TURNSTILE_DOCKER_SERVICE_HOST` to the host name the control plane should use to reach published ports.

More deployment notes live in [`docs/deployment.md`](docs/deployment.md).

## GPU-Backed Services

- Turnstile models one scarce GPU.
- Only one conflicting GPU resident is kept warm at a time.
- GPU jobs enter a FIFO Redis queue.
- The worker renews the GPU lease while a job is running so a long job does not lose the lock because of TTL expiry.
- Warm GPU services renew their residency lease until idle TTL expiry or eviction.

If a warm GPU service is active and a conflicting GPU service is needed, Turnstile evicts the idle resident before launching the next one.

## Operator Guide

Health and ops views:

- `GET /healthz`: Redis reachability, Docker reachability, queue presence, active job, active service
- `GET /ops/runtime`: queue snapshots, warm services, worker-lane visibility, dependency reachability
- `GET /ops/jobs`: recent job records
- `GET /ops/services`: service definitions plus warm runtime state
- `GET /ops/queues`: queue lane summaries
- Flower: `http://localhost:5555`

Cancellation:

- Queued jobs are removed from the queue and marked `cancelled`.
- Running ephemeral jobs are cancelled by stopping the tracked container.
- Running warm HTTP jobs support best-effort cancellation. If the service config provides `cancel_path`, Turnstile posts the `job_id` to that route. If not, Turnstile still marks the job cancelled, but in-flight backend work may continue.

## Adding a Capability

1. Add a request schema in `config/requests/`.
2. Add or reuse a response schema in `config/responses/`.
3. Add a capability definition in `config/capabilities/`.
4. Set the queue lane, adapter type, and `default_service_selection`.
5. Restart the API and confirm the route appears in `/openapi.json`.

The shipped examples are:

- `config/capabilities/image_generate.yaml`
- `config/capabilities/audio_transcribe.yaml`

## Adding a Service

Warm HTTP example:

- `config/services/mock_image_generator.yaml`
- mode: `warm`
- adapter: `http_forward_json`
- container behavior: starts an HTTP service, waits for `/healthz`, forwards `POST /generate`, exposes `POST /cancel`

Ephemeral command example:

- `config/services/mock_audio_transcriber.yaml`
- mode: `ephemeral`
- adapter: `container_command`
- container behavior: reads `TURNSTILE_REQUEST_JSON`, writes `transcript.txt`, returns normalized JSON

For command-backed services, Turnstile injects:

- `TURNSTILE_JOB_ID`
- `TURNSTILE_REQUEST_JSON`
- `TURNSTILE_REQUEST_FILE`
- `TURNSTILE_OUTPUT_DIR`

## End-to-End Examples

Warm HTTP-backed image generation:

```bash
curl -X POST http://localhost:8000/v1/image/generate \
  -H 'Content-Type: application/json' \
  -d '{"prompt":"studio portrait","style":"cinematic"}'
```

Ephemeral command-backed audio transcription:

```bash
curl -X POST http://localhost:8000/v1/audio/transcribe \
  -H 'Content-Type: application/json' \
  -d '{"audio_url":"https://example.com/clip.wav","language":"en"}'
```

Cancel a job:

```bash
curl -X POST http://localhost:8000/v1/jobs/<job_id>/cancel
```

## Troubleshooting

- `/healthz` says Docker is unreachable:
  - Check the socket mount or `TURNSTILE_DOCKER_HOST`.
- Warm service never becomes ready:
  - Check `startup_timeout_s`, `healthcheck`, and `adapter_config.container_port`.
- GPU jobs never start:
  - Check `GET /ops/runtime` for the active GPU service/job and worker lane health.
- A warm service blocks another GPU service:
  - Wait for idle TTL expiry or cancel/finish the active work so eviction can occur.
- Flower has no workers:
  - Confirm both worker commands are subscribed to the expected lanes and Redis is reachable.

## Verification

```bash
make test
make lint
make typecheck
```
