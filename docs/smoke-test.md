# Docker Smoke Test

This is the primary source of truth for proving that a clean Turnstile checkout works end to end in Docker with the shipped example backends.

`make smoke-docker` automates the exact sequence below. Use `make smoke-docker-keepalive` if you want the stack left up for inspection after the checks pass.

## Canonical Sequence

1. Copy the example environment file:

```bash
cp .env.example .env
```

2. Build the shipped example backend images used by `config/services/*.yaml`:

```bash
make build-example-backends
```

That command builds:

- `turnstile/mock-http-tool:latest`
- `turnstile/mock-command-tool:latest`

3. Start the full Docker stack:

```bash
docker compose up -d --build
```

4. Check the control-plane and runtime endpoints:

```bash
curl -fsS http://localhost:8000/healthz | python3 -m json.tool
curl -fsS http://localhost:8000/readyz | python3 -m json.tool
curl -fsS http://localhost:8000/ops/readiness | python3 -m json.tool
curl -fsS http://localhost:8000/ops/capabilities | python3 -m json.tool
curl -fsS http://localhost:8000/ops/services | python3 -m json.tool
curl -fsS http://localhost:8000/ops/runtime | python3 -m json.tool
curl -fsS http://localhost:8000/ops/queues | python3 -m json.tool
```

Expected checkpoints:

- `/healthz` reports liveness and dependency reachability.
- `/readyz` returns success before you submit any async job.
- `/ops/readiness` shows `submission_ready: true` for both `cpu` and `gpu`.
- `/ops/capabilities` includes `example.http.echo` and `example.command.run`.
- `/ops/services` lists the shipped mock services from `config/services/`.
- `/ops/runtime` shows `docker_reachable: true` and `submission_ready: true` for both `cpu` and `gpu`.
- `/ops/queues` includes both `cpu` and `gpu`.

Do not submit async jobs until `GET /readyz` succeeds. `GET /healthz` alone is not a submission gate.

5. Submit one warm HTTP-backed example job:

```bash
HTTP_JOB_ID="$(
  curl -fsS -X POST http://localhost:8000/v1/example/http/echo \
    -H 'Content-Type: application/json' \
    -d '{"text":"hello warm world","service_id":"mock-http-alpha"}' |
  python3 -c 'import json,sys; print(json.load(sys.stdin)["job_id"])'
)"

echo "$HTTP_JOB_ID"
```

Expected submit response shape:

```json
{
  "job_id": "<uuid>",
  "status": "queued"
}
```

Poll until the job reaches a terminal state:

```bash
while true; do
  HTTP_JOB_JSON="$(curl -fsS "http://localhost:8000/v1/jobs/$HTTP_JOB_ID")"
  printf '%s\n' "$HTTP_JOB_JSON" | python3 -m json.tool
  HTTP_STATUS="$(printf '%s\n' "$HTTP_JOB_JSON" | python3 -c 'import json,sys; print(json.load(sys.stdin)["status"])')"
  if [ "$HTTP_STATUS" = "succeeded" ]; then
    break
  fi
  if [ "$HTTP_STATUS" = "failed" ] || [ "$HTTP_STATUS" = "cancelled" ]; then
    exit 1
  fi
  sleep 2
done
```

Expected success shape:

```json
{
  "job_id": "<uuid>",
  "status": "succeeded",
  "capability": "example.http.echo",
  "selected_service_id": "mock-http-alpha",
  "result_payload": {
    "backend_kind": "mock_http_tool",
    "instance_id": "alpha",
    "response_prefix": "alpha:",
    "response_text": "alpha:hello warm world",
    "request_echo": {
      "text": "hello warm world",
      "service_id": "mock-http-alpha"
    }
  }
}
```

6. Submit one ephemeral command-backed example job:

```bash
COMMAND_JOB_ID="$(
  curl -fsS -X POST http://localhost:8000/v1/example/command/run \
    -H 'Content-Type: application/json' \
    -d '{"text":"write artifact","artifact_name":"note.txt","artifact_text":"artifact payload","service_id":"mock-command-alpha"}' |
  python3 -c 'import json,sys; print(json.load(sys.stdin)["job_id"])'
)"

echo "$COMMAND_JOB_ID"
```

