"""The classifier HTTP client (Clef and Jev share the request and answer shape): one call within an
overall time budget, with retries. `Authorization` is sent only when the backend has a key."""

import json
import math
import random
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

from .request import JevRequest


class JevClient:
    """Calls one backend (Clef or Jev); `now` and `sleep` are injectable for tests."""

    def __init__(
        self,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.now = now
        self.sleep = sleep

    def retry_delay(self, attempt: int, retry_after: str | None) -> float:
        """Milliseconds to wait before the next attempt: `retry-after` (seconds or HTTP-date), else backoff."""
        if retry_after:
            try:
                seconds = float(retry_after)
            except ValueError:
                seconds = None
                try:
                    when = parsedate_to_datetime(retry_after)
                    if when.tzinfo is None:
                        when = when.replace(tzinfo=UTC)
                    seconds = max(0.0, (when - self.now()).total_seconds())
                except (TypeError, ValueError, IndexError):
                    pass
            if seconds is not None and math.isfinite(seconds) and seconds >= 0:
                return seconds * 1000
        return 100 * 2 ** (attempt - 1) + random.randrange(50)

    @staticmethod
    def _is_timeout(e: BaseException) -> bool:
        reason = getattr(e, "reason", None)
        return isinstance(e, TimeoutError) or isinstance(reason, TimeoutError)

    def ask_jev(
        self,
        state: dict,
        cfg: Mapping[str, Any],
        *,
        opener: Callable[..., Any] = urllib.request.urlopen,
        clock: Callable[[], float] = time.monotonic,
    ) -> dict:
        """Calls Jev within one overall time budget (retries included). Never raises. Returns
        {probabilities, usage, latencyMs} or {error, latencyMs}; latencyMs covers all attempts and `usage`
        is the token usage Jev reports, or None."""
        started = clock()
        deadline = started + cfg["timeoutMs"] / 1000

        def elapsed() -> int:
            return round((clock() - started) * 1000)

        def fail(error: str) -> dict:
            return {"error": error, "latencyMs": elapsed()}

        try:
            prompt = JevRequest._prompt_of(cfg)
            body = JevRequest._dumps(JevRequest.request_body(state, cfg["model"], prompt)).encode("utf-8", "replace")
            headers_out = {"content-type": "application/json"}
            if cfg.get("apiKey"):
                headers_out["authorization"] = f"Bearer {cfg['apiKey']}"
            attempt = 0
            while True:
                attempt += 1
                remaining = deadline - clock()
                if remaining <= 0:
                    return fail("timeout")
                request = urllib.request.Request(cfg["url"], data=body, method="POST", headers=headers_out)
                status = 0
                headers: Any = None
                raw = b""
                try:
                    res = opener(request, timeout=remaining)
                    try:
                        status = res.status
                        headers = res.headers
                        if 200 <= status < 300:
                            raw = res.read()
                    finally:
                        close = getattr(res, "close", None)
                        if close:
                            close()
                except urllib.error.HTTPError as e:
                    status, headers = e.code, e.headers
                    e.close()
                except Exception as e:  # noqa: BLE001 - ask_jev never raises
                    if self._is_timeout(e) or clock() >= deadline:
                        return fail("timeout")
                    # An unreadable or cut-off body of an answered request is not a network failure.
                    return fail("malformed" if status else "network")
                if status in (429, 529):
                    header = headers.get("retry-after") if headers is not None else None
                    delay = self.retry_delay(attempt, header)
                    if clock() + delay / 1000 >= deadline:
                        return fail(f"http {status}")
                    self.sleep(delay / 1000)
                    continue
                if not 200 <= status < 300:
                    return fail(f"http {status}")
                try:
                    parsed = json.loads(raw)
                except ValueError:
                    return fail("timeout" if clock() >= deadline else "malformed")
                probabilities = JevRequest.parse_answer(parsed, prompt)
                if not probabilities:
                    return fail("malformed")
                return {
                    "probabilities": probabilities,
                    "usage": JevRequest._get(parsed, "usage"),
                    "latencyMs": elapsed(),
                }
        except Exception:  # noqa: BLE001
            return fail("network")
