# RFC 0001: Turnstile v1

## Status

Proposed

## Summary

Turnstile is a front door, scheduler, and runtime broker for scarce local
compute. It presents a typed HTTP API for a small set of media capabilities and
decides whether each request should be served by a warm container, an ephemeral
container, or a queued execution lane.

Turnstile v1 is explicitly not a model server, a workflow engine, or a generic
container orchestrator. The product boundary is narrow: admit work, arbitrate
one scarce GPU, manage warm and cold backends, and expose a usable ops surface.

Tagline: "one API, one scarce GPU, many tools"

## Goals

- Expose stable, typed endpoints for a curated set of capabilities.
- Serialize GPU work through a single policy-controlled admission point.
- Support both warm services and one-shot ephemeral jobs.
- Keep job state, service state, and runtime decisions visible to operators.
- Make Panya and other clients depend on Turnstile as a separate service.

## Non-Goals

- Multi-node orchestration.
- Multi-GPU packing or placement.
- Arbitrary user-supplied container execution.
- Workflow DSLs or durable multi-step business processes.
- Billing, tenancy, or quota systems.
- Kubernetes integration.

## Product Definition

Turnstile accepts requests at capability-specific endpoints such as image
generation, image editing, and audio transcription. For each request, it:

1. Validates and normalizes the public API payload.
2. Resolves the request to a service adapter and service registry entry.
3. Applies scheduling policy against current resource state.
4. Routes the request to a warm backend, queues it, or starts a cold container.
5. Persists job state and exposes progress, cancellation, and operator insight.

## Primary Users

- Local applications that need a stable media API without directly managing GPU
  occupancy.
- Operators running a single workstation or server with one scarce GPU.
- Future platform clients such as Panya, which should consume Turnstile rather
  than embed runtime brokerage logic.

## Architecture

### Components

- FastAPI: public API, OpenAPI schema, Swagger UI, ReDoc, health endpoints.
- Celery + Redis: background execution, queue routing, retry primitives, worker
  lanes.
- Postgres: durable records for jobs, services, policies, and runtime state.
- Docker SDK for Python: container lifecycle control and GPU container startup.
- Flower: Celery monitoring UI under an operator-only route.

### High-Level Flow

1. Client calls a typed Turnstile endpoint.
2. API creates a job record and resolves an adapter.
3. Scheduler checks whether the request can run now, must wait, or requires
   evicting the current warm GPU resident.
4. Celery dispatches execution to the appropriate lane (`gpu`, `cpu`,
   `warmup`, or `admin`).
5. Runtime controller starts or reuses a container and captures artifacts and
   status.
6. API returns either a direct result or a `202 Accepted` response with a job
   identifier.

## Public API

Turnstile v1 should prefer capability-specific endpoints rather than a generic
passthrough.

### Endpoints

- `POST /v1/example/http/echo`
- `POST /v1/example/command/run`
- `GET /v1/jobs/{id}`
- `POST /v1/jobs/{id}/cancel`
- `GET /healthz`
- `GET /readyz`
- `GET /metrics`
- `GET /docs`
- `GET /redoc`
- `GET /openapi.json`
- `GET /ops/flower`
- `GET /ops/jobs`
- `GET /ops/services`
- `GET /ops/containers`

### API Shape Principles

- Each public endpoint has a typed request and response model.
- Each endpoint maps to an internal adapter, not directly to an arbitrary
  container URL.
- Public responses normalize backend-specific errors into a stable contract.
- Long-running work returns `202 Accepted` with a job resource.
- Short-running work may complete synchronously when policy allows.

## Service Registry

Turnstile needs a registry describing each backend it can broker. YAML is
acceptable for early development; Postgres-backed configuration is preferred for
longer-term operation.

### Minimum Schema

- `service_id`
- `capability`
- `image`
- `mode` (`warm` or `ephemeral`)
- `gpu_required`
- `estimated_vram_mb`
- `startup_timeout_s`
- `idle_ttl_s`
- `healthcheck`
- `endpoint_adapter`
- `cancel_strategy`
- `eviction_priority`

### Example

```yaml
service_id: comfyui-editor
capability: image.edit
image: ghcr.io/example/comfyui-editor:latest
mode: warm
gpu_required: true
estimated_vram_mb: 14336
startup_timeout_s: 90
idle_ttl_s: 600
healthcheck:
  type: http
  path: /health
endpoint_adapter: comfyui_edit
cancel_strategy: docker_stop
eviction_priority: 20
```

