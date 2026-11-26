"""When is the session halted (D17), and what does the halt say: one place for hook, session start and prompt gate.

Lean model (formal/SimpleGate.lean) -> code:

| Lean                         | Here                                                           |
| ---------------------------- | -------------------------------------------------------------- |
| `usable s = []`              | `Halt.of_env(env).halted` (no usable backend, or a bad flag)   |
| the halt's issue list        | `Halt.issues` (`clef: disabled by GATE_CLEF_ENABLED=0`, ...)   |
| the halt text (`noClassifier`) | `Halt.text(issues)`                                          |

A `GATE_*` flag the gate cannot read leaves nothing usable, so it halts too (the issue is the flag's error).
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .errors import ConfigError


@dataclass(frozen=True)
class Halt:
    backends: Any  # jev.backends.Backends, or None when a flag could not be read
    usable: list[dict[str, Any]]
    issues: list[str]  # why each backend is not usable, in order

    @property
    def halted(self) -> bool:
        return not self.usable

    @staticmethod
    def text(issues: list[str]) -> str:
        """The one halt text (hook `systemMessage`, session start, prompt gate reason)."""
        return (
            f"destructive-guard: no classifier available ({'; '.join(issues)}): this session is halted. "
            "Fix the setting or set GATE_ENABLED=0."
        )

    @staticmethod
    def issues_of(backends: Any) -> list[str]:
        """Why each backend is not usable, in order: `clef: disabled by GATE_CLEF_ENABLED=0`, or its config error."""
        issues = []
        for name in backends.ORDER:
            st = backends.state(name)
            if st.kind == "off":
                issues.append(f"{name}: disabled by GATE_{name.upper()}_ENABLED=0")
            elif st.kind == "bad":
                issues.append(str(st.error))
        return issues

    @classmethod
    def of_env(cls, env: Mapping[str, str], *, cwd: str | None = None) -> Halt:
        from .jev.backends import Backends

        try:
            backends = Backends.read(env, cwd=cwd)
        except ConfigError as e:
            return cls(None, [], [str(e)])
        return cls(backends, backends.usable, cls.issues_of(backends))
