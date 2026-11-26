"""PreToolUse hook (the Lean model's `hook`): one Bash or MCP call in, ask, nothing or (halted) deny out.

The call goes (redacted) to the usable backends in order, Clef first, Jev second (D14); the first
answer decides: a risky answer asks, a safe or uncertain one gives no decision, so Claude Code's own
permissions apply. A backend that does not answer falls through to the next (D15); all down asks
with a warning (D11). Plugin faults (D12) ask with a warning. A misconfigured backend is skipped
silently (`SessionStart` names it). No usable backend at all halts (D17): `deny` + `continue: false`,
checked before the `.env` skip. Never `allow`.

Lean model (formal/SimpleGate.lean) -> code; tests/test_lean_conformance.py checks the examples:

| Lean                                  | Here                                                             |
| ------------------------------------- | ---------------------------------------------------------------- |
| `hook`                                | `GateHook.main` (input, setting) and `GateHook.decide` (call)    |
| `Call.tool p`                         | `GateHook.call_of`                                               |
| `Call.badInput`, `Call.crash`         | `HookOut.fault(BAD_INPUT / INTERNAL_ERROR, ...)` in `main`       |
| `Call.pythonMissing`                  | `hooks/gate.sh` (Python lookup fails; never reaches this file)   |
| `Setting`, `usable s`                 | `Halt.of_env(env).usable` (a bad GATE_* flag: nothing usable)     |
| `usable s = []` -> `deny, noClassifier` | `HookOut.halt(Halt.of_env(env).issues)` in `main`, before `.env`  |
| `e.mentionsEnv`, `e.redact`           | `JevRequest.build_state` (None means `.env`)                     |
| `q b` (the request to backend `b`)    | `JevClient.ask_jev(state, cfg)` with that backend's `cfg`        |
| `consult q p (usable s)`              | the loop in `GateHook.decide` (first answer, else all failures)  |
| `judge c (.answer r)`                 | `JevJudge.judge` (reason names the backend)                      |
| `judge c (.down w)` (last `w`)        | `HookOut.unavailable(*failures)` (warning = last failure's kind) |
| `HookOut`, `Down`                     | `outcome.HookOut`, `outcome.Down`                                |
"""

import json
import os
import sys
import time
from collections.abc import Mapping, Sequence
from typing import Any, BinaryIO

from .errors import ConfigError
from .halt import Halt
from .jev.client import JevClient
from .jev.judge import JevJudge
from .jev.request import JevRequest
from .log import GateLog
from .outcome import Down, Failure, HookOut