## Execution Modes

### Warm Mode

Warm mode keeps a backend container alive and private to Turnstile. Turnstile
proxies requests to it when the service is healthy and policy permits reuse.

Use warm mode for:

- expensive model load times
- repetitive bursts of small requests
- APIs where load time dominates request time

### Ephemeral Mode

Ephemeral mode starts a container for a single job, waits for completion,
collects outputs, and removes the container.

Use ephemeral mode for:

- spiky or memory-volatile jobs
- batch transforms
- tools with little benefit from staying warm

## Resource Arbiter

The arbiter is the core differentiator. For v1, policy should stay simple and
deterministic.

### v1 Policy

- One GPU execution lane.
- One GPU worker process with concurrency `1`.
- At most one warm GPU service resident at a time.
- FIFO scheduling by default.
- Optional operator-triggered priority lane later, not in the first cut.
- Idle warm GPU services auto-stop after a configured TTL.

### Primary Decisions

For each incoming job, the arbiter answers:

- Is the required backend already warm and healthy?
- Is the GPU lane currently busy?
- Can the current resident satisfy this request directly?
- Should the current warm resident be retained, evicted, or rejected?
- Is this job allowed to wait, or should admission fail fast?

### Service Lifecycle

```text
stopped -> starting -> warm -> busy -> idle -> evicting -> stopped
```

## Job Model

Every request becomes a job record, even when the client experiences the API as
synchronous.

### Job States

- `queued`
- `waiting_for_gpu`
- `starting_backend`
- `running`
- `streaming`
- `succeeded`
- `failed`
- `cancelled`
- `timed_out`

### Required Job Fields

- `job_id`
- `capability`
- `requested_service_id`
- `selected_service_id`
- `status`
- `request_payload`
- `result_payload`
- `error_code`
- `error_detail`
- `container_id`
- `created_at`
- `started_at`
- `finished_at`

## Cancellation

Cancellation must target the runtime unit actually consuming resources.

### v1 Rule

- Turnstile stores the active `container_id` for any running container-backed
  job.
- `POST /v1/jobs/{id}/cancel` resolves the container and calls Docker stop.
- Worker logic treats container termination as a cancellation outcome and
  persists the terminal state.

Celery revoke may still be useful for queued work, but it should not be the
primary kill mechanism for live GPU jobs.

## Deployment Shape

### Logical Topology

```text
Client
  -> Turnstile API (FastAPI)
       -> Postgres
       -> Redis
       -> Celery workers
       -> Docker daemon
       -> Flower (operator-only)
```

### Suggested Lanes

- `gpu`: GPU-bound execution, concurrency `1`
- `cpu`: CPU-only jobs
- `warmup`: service startup and preloading
- `admin`: cleanup, eviction, and control tasks

## Operator Surface

Turnstile should expose one address with both developer docs and operator views.

### v1 Operator Routes

- `/docs`
- `/redoc`
- `/openapi.json`
- `/ops/flower`
- `/ops/jobs`
- `/ops/services`
- `/ops/containers`
- `/metrics`
- `/healthz`

## Risks

- Container startup variance may make synchronous behavior unpredictable.
- GPU memory estimation will be heuristic at first and may require conservative
  policy.
- Cancellation correctness depends on reliably tracking container identity and
  terminal state transitions.
- Redis and Celery are sufficient for v1, but not a substitute for a workflow
  engine if product scope expands into durable orchestration.

## v1 Exclusions

The following are intentionally excluded from the first version:

- multi-host scheduling
- multi-GPU placement
- generic reverse proxying to arbitrary containers
- user-defined pipelines
- tenancy and billing
- Kubernetes deployment abstractions

## Open Questions

- Which exact capabilities should ship first beyond image generation, image
  editing, and audio transcription?
- Should job artifact storage start as local disk, object storage, or
  database-backed metadata plus filesystem blobs?
- How much operator control should `/ops/services` expose in v1: read-only
  visibility, or manual warm/evict actions?

## Decision

Proceed with Turnstile as a separate application. Panya and other clients should
integrate with Turnstile over its public API rather than embedding resource
arbitration logic directly.
