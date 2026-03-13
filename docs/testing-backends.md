# Testing Backends

These example backends are test harnesses for Turnstile. They are ordinary container images referenced by service YAML, not framework special cases and not hardcoded FastAPI routes.

## Included Images

- `turnstile/mock-http-tool:latest`
  - Warm HTTP backend for `http_forward_json`
  - Source: `examples/backends/mock_http_tool/`
- `turnstile/mock-command-tool:latest`
  - Ephemeral command backend for `container_command`
  - Source: `examples/backends/mock_command_tool/`

## Build Commands

Build both images:

```bash
make build-example-backends
```

Build one image at a time:

```bash
make build-mock-http-tool
make build-mock-command-tool
```

These commands produce the same tags used by the shipped service definitions:

- `turnstile/mock-http-tool:latest`
- `turnstile/mock-command-tool:latest`

## Generic Capabilities

The generic example endpoints come entirely from capability YAML:

- `config/capabilities/example_http_echo.yaml`
  - `POST /v1/example/http/echo`
- `config/capabilities/example_command_run.yaml`
  - `POST /v1/example/command/run`

No FastAPI route is hardcoded for those paths. Turnstile loads them from YAML and exposes them in OpenAPI and `/ops/capabilities`.

## Reusing One Image Across Many Services

Two warm HTTP services point at the same HTTP image:

```yaml
# config/services/mock_http_alpha.yaml
service_id: mock-http-alpha
image: turnstile/mock-http-tool:latest
adapter_config:
  env:
    MOCK_INSTANCE_ID: alpha
    MOCK_RESPONSE_PREFIX: "alpha:"
```

```yaml
# config/services/mock_http_beta.yaml
service_id: mock-http-beta
image: turnstile/mock-http-tool:latest
adapter_config:
  env:
    MOCK_INSTANCE_ID: beta
    MOCK_RESPONSE_PREFIX: "beta:"
```

The image stays the same. Only `service_id` and env differ. That is enough to test:

- warm reuse when `mock-http-alpha` is requested twice
- backend selection by `service_id`

The same pattern also works for command services. `mock-command-alpha` and `mock-command-beta` both use `turnstile/mock-command-tool:latest` and differ only by env such as `MOCK_INSTANCE_ID` and `MOCK_OUTPUT_BASENAME`.

## Selecting a Specific Backend Instance

Requests can override the default service by including `service_id` in the JSON payload.

Select alpha:

```bash
curl -X POST http://localhost:8000/v1/example/http/echo \
  -H 'Content-Type: application/json' \
  -d '{"text":"hello from alpha","service_id":"mock-http-alpha"}'
```

Select beta:

```bash
curl -X POST http://localhost:8000/v1/example/http/echo \
  -H 'Content-Type: application/json' \
  -d '{"text":"hello from beta","service_id":"mock-http-beta"}'
```

## Warm vs Ephemeral Testing

- Warm HTTP testing:
  - Turnstile starts or reuses `turnstile/mock-http-tool:latest`
  - service state is visible in `/ops/services` and `/ops/runtime`
- Ephemeral command testing:
  - Turnstile launches a one-shot container from `turnstile/mock-command-tool:latest`
  - the backend writes `result.json` plus artifacts into `TURNSTILE_OUTPUT_DIR`

## Canonical End-To-End Smoke Test

Use [docs/smoke-test.md](smoke-test.md) for the primary clean-checkout path. That document is the source of truth for:

- copying `.env.example` to `.env`
- building the example backend images
- starting `docker compose`
- checking `/healthz`, `/readyz`, `/ops/readiness`, `/ops/capabilities`, `/ops/services`, `/ops/runtime`, and `/ops/queues`
- submitting and polling `POST /v1/example/http/echo`
- submitting and polling `POST /v1/example/command/run`
- cancelling stranded queued jobs with `POST /ops/queues/{lane}/cancel`
- optionally checking Flower
- shutting the stack down cleanly

The executable version of that path is:

```bash
make smoke-docker
```

Keep the stack running after the smoke test:

```bash
make smoke-docker-keepalive
```

## Direct Backend Debugging

For manual backend debugging outside Turnstile, either run the Make targets:

```bash
make run-mock-http-alpha
make run-mock-http-beta
```

or start both HTTP mock instances directly:

```bash
docker compose -f docker-compose.examples.yml up --build
```

## Adding Another HTTP Mock Instance

To add `mock-http-gamma` using the same image:

1. Copy `config/services/mock_http_alpha.yaml` to `config/services/mock_http_gamma.yaml`.
2. Change `service_id` to `mock-http-gamma`.
3. Keep `image: turnstile/mock-http-tool:latest`.
4. Keep `capabilities: [example.http.echo]`.
5. Change only env such as:

```yaml
adapter_config:
  env:
    MOCK_INSTANCE_ID: gamma
    MOCK_RESPONSE_PREFIX: "gamma:"
    MOCK_TAG_COLOR: green
```

No new FastAPI route is needed. No new image is needed.