class GateHook:
    """Reads one hook input from stdin, decides it and writes the output to stdout."""

    @staticmethod
    def _reject_constant(name: str) -> Any:
        # JSON.parse refuses NaN and Infinity; Python's json accepts them by default.
        raise ValueError(f"invalid JSON constant {name}")

    @classmethod
    def parse(cls, stdin: BinaryIO) -> dict:
        """The hook input: a JSON object with a string `tool_name`. Raises ValueError or TypeError otherwise."""
        text = stdin.read().decode("utf-8", errors="replace")
        data = json.loads(text, parse_constant=cls._reject_constant)
        if not isinstance(data, dict) or not isinstance(data.get("tool_name"), str):
            raise TypeError("not a hook input")
        return data

    @staticmethod
    def call_of(data: Mapping[str, Any]) -> dict | None:
        """A Bash call with a string command, or an MCP call; anything else needs no decision."""
        tool = data["tool_name"]
        tool_input = data.get("tool_input")
        if tool == "Bash":
            command = tool_input.get("command") if isinstance(tool_input, dict) else None
            return {"tool": "Bash", "command": command} if isinstance(command, str) else None
        if tool.startswith("mcp__"):
            return {"tool": tool, "input": tool_input if tool_input is not None else {}}
        return None

    @staticmethod
    def write(out: HookOut) -> None:
        text = out.to_json()
        if text is not None:
            sys.stdout.buffer.write(text.encode("utf-8", errors="replace"))
            sys.stdout.buffer.flush()

    @classmethod
    def decide(
        cls, call: Mapping[str, Any], cfgs: Mapping[str, Any] | Sequence[Mapping[str, Any]], record: dict[str, Any]
    ) -> HookOut:
        """Call -> the usable backends in order (one settings dict or a non-empty list, Clef first) -> output.
        Fills `record` for GATE_LOG: `backend` (who answered, or None) and `fallback` (the ones that did not)."""
        chain = [cfgs] if isinstance(cfgs, Mapping) else list(cfgs)
        if not chain:
            raise ValueError("decide needs a usable backend (no backend usable is the halt)")
        fallback: list[dict[str, Any]] = []
        record.update(backend=None, fallback=fallback)
        state = JevRequest.build_state(call)
        if state is None:
            record.update(command=None, input=None, decision="none", warning=Down.ENV_REFERENCE.value)
            return HookOut("none", warning=Down.ENV_REFERENCE)
        record["command" if call["tool"] == "Bash" else "input"] = state.get("command", state.get("input"))
        failures: list[Failure] = []
        total_ms = 0
        for cfg in chain:
            result = JevClient().ask_jev(state, cfg)
            total_ms += result.get("latencyMs") or 0
            probs = result.get("probabilities")
            if not probs:
                error = str(result.get("error") or "network")
                failures.append(JevJudge.failure(error, cfg))
                fallback.append({"backend": JevJudge.backend_of(cfg), "error": error, "latencyMs": result.get("latencyMs")})
                continue
            prompt = JevRequest._prompt_of(cfg)
            record.update(
                backend=JevJudge.backend_of(cfg),
                probabilities=probs,
                risk=JevJudge.risk(probs, prompt),
                usage=result.get("usage"),
                latencyMs=total_ms,
            )
            return JevJudge.judge(result, cfg)
        record.update(probabilities=None, risk=None, usage=None, latencyMs=total_ms)
        return HookOut.unavailable(*failures)

    @classmethod
    def main(cls, stdin: BinaryIO | None = None, env: Mapping[str, str] | None = None) -> int:
        from .config import GateConfig

        stdin = sys.stdin.buffer if stdin is None else stdin
        env = os.environ if env is None else env
        record: dict[str, Any] = {}
        log_path: str | None = None
        out: HookOut | None = None
        try:
            log_path = GateConfig.log_path(env)
            usable: list[dict[str, Any]] = []
            try:
                enabled = GateConfig._flag(env, "GATE_ENABLED", True)
            except ConfigError:
                enabled = True  # unreadable: the gate stays on and Backends.read below reports it
            if not enabled:
                return 0
            # A GATE_* flag the gate cannot read leaves nothing usable: halted (see halt.py).
            state = Halt.of_env(env)
            usable, issues = state.usable, state.issues
            try:
                data = cls.parse(stdin)
            except Exception:  # noqa: BLE001 - any unreadable input is a plugin fault
                out = HookOut.fault(Down.BAD_INPUT, "bad hook input")
            else:
                record["tool"] = data["tool_name"]
                call = cls.call_of(data)
                if call is None:
                    return 0
                if not usable:
                    # S12: halted; before the .env skip, nothing is sent.
                    out = HookOut.halt(issues)
                else:
                    # S9: a bad backend is skipped silently; the session warning names it.
                    t0 = time.monotonic()
                    out = cls.decide(call, usable, record)
                    record.setdefault("latencyMs", round((time.monotonic() - t0) * 1000))
        except Exception as e:  # noqa: BLE001
            out = HookOut.fault(Down.INTERNAL_ERROR, type(e).__name__)
        record.update(
            decision=out.decision,
            reason=out.reason,
            warning=out.warning.value if out.warning else None,
        )
        cls.write(out)
        GateLog.write(log_path, record)
        return 0

def main(stdin: BinaryIO | None = None, env: Mapping[str, str] | None = None) -> int:
    return GateHook.main(stdin, env)
