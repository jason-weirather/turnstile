#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

API_BASE_URL="${TURNSTILE_SMOKE_API_URL:-http://127.0.0.1:8000}"
FLOWER_URL="${TURNSTILE_SMOKE_FLOWER_URL:-http://flower:5555}"
STARTUP_TIMEOUT_S="${TURNSTILE_SMOKE_STARTUP_TIMEOUT_S:-120}"
JOB_TIMEOUT_S="${TURNSTILE_SMOKE_JOB_TIMEOUT_S:-120}"
POLL_INTERVAL_S="${TURNSTILE_SMOKE_POLL_INTERVAL_S:-2}"
KEEP_RUNNING="${TURNSTILE_SMOKE_KEEP_RUNNING:-0}"
REBUILD_IMAGES="${TURNSTILE_SMOKE_REBUILD_IMAGES:-0}"

TEARDOWN_ON_EXIT=1
STACK_WAS_RUNNING=0
STACK_STARTED=0

log() {
  printf '[smoke] %s\n' "$*"
}

fail() {
  printf '[smoke] ERROR: %s\n' "$*" >&2
  exit 1
}

usage() {
  cat <<'EOF'
Usage: scripts/smoke_test.sh [--keep-running] [--rebuild-images]

Options:
  --keep-running   Leave the Docker Compose stack running after the smoke test.
  --rebuild-images Rebuild the example backend images even if they already exist.
EOF
}

require_command() {
  local cmd="$1"
  command -v "$cmd" >/dev/null 2>&1 || fail "Required command not found: $cmd"
}

compose() {
  docker compose "$@"
}

json_pretty() {
  python3 -m json.tool
}

json_extract() {
  local expr="$1"
  python3 -c '
import json
import sys

expr = sys.argv[1]
data = json.load(sys.stdin)
value = eval(expr, {"__builtins__": {}}, {"data": data})
if isinstance(value, bool):
    print("true" if value else "false")
elif value is None:
    print("")
else:
    print(value)
' "$expr"
}

wait_for_condition() {
  local description="$1"
  local timeout_s="$2"
  shift 2
  local deadline=$((SECONDS + timeout_s))
  while true; do
    if "$@"; then
      return 0
    fi
    if (( SECONDS >= deadline )); then
      return 1
    fi
    sleep "$POLL_INTERVAL_S"
  done
}

api_get() {
  local path="$1"
  compose exec -T api python -c '
import sys
import urllib.error
import urllib.request

try:
    with urllib.request.urlopen(sys.argv[1]) as response:
        print(response.read().decode())
except (urllib.error.URLError, TimeoutError, OSError):
    raise SystemExit(1)
' "$API_BASE_URL$path"
}

api_post() {
  local path="$1"
  local payload="$2"
  compose exec -T api python -c '
import sys
import urllib.error
import urllib.request

body = sys.argv[2].encode("utf-8")
request = urllib.request.Request(
    sys.argv[1],
    data=body,
    headers={"Content-Type": "application/json"},
    method="POST",
)
try:
    with urllib.request.urlopen(request) as response:
        print(response.read().decode())
except (urllib.error.URLError, TimeoutError, OSError):
    raise SystemExit(1)
' "$API_BASE_URL$path" "$payload"
}

show_json_endpoint() {
  local path="$1"
  log "GET $path"
  api_get "$path" | json_pretty
}

flower_status() {
  compose exec -T api python -c '
import sys
import urllib.error
import urllib.request

try:
    with urllib.request.urlopen(sys.argv[1]) as response:
        print(response.status)
except urllib.error.HTTPError as exc:
    print(exc.code)
    raise SystemExit(0 if exc.code == 401 else 1)
except (urllib.error.URLError, TimeoutError, OSError):
    raise SystemExit(1)
' "$FLOWER_URL/api/workers"
}

ensure_env_file() {
  if [[ -f .env ]]; then
    return 0
  fi
  if [[ ! -f .env.example ]]; then
    fail "Missing .env and .env.example"
  fi
  cp .env.example .env
  log "Copied .env.example to .env"
}

