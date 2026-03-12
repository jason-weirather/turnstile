import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

INSTANCE_ID = os.getenv("MOCK_INSTANCE_ID", "mock-http-tool")
DEFAULT_SLEEP_S = float(os.getenv("MOCK_DEFAULT_SLEEP_S", "0"))
RESPONSE_PREFIX = os.getenv("MOCK_RESPONSE_PREFIX", "")
PORT = int(os.getenv("MOCK_PORT", "8000"))
TAG_FIELDS = {
    key.removeprefix("MOCK_TAG_").lower(): value
    for key, value in os.environ.items()
    if key.startswith("MOCK_TAG_")
}
_CANCELLED_JOB_IDS: set[str] = set()
_CANCEL_LOCK = threading.Lock()


def _coerce_sleep(value: Any) -> float:
    if value is None:
        return DEFAULT_SLEEP_S
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return DEFAULT_SLEEP_S


def _is_true(value: Any) -> bool:
    return value is True or str(value).lower() in {"1", "true", "yes", "on"}


def _mark_cancelled(job_id: str) -> None:
    if not job_id:
        return
    with _CANCEL_LOCK:
        _CANCELLED_JOB_IDS.add(job_id)


def _is_cancelled(job_id: str) -> bool:
    if not job_id:
        return False
    with _CANCEL_LOCK:
        return job_id in _CANCELLED_JOB_IDS


class Handler(BaseHTTPRequestHandler):
    server_version = "mock_http_tool/1.0"

    def log_message(self, format: str, *args: object) -> None:
        del format, args

    def do_GET(self) -> None:
        if self.path != "/healthz":
            self._send_json(404, {"error": "not_found"})
            return
        self._send_json(
            200,
            {
                "status": "ok",
                "backend_kind": "mock_http_tool",
                "instance_id": INSTANCE_ID,
                "response_prefix": RESPONSE_PREFIX,
                "tags": TAG_FIELDS,
            },
        )

    def do_POST(self) -> None:
        payload = self._read_json()
        if self.path == "/invoke":
            self._handle_invoke(payload)
            return
        if self.path == "/cancel":
            self._handle_cancel(payload)
            return
        self._send_json(404, {"error": "not_found"})

    def _handle_invoke(self, payload: dict[str, Any]) -> None:
        job_id = self.headers.get("X-Turnstile-Job-Id", "")
        sleep_s = _coerce_sleep(payload.get("sleep_s"))
        if sleep_s > 0:
            deadline = time.monotonic() + sleep_s
            while time.monotonic() < deadline:
                if _is_cancelled(job_id):
                    self._send_json(
                        409,
                        {
                            "backend_kind": "mock_http_tool",
                            "instance_id": INSTANCE_ID,
                            "job_id": job_id or None,
                            "status": "cancelled",
                            "response_prefix": RESPONSE_PREFIX,
                            "request_echo": payload,
                            "tags": TAG_FIELDS,
                        },
                    )
                    return
                time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))

        text_value = payload.get("text") or payload.get("prompt") or payload.get("payload")
        response = {
            "backend_kind": "mock_http_tool",
            "instance_id": INSTANCE_ID,
            "job_id": job_id or None,
            "response_prefix": RESPONSE_PREFIX,
            "response_text": f"{RESPONSE_PREFIX}{text_value}"
            if text_value is not None
            else RESPONSE_PREFIX,
            "request_echo": payload,
            "tags": TAG_FIELDS,
        }
        if _is_true(payload.get("fail")):
            response["status"] = "failed"
            response["error"] = "forced failure"
            self._send_json(500, response)
            return
        response["status"] = "ok"
        self._send_json(200, response)

    def _handle_cancel(self, payload: dict[str, Any]) -> None:
        job_id = str(payload.get("job_id", "")).strip()
        _mark_cancelled(job_id)
        self._send_json(
            202,
            {
                "backend_kind": "mock_http_tool",
                "instance_id": INSTANCE_ID,
                "job_id": job_id or None,
                "status": "cancelling",
                "response_prefix": RESPONSE_PREFIX,
                "tags": TAG_FIELDS,
            },
        )

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length > 0 else b"{}"
        try:
            parsed = json.loads(raw or b"{}")
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {"value": parsed}

    def _send_json(self, status_code: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, sort_keys=True).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    server.serve_forever()
