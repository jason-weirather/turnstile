# Single-Server Deployment Notes

This document covers the intended deployment target for Turnstile: one server, one scarce GPU, a Dockerized control plane, warm HTTP backends, and ephemeral command-backed jobs.

For the clean-checkout, operator-facing verification path, use [docs/smoke-test.md](smoke-test.md). This document focuses on deployment semantics and troubleshooting rather than the canonical smoke sequence.

## Prerequisites

- Docker Engine installed and running
- Redis reachable from the API and workers
- If GPU-backed containers are used:
  - NVIDIA drivers installed on the host
  - NVIDIA Container Toolkit installed
  - Docker configured for GPU workloads

## Docker Access

Socket mount:

- Mount `/var/run/docker.sock` into the API and worker containers
- Leave `TURNSTILE_DOCKER_HOST` empty
- This is the default in `docker-compose.yml`

Remote daemon:

- Set `DOCKER_HOST` and `TURNSTILE_DOCKER_HOST` to the same endpoint
- Ensure the API and workers can reach it
- Remove the socket mount if it is not needed

If Docker is unreachable, `GET /healthz`, `GET /readyz`, and `GET /ops/runtime` will report that directly.

## Compose Networking

The shipped Compose file declares an explicit bridge network named `turnstile`.

- `api`, `worker-gpu`, `worker-cpu`, `flower`, and `redis` all join `turnstile`
- managed warm service containers are started on that same network
- Turnstile addresses warm services by their generated Docker container name while they are attached to `turnstile`

If you deploy outside Compose:

- leave `TURNSTILE_DOCKER_NETWORK` empty
- set `TURNSTILE_DOCKER_SERVICE_HOST` to the hostname or IP used for published ports

## Ephemeral Command Containers

Ephemeral `container_command` services do not rely on host bind mounts for request or artifact exchange.

- before start, Turnstile uploads `/turnstile/input/request.json` into the child container with Docker archive copy APIs
- the child container writes any outputs under `/turnstile/output`
- after completion, Turnstile downloads `/turnstile/output` back out and records artifacts from the extracted files

This design works when Turnstile itself is running in a container and talking to a host Docker daemon through the mounted Docker socket. Bind-mounted temp paths created inside the worker container are no longer part of the contract.

## Compose Layout

The shipped Compose file starts:

- `api`
- `worker-gpu`
- `worker-cpu`
- `flower`
- `redis`

Both workers and the API receive Docker access:

- workers launch and monitor containers
- the API handles cancellation requests
- the API reports liveness in `/healthz` and submission readiness in `/readyz`

The Celery app target is `worker:celery_app` for workers and Flower.

## Operational Checks

Check:

- `GET /healthz`
- `GET /readyz`
- `GET /ops/readiness`
- `GET /ops/runtime`
- `GET /ops/jobs`
- `GET /ops/services`
- `GET /ops/capabilities`
- `GET /ops/queues`
- Flower on port `5555`

Before treating the deployment as healthy, confirm:

- Redis is reachable
- Docker is reachable
- `GET /readyz` returns success before you submit any async work
- `cpu` and `gpu` lanes both appear in `/ops/queues`
- the expected workers are attached to those lanes and marked `submission_ready`
- loaded capabilities appear in `/ops/capabilities`
- warm services appear in `/ops/services` after first use

## Troubleshooting

- `httpx` import failures in containers:
  - rebuild after updating runtime dependencies; the image installs `pip install -e .`
- Flower startup fails:
  - confirm the image includes the `flower` package and the container uses `celery -A worker:celery_app flower`
- Docker socket access fails:
  - confirm `/var/run/docker.sock` is mounted into the API and worker containers and readable by the container user
- Warm service networking fails:
  - confirm `TURNSTILE_DOCKER_NETWORK=turnstile` and that the control-plane services are also attached to `turnstile`
- Artifact or request file exchange fails for ephemeral jobs:
  - confirm the Docker daemon is reachable; Turnstile depends on Docker archive copy APIs to transfer `/turnstile/input` and `/turnstile/output`
- Jobs stay `queued` forever:
  - do not trust `/healthz` alone; check `/readyz` and `/ops/readiness`
  - inspect `/ops/runtime` and `/ops/queues`
  - inspect `docker compose ps`
  - inspect `docker compose logs worker-gpu worker-cpu`
  - cancel stranded queued jobs explicitly with `POST /ops/queues/{lane}/cancel`

## Extending the Runtime

The config source of truth lives in:

- `config/capabilities/`
- `config/services/`
- `config/requests/`
- `config/responses/`

Use those files, plus `GET /ops/capabilities`, as the source of truth for supported contract shape.
