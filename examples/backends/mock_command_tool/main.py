import json
import os
import sys
import time
from pathlib import Path
from typing import Any

INSTANCE_ID = os.getenv("MOCK_INSTANCE_ID", "mock-command-tool")
DEFAULT_SLEEP_S = float(os.getenv("MOCK_DEFAULT_SLEEP_S", "0"))
OUTPUT_BASENAME = os.getenv("MOCK_OUTPUT_BASENAME", "mock-output")


def _load_request() -> dict[str, Any]:
    raw_json = os.getenv("TURNSTILE_REQUEST_JSON")
    if raw_json:
        parsed = json.loads(raw_json)
        if isinstance(parsed, dict):
            return parsed
        return {"value": parsed}

    request_file = os.getenv("TURNSTILE_REQUEST_FILE")
    if request_file:
        parsed = json.loads(Path(request_file).read_text(encoding="utf-8"))
        if isinstance(parsed, dict):
            return parsed
        return {"value": parsed}
    return {}


def _coerce_sleep(value: Any) -> float:
    if value is None:
        return DEFAULT_SLEEP_S
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return DEFAULT_SLEEP_S


def _is_true(value: Any) -> bool:
    return value is True or str(value).lower() in {"1", "true", "yes", "on"}


def main() -> int:
    request = _load_request()
    sleep_s = _coerce_sleep(request.get("sleep_s"))
    if sleep_s > 0:
        time.sleep(sleep_s)

    output_dir = Path(os.getenv("TURNSTILE_OUTPUT_DIR", "/tmp/turnstile-output"))
    output_dir.mkdir(parents=True, exist_ok=True)

    artifact_name = str(request.get("artifact_name") or f"{OUTPUT_BASENAME}.txt")
    artifact_text = str(
        request.get("artifact_text")
        or request.get("text")
        or request.get("prompt")
        or f"{INSTANCE_ID}:{OUTPUT_BASENAME}"
    )
    artifact_path = output_dir / artifact_name
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_text(artifact_text, encoding="utf-8")

    result = {
        "backend_kind": "mock_command_tool",
        "instance_id": INSTANCE_ID,
        "job_id": os.getenv("TURNSTILE_JOB_ID") or None,
        "output_basename": OUTPUT_BASENAME,
        "request_echo": request,
        "artifact_names": [artifact_name],
    }
    (output_dir / "result.json").write_text(json.dumps(result, sort_keys=True), encoding="utf-8")
    print(json.dumps(result, sort_keys=True))

    if _is_true(request.get("fail")):
        print(json.dumps({"error": "forced failure", "instance_id": INSTANCE_ID}), file=sys.stderr)
        return 7
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
