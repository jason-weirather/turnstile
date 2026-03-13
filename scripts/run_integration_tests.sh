#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

API_BASE_URL="${TURNSTILE_INTEGRATION_API_URL:-http://127.0.0.1:8000}"
STARTUP_TIMEOUT_S="${TURNSTILE_INTEGRATION_STARTUP_TIMEOUT_S:-120}"
POLL_INTERVAL_S="${TURNSTILE_INTEGRATION_POLL_INTERVAL_S:-2}"
KEEP_RUNNING=0
REBUILD_IMAGES=0
GPU_EVICTION_ONLY=0
STACK_STARTED=0
TEARDOWN_ON_EXIT=1

log() {
  printf '[integration] %s\n' "$*"
}

fail() {
  printf '[integration] ERROR: %s\n' "$*" >&2
  exit 1
}

usage() {
  cat <<'EOF'
Usage: scripts/run_integration_tests.sh [--keep-running] [--rebuild-images] [--gpu-eviction-only]

Options:
  --keep-running       Leave the Docker Compose stack running after the tests.
  --rebuild-images     Rebuild the example backend images even if they already exist.
  --gpu-eviction-only  Run only the scarce-GPU warm eviction integration test.
EOF
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
      --gpu-eviction-only)
        GPU_EVICTION_ONLY=1
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

compose() {
  docker compose "$@"
}

ensure_env_file() {
  if [[ -f .env ]]; then
    return 0
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

runtime_ready() {
  python3 - <<'PY'
import json
import os
import sys
import urllib.error
import urllib.request

url = os.environ["API_BASE_URL"] + "/ops/runtime"
try:
    with urllib.request.urlopen(url) as response:
        data = json.load(response)
except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError):
    raise SystemExit(1)

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
PY
}

wait_for_runtime() {
  local deadline=$((SECONDS + STARTUP_TIMEOUT_S))
  while true; do
    if API_BASE_URL="$API_BASE_URL" runtime_ready; then
      return 0
    fi
    if (( SECONDS >= deadline )); then
      fail "Timed out waiting for /ops/runtime readiness"
    fi
    sleep "$POLL_INTERVAL_S"
  done
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

run_pytest() {
  local -a pytest_args
  pytest_args=(-m integration tests/integration/test_acceptance_integration.py)
  if [[ "$GPU_EVICTION_ONLY" == "1" ]]; then
    pytest_args=(-m "integration and gpu_eviction" tests/integration/test_acceptance_integration.py)
  fi

  log "Running ${pytest_args[*]}"
  conda run -n turnstile_env pytest "${pytest_args[@]}"
}

main() {
  parse_args "$@"
  trap cleanup EXIT

  ensure_env_file
  ensure_image "turnstile/mock-http-tool:latest" "examples/backends/mock_http_tool"
  ensure_image "turnstile/mock-command-tool:latest" "examples/backends/mock_command_tool"

  log "Starting Docker Compose stack"
  compose up -d --build
  STACK_STARTED=1

  wait_for_runtime
  if [[ "$KEEP_RUNNING" == "1" ]]; then
    TEARDOWN_ON_EXIT=0
  fi
  run_pytest
}

main "$@"
