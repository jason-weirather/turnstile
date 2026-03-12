# Single-Server Deployment Notes

This document expands the deployment path in `README.md` for the intended Turnstile target: one server, one scarce GPU, Dockerized control plane, warm HTTP backends, and ephemeral command-backed jobs.

## Prerequisites

- Docker Engine installed and running
- Redis reachable from the control plane and workers
- If GPU-backed containers are used:
  - NVIDIA drivers installed on the host
  - NVIDIA Container Toolkit installed
  - Docker configured to launch GPU workloads

## Docker Access Choices

Socket mount:

- Bind-mount `/var/run/docker.sock` into the API and worker containers
- Leave `TURNSTILE_DOCKER_HOST` empty
- This is the default in `docker-compose.yml`

Remote Docker daemon:

- Set `DOCKER_HOST` and `TURNSTILE_DOCKER_HOST`
- Ensure the API and workers can reach that endpoint
- Remove the socket mount if it is not required

## Warm-Service Networking

Managed warm services need to be reachable from the worker that forwards requests to them.

Recommended option:

- Set `TURNSTILE_DOCKER_NETWORK=turnstile_default`
- Launch Turnstile through the provided Compose file
- Turnstile starts warm service containers on the same bridge network and reaches them by container name

Fallback option:

- Leave `TURNSTILE_DOCKER_NETWORK` empty
- Set `TURNSTILE_DOCKER_SERVICE_HOST` to the hostname or IP the worker should use for published ports

## Compose Layout

The shipped Compose file starts:

- `api`
- `worker-gpu`
- `worker-cpu`
- `flower`
- `redis`

Both workers and the API receive Docker access because all three may need runtime control:

- the workers launch and monitor containers
- the API handles cancellation requests
- the API reports Docker health in `/healthz` and `/ops/runtime`

## Operational Checks

- `GET /healthz`
- `GET /ops/runtime`
- `GET /ops/jobs`
- `GET /ops/services`
- `GET /ops/queues`
- Flower on port `5555`

Before treating the deployment as healthy, confirm:

- Redis is reachable
- Docker is reachable
- `cpu` and `gpu` lanes both appear in `/ops/queues`
- the expected workers are attached to those lanes
- warm services appear in `/ops/services` after first use

## Extending the Runtime

The concrete example configs shipped with the repo live in:

- `config/capabilities/`
- `config/services/`
- `config/requests/`
- `config/responses/`

Use those files as the source of truth for supported config shape, not the RFC document.