Poll until the command job reaches a terminal state:

```bash
while true; do
  COMMAND_JOB_JSON="$(curl -fsS "http://localhost:8000/v1/jobs/$COMMAND_JOB_ID")"
  printf '%s\n' "$COMMAND_JOB_JSON" | python3 -m json.tool
  COMMAND_STATUS="$(printf '%s\n' "$COMMAND_JOB_JSON" | python3 -c 'import json,sys; print(json.load(sys.stdin)["status"])')"
  if [ "$COMMAND_STATUS" = "succeeded" ]; then
    break
  fi
  if [ "$COMMAND_STATUS" = "failed" ] || [ "$COMMAND_STATUS" = "cancelled" ]; then
    exit 1
  fi
  sleep 2
done
```

Expected success shape:

```json
{
  "job_id": "<uuid>",
  "status": "succeeded",
  "capability": "example.command.run",
  "selected_service_id": "mock-command-alpha",
  "result_payload": {
    "backend_kind": "mock_command_tool",
    "instance_id": "command-alpha",
    "output_basename": "alpha-output",
    "artifact_names": ["note.txt"],
    "artifacts": [
      {"name": "note.txt", "path": "<local extracted path>", "size_bytes": 16},
      {"name": "result.json", "path": "<local extracted path>", "size_bytes": 200}
    ],
    "request_echo": {
      "artifact_name": "note.txt",
      "artifact_text": "artifact payload",
      "service_id": "mock-command-alpha",
      "text": "write artifact"
    }
  }
}
```

7. Optionally inspect Flower:

```bash
curl -fsS http://localhost:5555/api/workers | python3 -m json.tool
```

8. Shut the stack down cleanly:

```bash
TURNSTILE_MANAGED_IDS="$(docker ps -aq --filter label=turnstile.managed=true --filter network=turnstile)"
if [ -n "$TURNSTILE_MANAGED_IDS" ]; then
  docker rm -f $TURNSTILE_MANAGED_IDS
fi
docker compose down --remove-orphans -v
```

## One-Command Version

Run the entire smoke test, including image build, compose startup, readiness checks, job submission, job polling, result verification, optional Flower probe, and teardown:

```bash
make smoke-docker
```

Keep the stack running after the smoke test finishes:

```bash
make smoke-docker-keepalive
```

The script lives at `scripts/smoke_test.sh`. It fails fast, exits nonzero on any error, and verifies both the warm HTTP route and the ephemeral command-backed route through `GET /v1/jobs/{job_id}`.

The script performs its API and Flower probes from inside the Docker Compose network, so it remains reliable even if `localhost:8000` or `localhost:5555` is already claimed by another local process.

## Recovery And Troubleshooting

If jobs stay queued forever, inspect:

```bash
curl -sS http://localhost:8000/readyz | python3 -m json.tool
curl -sS http://localhost:8000/ops/readiness | python3 -m json.tool
curl -sS http://localhost:8000/ops/runtime | python3 -m json.tool
curl -sS http://localhost:8000/ops/queues | python3 -m json.tool
docker compose ps
docker compose logs worker-gpu worker-cpu
```

If queued jobs are already stranded on a dead lane, cancel them explicitly:

```bash
curl -fsS -X POST http://localhost:8000/ops/queues/gpu/cancel | python3 -m json.tool
curl -fsS -X POST http://localhost:8000/ops/queues/cpu/cancel | python3 -m json.tool
```

## Notebook Check

In Jupyter or a notebook, verify readiness before submit:

```python
import requests

ready = requests.get("http://localhost:8000/readyz", timeout=5)
ready.raise_for_status()

runtime = requests.get("http://localhost:8000/ops/readiness", timeout=5).json()
assert all(lane["submission_ready"] for lane in runtime["worker_lanes"]), runtime
```