ensure_image() {
  local tag="$1"
  local context_dir="$2"
  if [[ "$REBUILD_IMAGES" == "1" ]] || ! docker image inspect "$tag" >/dev/null 2>&1; then
    log "Building $tag"
    docker build -t "$tag" "$context_dir"
  else
    log "Using existing image $tag"
  fi
}

remove_managed_containers() {
  local ids
  ids="$(docker ps -aq --filter label=turnstile.managed=true --filter network=turnstile)"
  if [[ -n "$ids" ]]; then
    log "Removing Turnstile-managed runtime containers"
    docker rm -f $ids >/dev/null 2>&1 || true
  fi
}

cleanup() {
  local exit_code=$?
  if [[ "$TEARDOWN_ON_EXIT" == "1" && "$STACK_STARTED" == "1" ]]; then
    remove_managed_containers
    log "Stopping Docker Compose stack"
    compose down --remove-orphans -v || true
  elif [[ "$STACK_STARTED" == "1" ]]; then
    log "Leaving Docker Compose stack running"
  fi
  exit "$exit_code"
}

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --keep-running)
        KEEP_RUNNING=1
        shift
        ;;
      --rebuild-images)
        REBUILD_IMAGES=1
        shift
        ;;
      -h|--help)
        usage
        exit 0
        ;;
      *)
        fail "Unknown argument: $1"
        ;;
    esac
  done
}

runtime_ready() {
  local payload
  payload="$(api_get /ops/runtime 2>/dev/null)" || return 1
  printf '%s' "$payload" | python3 -c '
import json
import sys

data = json.load(sys.stdin)
lanes = {item["lane"]: item for item in data.get("worker_lanes", [])}
queue_names = {item["lane"] for item in data.get("queues", [])}
ok = (
    data.get("redis_reachable") is True
    and data.get("docker_reachable") is True
    and data.get("submission_ready") is True
    and {"cpu", "gpu"} <= queue_names
    and lanes.get("cpu", {}).get("submission_ready") is True
    and lanes.get("gpu", {}).get("submission_ready") is True
)
raise SystemExit(0 if ok else 1)
'
}

submit_job() {
  local path="$1"
  local payload="$2"
  local response
  response="$(
    api_post "$path" "$payload"
  )"
  printf '%s\n' "$response" | json_pretty >&2
  printf '%s' "$response" | python3 -c '
import json
import sys

data = json.load(sys.stdin)
if data.get("status") != "queued":
    raise SystemExit("job was not queued")
job_id = data.get("job_id")
if not isinstance(job_id, str) or not job_id:
    raise SystemExit("job_id missing from submit response")
print(job_id)
'
}

poll_job() {
  local job_id="$1"
  local output_file="$2"
  local deadline=$((SECONDS + JOB_TIMEOUT_S))
  while true; do
    local response
    response="$(api_get "/v1/jobs/$job_id")"
    local status
    status="$(printf '%s' "$response" | json_extract 'data["status"]')"
    log "Job $job_id status: $status"
    case "$status" in
      succeeded)
        printf '%s\n' "$response" | tee "$output_file" | json_pretty
        return 0
        ;;
      failed|cancelled)
        printf '%s\n' "$response" | tee "$output_file" | json_pretty
        fail "Job $job_id ended with status $status"
        ;;
    esac
    if (( SECONDS >= deadline )); then
      fail "Timed out waiting for job $job_id"
    fi
    sleep "$POLL_INTERVAL_S"
  done
}

verify_http_job() {
  local path="$1"
  python3 - "$path" <<'PY'
import json
import sys
from pathlib import Path

data = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
payload = data["result_payload"]

assert data["status"] == "succeeded"
assert data["capability"] == "example.http.echo"
assert data["selected_service_id"] == "mock-http-alpha"
assert payload["backend_kind"] == "mock_http_tool"
assert payload["instance_id"] == "alpha"
assert payload["response_prefix"] == "alpha:"
assert payload["response_text"] == "alpha:hello warm world"
assert payload["request_echo"]["text"] == "hello warm world"
print("verified")
PY
}

