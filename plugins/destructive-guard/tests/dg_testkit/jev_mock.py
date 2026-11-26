"""A local HTTP stand-in for a classifier backend: Jev or Clef (same request and answer shape).

The `jev` and `clef` fixtures (conftest.py) each start one."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Self


class JevMock:
    """Context manager: `.url`, `.answer(...)`, and the recorded `.bodies` / `.hits`."""

    HANG_MAX_SECONDS = 10

    @staticmethod
    def probs(unrelated: float, read_only: float, cloud_or_data_change: float, destructive: float) -> dict:
        return {
            "unrelated": unrelated,
            "read_only": read_only,
            "cloud_or_data_change": cloud_or_data_change,
            "destructive": destructive,
        }

    def __init__(self) -> None:
        self.bodies: list[dict | None] = []
        self.headers: list[dict[str, str]] = []
        self._answers: list[dict[str, Any]] = [self._build()]
        self._release = threading.Event()
        self._lock = threading.Lock()
        mock = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: Any) -> None:
                pass

            def do_POST(self) -> None:
                raw = self.rfile.read(int(self.headers.get("content-length") or 0))
                try:
                    body = json.loads(raw)
                except ValueError:
                    body = None
                with mock._lock:
                    mock.bodies.append(body)
                    mock.headers.append({k.lower(): v for k, v in self.headers.items()})
                    a = dict(mock._answers[min(len(mock.bodies), len(mock._answers)) - 1])
                if a["kind"] == "hang":
                    mock._release.wait(JevMock.HANG_MAX_SECONDS)
                    self.close_connection = True
                    return
                if a["kind"] == "status":
                    self.send_response(a["status"])
                    if a["retry_after"] is not None:
                        self.send_header("retry-after", a["retry_after"])
                    self.send_header("content-length", "0")
                    self.end_headers()
                    return
                if a["kind"] == "malformed":
                    payload = b"{"
                elif a["kind"] == "raw":
                    payload = json.dumps(a["body"]).encode()
                else:
                    payload = json.dumps(
                        {
                            "answers": {a["question"]: {"type": "choice", "probabilities": a["probs"]}},
                            "usage": a["usage"] or {"input_tokens": 1000, "output_tokens": 10},
                        }
                    ).encode()
                self.send_response(200)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        self._server.block_on_close = False
        self.url = f"http://127.0.0.1:{self._server.server_address[1]}/v1/systemone"
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def hits(self) -> int:
        return len(self.bodies)

    @staticmethod
    def _build(
        probs: dict | None = None,
        *,
        kind: str = "ok",
        status: int | None = None,
        question: str = "cloud_change",
        usage: dict | None = None,
        retry_after: str | None = None,
        body: dict | None = None,
    ) -> dict[str, Any]:
        if kind == "ok" and status is not None:
            kind = "status"
        if kind == "status" and status is None:
            raise ValueError("answer(kind='status') needs a status code")
        if kind == "raw" and body is None:
            raise ValueError("answer(kind='raw') needs a body")
        return {
            "kind": kind,
            "probs": probs or {},
            "status": status,
            "question": question,
            "usage": usage,
            "retry_after": retry_after,
            "body": body,
        }

    def answer(
        self,
        probs: dict | None = None,
        *,
        kind: str = "ok",
        status: int | None = None,
        question: str = "cloud_change",
        usage: dict | None = None,
        retry_after: str | None = None,
        body: dict | None = None,
    ) -> None:
        """Set the next answers and reset the recorded requests."""
        self.sequence(
            {
                "probs": probs,
                "kind": kind,
                "status": status,
                "question": question,
                "usage": usage,
                "retry_after": retry_after,
                "body": body,
            }
        )

    def sequence(self, *answers: dict) -> None:
        """Serve answers in order (each dict is `answer()` kwargs), the last repeating; reset recorded requests."""
        built = [self._build(**a) for a in answers]
        if not built:
            raise ValueError("sequence() needs at least one answer")
        with self._lock:
            self._answers = built
            self.bodies = []
            self.headers = []

    def __enter__(self) -> Self:
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._release.set()
        self._server.shutdown()
        self._server.server_close()
