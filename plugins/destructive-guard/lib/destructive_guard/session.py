"""SessionStart hook (the Lean model's `sessionStart`): warn once about a misconfigured setting (D3).

Never contacts Jev. Silent when everything is fine or the gate is off.

Lean model (formal/SimpleGate.lean) -> code:

| Lean                         | Here                                                          |
| ---------------------------- | ------------------------------------------------------------- |
| `sessionStart`               | `SessionCheck.message` (via `SessionCheck.main`)              |
| `issues s` (Jev `bad w`)     | `SessionCheck.issue` returns a `JevConfigError` / `ConfigError` |
| no issue, Jev `ok`           | `SessionCheck.issue` returns None: no output                  |

The model's halt (`SessionOut.halt`, `promptGate`) and Clef are not implemented yet (C4).

Outside the model (notices only, they never change a decision): the removed 0.8 variables and a
GATE_LOG path that cannot be written. They join the same message.
"""

import json
import os
import sys
from collections.abc import Mapping
from typing import ClassVar

from .config import GateConfig
from .errors import ConfigError, JevConfigError
from .jev.config import JevConfig
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

    @classmethod
    def issue(cls, env: Mapping[str, str]) -> JevConfigError | ConfigError | None:
        """What is wrong with the setting, or None."""
        try:
            GateConfig.read(env)
        except ConfigError as e:
            return e
        try:
            JevConfig.read(env)
        except JevConfigError as e:
            return e
        return None

    @classmethod
    def message(cls, env: Mapping[str, str]) -> str | None:
        """The one `systemMessage`, or None when there is nothing to say."""
        try:
            if not GateConfig.read(env)["enabled"]:
                return None
        except ConfigError:
            pass  # a bad GATE_ENABLED value is reported below
        parts: list[str] = []
        problem = cls.issue(env)
        if problem is not None:
            parts.append(f"{problem}: destructive-guard only blocks .env files this session")
        removed = [name for name in cls.REMOVED if (env.get(name) or "").strip()]
        if removed:
            parts.append(f"ignoring the removed 0.8 variables {', '.join(removed)}")
        log = GateConfig.log_path(env)
        log_problem = GateLog.problem(log)
        if log_problem is not None:
            parts.append(f"cannot write GATE_LOG {log} ({log_problem}): no log this session")
        if not parts:
            return None
        return cls.PREFIX + "; ".join(parts)

    @classmethod
    def main(cls, env: Mapping[str, str] | None = None) -> int:
        env = os.environ if env is None else env
        try:
            message = cls.message(env)
        except Exception:  # noqa: BLE001 - a session warning must never break the session
            return 0
        if message is not None:
            sys.stdout.write(json.dumps({"systemMessage": message}, ensure_ascii=False, separators=(",", ":")))
            sys.stdout.flush()
        return 0


def main(env: Mapping[str, str] | None = None) -> int:
    return SessionCheck.main(env)
