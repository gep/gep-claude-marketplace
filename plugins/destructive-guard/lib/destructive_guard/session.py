"""SessionStart hook (the Lean model's `sessionStart`): warn once about a misconfigured setting (D3, D17).

Never contacts a backend. Silent when everything is fine or the gate is off.

Lean model (formal/SimpleGate.lean) -> code:

| Lean                                   | Here                                                              |
| -------------------------------------- | ----------------------------------------------------------------- |
| `sessionStart s`                       | `SessionCheck.message` / `SessionCheck.output` (via `main`)       |
| `issues s` (backends that are `bad`)   | `Backends.issues`: each skipped backend with its labelled error   |
| `none` (no issue, a backend usable)    | `message` returns None: no output                                 |
| `SessionOut.halt` (`usable s = []`)    | `Halt.text(issues)`, the hook's text, plus `continue: false` and `stopReason` |

A bad `GATE_*` flag leaves nothing usable, so it halts (same as the hook, via `Halt.of_env`). A halted
`SessionStart` cannot stop the session (A8); `continue`/`stopReason` are harmless where ignored. The
prompt gate and the hook do the blocking.

Outside the model (notices only, they never change a decision): the removed 0.8 variables and a
GATE_LOG path that cannot be written. They join the message of a non-halted session; a halt prints only
its own text.
"""

import json
import os
import sys
from collections.abc import Mapping
from typing import Any, ClassVar

from .config import GateConfig
from .errors import ConfigError
from .halt import Halt
from .log import GateLog


class SessionCheck:
    # Variables of 0.8 that 1.0 ignores.
    REMOVED: ClassVar[list[str]] = [
        "GATE_CHECKS",
        "GATE_RULES_FILE",
        "GATE_RULES",
        "GATE_DISABLE_RULES",
        "GATE_STRICT",
        "GATE_JEV",
        "GATE_JEV_ALLOW_THRESHOLD",
        "GATE_JEV_CAN_LIFT",
        "GATE_JEV_SCOPE",
        "GATE_JEV_LOG",
    ]
    PREFIX = "destructive-guard: "

    @staticmethod
    def enabled(env: Mapping[str, str]) -> bool:
        try:
            return GateConfig._flag(env, "GATE_ENABLED", True)
        except ConfigError:
            return True  # unreadable: the gate stays on and the halt names the flag

    @classmethod
    def output(cls, env: Mapping[str, str]) -> dict[str, Any] | None:
        """The hook output (`systemMessage`, plus `continue`/`stopReason` when halted), or None when silent."""
        if not cls.enabled(env):
            return None
        state = Halt.of_env(env)
        if state.halted:
            text = Halt.text(state.issues)
            return {"continue": False, "stopReason": text, "systemMessage": text}
        parts: list[str] = []
        if state.backends is not None:
            parts += [f"{error} (skipped this session)" for _, error in state.backends.issues]
        removed = [name for name in cls.REMOVED if (env.get(name) or "").strip()]
        if removed:
            parts.append(f"ignoring the removed 0.8 variables {', '.join(removed)}")
        log = GateConfig.log_path(env)
        log_problem = GateLog.problem(log)
        if log_problem is not None:
            parts.append(f"cannot write GATE_LOG {log} ({log_problem}): no log this session")
        if not parts:
            return None
        return {"systemMessage": cls.PREFIX + "; ".join(parts)}

    @classmethod
    def message(cls, env: Mapping[str, str]) -> str | None:
        """The one `systemMessage`, or None when there is nothing to say."""
        out = cls.output(env)
        return None if out is None else out["systemMessage"]

    @classmethod
    def main(cls, env: Mapping[str, str] | None = None) -> int:
        env = os.environ if env is None else env
        try:
            out = cls.output(env)
        except Exception:  # noqa: BLE001 - a session warning must never break the session
            return 0
        if out is not None:
            sys.stdout.write(json.dumps(out, ensure_ascii=False, separators=(",", ":")))
            sys.stdout.flush()
        return 0


def main(env: Mapping[str, str] | None = None) -> int:
    return SessionCheck.main(env)
