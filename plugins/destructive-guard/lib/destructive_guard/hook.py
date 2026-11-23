"""PreToolUse hook (the Lean model's `hook`): one Bash or MCP call in, ask or nothing out.

The call goes to Jev (redacted); a risky answer asks, a safe or uncertain one gives no decision, so
Claude Code's own permissions apply. It never prints allow or deny. Jev unavailable (D11) and plugin
faults (D12) ask with a warning. A misconfigured setting is silent here: `SessionStart` warns once.

Lean model (formal/SimpleGate.lean) -> code; tests/test_lean_conformance.py checks the examples:

| Lean                                  | Here                                                           |
| ------------------------------------- | -------------------------------------------------------------- |
| `hook`                                | `GateHook.main` (input, setting) and `GateHook.decide` (call)  |
| `Call.tool p`                         | `GateHook.call_of`                                             |
| `Call.badInput`, `Call.crash`         | `HookOut.fault(BAD_INPUT / INTERNAL_ERROR, ...)` in `main`     |
| `Call.pythonMissing`                  | `hooks/gate.sh` (Python lookup fails; never reaches this file) |
| `e.mentionsEnv`, `e.redact`           | `JevRequest.build_state` (None means `.env`)                   |
| `q .jev` (the Jev request)            | `JevClient.ask_jev`                                            |
| `judge c (consult ...)`               | `JevJudge.judge` (Jev only; `consult` over Clef comes in C3)   |
| `Setting` with `jev := .bad _`        | `JevConfigError` in `main`: silent, exit 0 (the model halts: C3) |
| `HookOut`, `Down`                     | `outcome.HookOut`, `outcome.Down`                              |
"""

import json
import os
import sys
import time
from collections.abc import Mapping
from typing import Any, BinaryIO

from .errors import ConfigError, JevConfigError
from .jev.client import JevClient
from .jev.judge import JevJudge
from .jev.request import JevRequest
from .log import GateLog
from .outcome import Down, HookOut


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
    def decide(cls, call: Mapping[str, Any], cfg: Mapping[str, Any], record: dict[str, Any]) -> HookOut:
        """Call -> Jev -> output. Fills `record` for GATE_LOG."""
        state = JevRequest.build_state(call)
        if state is None:
            record.update(command=None, input=None, decision="none", warning=Down.ENV_REFERENCE.value)
            return HookOut("none", warning=Down.ENV_REFERENCE)
        record["command" if call["tool"] == "Bash" else "input"] = state.get("command", state.get("input"))
        result = JevClient().ask_jev(state, cfg)
        out = JevJudge.judge(result, cfg)
        probs = result.get("probabilities")
        prompt = JevRequest._prompt_of(cfg)
        record.update(
            probabilities=probs,
            risk=JevJudge.risk(probs, prompt) if probs else None,
            usage=result.get("usage"),
            latencyMs=result.get("latencyMs"),
        )
        return out

    @classmethod
    def main(cls, stdin: BinaryIO | None = None, env: Mapping[str, str] | None = None) -> int:
        from .config import GateConfig
        from .jev.config import JevConfig

        stdin = sys.stdin.buffer if stdin is None else stdin
        env = os.environ if env is None else env
        record: dict[str, Any] = {}
        log_path: str | None = None
        out: HookOut | None = None
        try:
            log_path = GateConfig.log_path(env)
            try:
                gate = GateConfig.read(env)
                if not gate["enabled"]:
                    return 0
                setting_error: Exception | None = None
            except ConfigError as e:
                setting_error = e
            try:
                data = cls.parse(stdin)
            except Exception:  # noqa: BLE001 - any unreadable input is a plugin fault
                out = HookOut.fault(Down.BAD_INPUT, "bad hook input")
            else:
                record["tool"] = data["tool_name"]
                call = cls.call_of(data)
                if call is None:
                    return 0
                try:
                    if setting_error is not None:
                        raise JevConfigError(str(setting_error), "badValue")
                    cfg = JevConfig.read(env)
                except JevConfigError as e:
                    # S9: silent per call; the session warning says what is wrong.
                    GateLog.write(log_path, {**record, "decision": "none", "warning": None, "misconfigured": e.kind})
                    return 0
                t0 = time.monotonic()
                out = cls.decide(call, cfg, record)
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
