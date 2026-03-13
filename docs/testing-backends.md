# Testing Backends

These example backends are ordinary container images referenced by service YAML. They are not hardcoded FastAPI routes and they are not adapter special cases.

## Included Images

- `turnstile/mock-http-tool:latest`
  - warm HTTP backend for `http_forward_json`
  - source: `examples/backends/mock_http_tool/`
- `turnstile/mock-command-tool:latest`
  - ephemeral command backend for `container_command`
  - source: `examples/backends/mock_command_tool/`

Build both with:

```bash
make build-example-backends
```

## Config-Driven Example Surface

The shipped generic example endpoints come from capability YAML:

- `config/capabilities/example_http_echo.yaml`
  - `POST /v1/example/http/echo`
- `config/capabilities/example_http_gpu_echo.yaml`
  - `POST /v1/example/http/gpu-echo`
- `config/capabilities/example_command_run.yaml`
  - `POST /v1/example/command/run`

The shipped backend instances come from service YAML:

- `config/services/mock_http_alpha.yaml`
- `config/services/mock_http_beta.yaml`
- `config/services/mock_gpu_http_alpha.yaml`
- `config/services/mock_gpu_http_beta.yaml`
- `config/services/mock_command_alpha.yaml`
- `config/services/mock_command_beta.yaml`

Check `GET /ops/capabilities` and `GET /openapi.json` if you want to verify the loaded capability surface. Those are the source of truth, along with the config files.

## Reusing One Image Across Many Services

The intended scaling model is to duplicate service YAML, not container images.

Warm HTTP CPU-safe examples:

```yaml
service_id: mock-http-alpha
image: turnstile/mock-http-tool:latest
gpu_required: false
adapter_config:
  env:
    MOCK_INSTANCE_ID: alpha
    MOCK_RESPONSE_PREFIX: "alpha:"
```

```yaml
service_id: mock-http-beta
image: turnstile/mock-http-tool:latest
gpu_required: false
adapter_config:
  env:
    MOCK_INSTANCE_ID: beta
    MOCK_RESPONSE_PREFIX: "beta:"
```

Warm GPU examples from the same image:

```yaml
service_id: mock-gpu-http-alpha
image: turnstile/mock-http-tool:latest
gpu_required: true
adapter_config:
  env:
    MOCK_INSTANCE_ID: gpu-alpha
    MOCK_RESPONSE_PREFIX: "gpu-alpha:"
```

```yaml
service_id: mock-gpu-http-beta
image: turnstile/mock-http-tool:latest
gpu_required: true
adapter_config:
  env:
    MOCK_INSTANCE_ID: gpu-beta
    MOCK_RESPONSE_PREFIX: "gpu-beta:"
```

That pattern proves:

- warm reuse when the same warm service is requested repeatedly
- backend selection by `service_id`
- scarce-GPU eviction when two warm GPU services contend for the single resident slot

The same pattern works for command services. `mock-command-alpha` and `mock-command-beta` both use `turnstile/mock-command-tool:latest` and differ only by env such as `MOCK_INSTANCE_ID` and `MOCK_OUTPUT_BASENAME`.

## Manual Requests

Select a warm HTTP service explicitly:

```bash
curl -X POST http://localhost:8000/v1/example/http/echo \
  -H 'Content-Type: application/json' \
  -d '{"text":"hello from alpha","service_id":"mock-http-alpha"}'
```

Select a warm GPU service explicitly:

```bash
curl -X POST http://localhost:8000/v1/example/http/gpu-echo \
  -H 'Content-Type: application/json' \
  -d '{"text":"hello from gpu beta","service_id":"mock-gpu-http-beta"}'
```

## Warm vs Ephemeral Testing

- Warm HTTP tests start or reuse `turnstile/mock-http-tool:latest` and expose resident state through `/ops/services` and `/ops/runtime`.
- Ephemeral command tests launch one-shot `turnstile/mock-command-tool:latest` containers that write `result.json` plus artifacts into `TURNSTILE_OUTPUT_DIR`.
- Artifact `path` values returned in job results are diagnostic server-local extracted paths, not durable storage handles.

## Automated Integration Suite

The notebook-style acceptance checks now live in the Docker-backed integration pytest suite.

The default fast suite remains the local stub-heavy path:

```bash
make test
```

Run the full suite:

```bash
make test-integration
```

Run only the scarce-GPU warm eviction scenario:

```bash
make test-gpu-eviction
```

The harness:

- builds the example backend images
- starts `docker compose`
- waits for `/ops/runtime` readiness
- runs `pytest -m integration`

The GPU eviction test requires Docker GPU support. On hosts without a usable GPU runtime, that specific test skips and the rest of the integration suite still runs.

The suite covers:

- readiness before submit
- warm HTTP success for alpha and beta
- warm service reuse
- command success for alpha and beta
- forced HTTP and command failures
- warm HTTP cancellation
- ops state sanity after jobs complete
- GPU warm reuse plus GPU warm eviction handoff

For the clean-checkout smoke path instead of pytest, use `make smoke-docker` or `make smoke-docker-keepalive`.

## Manual GPU Warm Eviction Check

Use [docs/smoke-test.md](smoke-test.md) for the full clean-checkout flow. For the specific scarce-GPU check, submit:

```bash
curl -X POST http://localhost:8000/v1/example/http/gpu-echo \
  -H 'Content-Type: application/json' \
  -d '{"text":"hello gpu alpha","service_id":"mock-gpu-http-alpha"}'

curl -X POST http://localhost:8000/v1/example/http/gpu-echo \
  -H 'Content-Type: application/json' \
  -d '{"text":"hello gpu beta","service_id":"mock-gpu-http-beta"}'
```

Then inspect:

```bash
curl -fsS http://localhost:8000/ops/services | python3 -m json.tool
curl -fsS http://localhost:8000/ops/runtime | python3 -m json.tool
docker ps --filter label=turnstile.managed=true
```

You should see the active warm GPU resident move from alpha to beta, with only one `gpu_required` warm resident at a time.

## Direct Backend Debugging

For manual backend debugging outside Turnstile, either run:

```bash
make run-mock-http-alpha
make run-mock-http-beta
```

or start both HTTP mock instances directly:

```bash
docker compose -f docker-compose.examples.yml up --build
```

## Adding Another Mock Instance

To add another backend instance, copy an existing service YAML, keep the same image, keep the same adapter type, and change only `service_id` plus env. No new FastAPI route is needed unless you are intentionally defining a new capability in `config/capabilities/`.
