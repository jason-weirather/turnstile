# RFC 0001: Turnstile v1

## Status

Accepted in part

## Summary

Turnstile is a typed capability broker for a single server with scarce local compute. It exposes YAML-defined HTTP capabilities, routes them onto `cpu` or `gpu` Celery lanes, and executes them through warm HTTP services or ephemeral command containers managed through Docker.

The implemented system is intentionally narrow:

- one server
- one scarce GPU
- typed capability endpoints
- warm services plus ephemeral jobs
- readiness-gated async submission
- operator visibility through `/ops/*`

Turnstile is not a generic passthrough proxy, a workflow engine, or a multi-node orchestrator.

## Source Of Truth

The source of truth for the public API and backend inventory is:

- `config/capabilities/*.yaml`
- `config/services/*.yaml`
- `config/requests/*.json`
- `config/responses/*.json`
- `GET /ops/capabilities`

This RFC describes the design direction and current scope. The config files and live ops endpoints describe the exact shipped surface.

## Implemented Architecture

### Components

- FastAPI for the public API, OpenAPI generation, and health/ops endpoints
- Celery for async execution on the `cpu` and `gpu` lanes
- Redis for transient job state, queue state, and scarce-GPU arbitration state
- Docker SDK / Docker API for warm container lifecycle, ephemeral jobs, cancellation, and archive copy
- Flower for worker visibility

### Execution Model

1. A client calls a typed capability endpoint such as `POST /v1/example/http/echo`, `POST /v1/example/http/gpu-echo`, or `POST /v1/example/command/run`.
2. Turnstile validates the request against the capability schema loaded from YAML.
3. Turnstile resolves a service from the service registry loaded from YAML.
4. Readiness checks gate async submission so work is rejected fast when the target lane is not ready.
5. Celery dispatches the job to the target lane.
6. The runtime either reuses or starts a warm HTTP container, or launches a one-shot command container.
7. Turnstile records transient job state and exposes runtime state through `/ops/runtime`, `/ops/services`, `/ops/jobs`, and `/ops/queues`.

### Resource Policy

The v1 policy is deliberately simple:

- one `gpu` lane
- one scarce GPU resident at a time for conflicting warm GPU services
- warm GPU services may be evicted when another warm GPU service is requested
- readiness must be green before async work is accepted
- warm services expire after their configured idle TTL

## Public API Shape

Turnstile prefers capability-specific routes instead of a generic forwarding endpoint.

Implemented route families:

- config-driven capability routes under `/v1/...`
- job status and cancellation routes under `/v1/jobs/...`
- health and readiness routes
- operator visibility routes under `/ops/...`

All public capability routes are typed. Capabilities define the public contract. Services define implementations. Adapters decide how a capability is executed.

## Runtime Contracts

### Warm HTTP Services

Warm services are private backend containers started and managed by Turnstile. They are reused when healthy and when policy permits reuse.

### Ephemeral Command Jobs

Ephemeral jobs exchange inputs and outputs through Docker archive copy APIs:

- Turnstile uploads `/turnstile/input/request.json`
- the backend writes outputs under `/turnstile/output`
- Turnstile downloads `/turnstile/output` after completion

Artifact paths recorded in job results are diagnostic server-local extracted paths. They are not durable storage handles.

## Implemented Examples

The repo currently ships generic examples that demonstrate the architecture without hardcoded capability routes:

- `example.http.echo`
- `example.http.gpu-echo`
- `example.command.run`

The example warm HTTP services show the intended scaling model:

- one reusable image
- multiple service YAMLs
- different `service_id` values and env

That pattern is used for both non-GPU warm reuse and scarce-GPU warm eviction.

## Implemented Vs Future

Implemented now:

- YAML-defined capabilities and services
- typed OpenAPI surface generated from those definitions
- Redis-backed transient job and warm-service state
- Celery lane routing for `cpu` and `gpu`
- warm HTTP reuse
- warm GPU eviction for conflicting residents
- ephemeral command execution with artifact extraction
- cancellation for warm HTTP and ephemeral jobs
- `/ops/readiness`, `/ops/runtime`, `/ops/services`, `/ops/jobs`, and `/ops/queues`

Explicitly deferred:

- durable artifact storage
- durable historical job database
- Postgres-backed persistence
- multi-node scheduling
- multi-GPU placement and packing
- Kubernetes integration
- generic passthrough proxying

## Notes

If the repo docs and this RFC drift, prefer the implementation-facing docs plus the config files and `/ops/capabilities`. This RFC should stay short and aligned with the live system rather than describe future architecture as if it already exists.