verify_command_job() {
  local path="$1"
  python3 - "$path" <<'PY'
import json
import sys
from pathlib import Path

data = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
payload = data["result_payload"]
artifact_names = sorted(payload["artifact_names"])
artifacts = sorted(item["name"] for item in payload["artifacts"])

assert data["status"] == "succeeded"
assert data["capability"] == "example.command.run"
assert data["selected_service_id"] == "mock-command-alpha"
assert payload["backend_kind"] == "mock_command_tool"
assert payload["instance_id"] == "command-alpha"
assert payload["output_basename"] == "alpha-output"
assert payload["request_echo"]["artifact_name"] == "note.txt"
assert payload["request_echo"]["artifact_text"] == "artifact payload"
assert artifact_names == ["note.txt"]
assert artifacts == ["note.txt", "result.json"]
print("verified")
PY
}

main() {
  parse_args "$@"

  require_command docker
  require_command curl
  require_command python3

  ensure_env_file

  if compose ps --services --status running 2>/dev/null | grep -q .; then
    STACK_WAS_RUNNING=1
  fi
  if [[ "$KEEP_RUNNING" == "1" || "$STACK_WAS_RUNNING" == "1" ]]; then
    TEARDOWN_ON_EXIT=0
  fi

  trap cleanup EXIT INT TERM

  ensure_image "turnstile/mock-http-tool:latest" "examples/backends/mock_http_tool"
  ensure_image "turnstile/mock-command-tool:latest" "examples/backends/mock_command_tool"

  log "Starting Docker Compose stack"
  compose up -d --build
  STACK_STARTED=1

  log "Waiting for API"
  wait_for_condition "API readiness" "$STARTUP_TIMEOUT_S" api_get /healthz >/dev/null \
    || fail "API did not become reachable at $API_BASE_URL/healthz"

  log "Waiting for Redis, Docker, queues, and workers"
  wait_for_condition "runtime readiness" "$STARTUP_TIMEOUT_S" runtime_ready \
    || fail "runtime did not report healthy cpu/gpu workers and reachable Docker"

  show_json_endpoint /healthz
  show_json_endpoint /readyz
  show_json_endpoint /ops/readiness
  show_json_endpoint /ops/capabilities
  show_json_endpoint /ops/services
  show_json_endpoint /ops/runtime
  show_json_endpoint /ops/queues

  log "Submitting warm HTTP example job"
  local http_job_id
  http_job_id="$(submit_job /v1/example/http/echo '{"text":"hello warm world","service_id":"mock-http-alpha"}')"
  local http_job_file
  http_job_file="$(mktemp)"
  poll_job "$http_job_id" "$http_job_file"
  verify_http_job "$http_job_file" >/dev/null
  log "Verified warm HTTP example job"

  log "Submitting ephemeral command example job"
  local command_job_id
  command_job_id="$(
    submit_job /v1/example/command/run \
      '{"text":"write artifact","artifact_name":"note.txt","artifact_text":"artifact payload","service_id":"mock-command-alpha"}'
  )"
  local command_job_file
  command_job_file="$(mktemp)"
  poll_job "$command_job_id" "$command_job_file"
  verify_command_job "$command_job_file" >/dev/null
  log "Verified ephemeral command example job"

  log "Optional Flower check"
  local flower_http_status
  if flower_http_status="$(flower_status)"; then
    if [[ "$flower_http_status" == "401" ]]; then
      log "Flower is reachable at $FLOWER_URL but requires authentication"
    else
      compose exec -T api python -c '
import sys
import urllib.request

print(urllib.request.urlopen(sys.argv[1]).read().decode())
' "$FLOWER_URL/api/workers" | json_pretty
      log "Flower responded at $FLOWER_URL"
    fi
  else
    log "Flower check skipped or unavailable at $FLOWER_URL"
  fi

  rm -f "$http_job_file" "$command_job_file"
  log "Smoke test completed successfully"
}

main "$@"
