"""UserPromptSubmit hook (the Lean model's `promptGate`): reject every prompt while the session is halted (D17).

Never contacts a backend and never reads stdin: the model's `promptGate s` depends only on the setting.
A plugin fault (any exception here) prints nothing: the prompt is never blocked because of a fault.

Lean model (formal/SimpleGate.lean) -> code:

| Lean                       | Here                                                                   |
| -------------------------- | ---------------------------------------------------------------------- |
| `promptGate s = true`      | `PromptGate.output` returns `{"decision":"block","reason":<halt text>}` |
| `promptGate s = false`     | `PromptGate.output` returns None: no output                            |
| `usable s = []`            | `Halt.of_env(env).halted`, the same decision as hook and session start |
"""

import json
import os
import sys
from collections.abc import Mapping
from typing import Any

from .halt import Halt
from .session import SessionCheck


class PromptGate:
    @staticmethod
    def output(env: Mapping[str, str]) -> dict[str, Any] | None:
        if not SessionCheck.enabled(env):
            return None
        state = Halt.of_env(env)
        if not state.halted:
            return None
        return {"decision": "block", "reason": Halt.text(state.issues)}

    @classmethod
    def main(cls, env: Mapping[str, str] | None = None) -> int:
        env = os.environ if env is None else env
        try:
            out = cls.output(env)
        except Exception:  # noqa: BLE001 - a plugin fault never blocks a prompt
            return 0
        if out is not None:
            sys.stdout.write(json.dumps(out, ensure_ascii=False, separators=(",", ":")))
            sys.stdout.flush()
        return 0


def main(env: Mapping[str, str] | None = None) -> int:
    return PromptGate.main(env)
