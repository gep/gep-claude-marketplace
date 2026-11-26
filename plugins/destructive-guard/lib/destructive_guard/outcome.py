"""What the hook prints: a decision (`none`, `ask`, or `deny` only when halted) and an optional warning.

The Lean model's `HookOut` (formal/SimpleGate.lean). `HookOut.to_json` is the only place
that writes hook output for `PreToolUse`.

Lean model -> code:

| Lean                                       | Here                                                          |
| ------------------------------------------ | ------------------------------------------------------------- |
| `HookOut`                                  | `HookOut` (`decision`, `warning`; `reason` and texts on top)  |
| `HookDecision.none` / `.ask`               | `decision == "none"` / `"ask"`                                |
| `HookDecision.deny` (only with `noClassifier`) | `HookOut.halt`: `deny` + `continue: false` (A10)          |
| `HookDecision.allow`                       | not representable                                             |
| `judge c (.down w)`, `w.jevUnavailable`    | `HookOut.unavailable(*failures)`: the last failure's `Down`   |
| plugin faults (D12)                        | `HookOut.fault`                                               |
"""

import json
from dataclasses import dataclass
from typing import Literal

from .down import Down

# Display names of the backends in user-facing messages.
BACKEND_NAMES = {"clef": "Clef", "jev": "Jev"}


@dataclass(frozen=True)
class Failure:
    """One usable backend that gave no usable answer: its name, why, and the detail for the reason."""

    backend: str  # "clef" | "jev"
    kind: Down
    detail: str  # e.g. "http 503", "timeout after 1500 ms", "network error", "malformed answer"

    def __post_init__(self) -> None:
        if not self.kind.jev_unavailable:
            raise ValueError(f"{self.kind} is not a backend failure")

    @property
    def reason(self) -> str:
        """`<backend>-unavailable: <detail>`."""
        return f"{self.backend}-unavailable: {self.detail}"

    @property
    def text(self) -> str:
        """The failure for the warning, naming the backend."""
        name = BACKEND_NAMES.get(self.backend, self.backend)
        return {
            Down.TIMEOUT: f"{name} did not answer in time ({self.detail.removeprefix('timeout after ')})",
            Down.HTTP: f"{name} returned {self.detail}",
            Down.NETWORK: f"{name} could not be reached",
            Down.MALFORMED: f"{name}'s answer was malformed",
        }[self.kind]


@dataclass(frozen=True)
class HookOut:
    decision: Literal["none", "ask", "deny"]  # no "allow" in the type; "deny" only with NO_CLASSIFIER
    reason: str | None = None  # set iff decision is "ask" or "deny"
    warning: Down | None = None
    detail: str | None = None  # e.g. "http 503", "timeout after 1500 ms"; the halt's issue list
    failures: tuple[Failure, ...] = ()  # the backends that did not answer, in the order tried

    OFF_SWITCH = "GATE_ENABLED=0 turns the gate off."

    def __post_init__(self) -> None:
        if (self.decision in ("ask", "deny")) != (self.reason is not None):
            raise ValueError("a reason comes with ask or deny, and only with them")
        if (self.decision == "deny") != (self.warning is Down.NO_CLASSIFIER):
            raise ValueError("deny comes with the noClassifier warning, and only with it")
        if self.warning is not None and self.decision == "ask" and not self.warning.asks:
            raise ValueError(f"warning {self.warning} must not come with ask")
        if self.warning is not None and self.decision == "none" and self.warning.asks:
            raise ValueError(f"warning {self.warning} must come with ask")

    @classmethod
    def fault(cls, kind: Down, detail: str | None = None) -> HookOut:
        """A plugin fault (D12): ask with reason `plugin-fault: <text>` and a warning."""
        if not kind.plugin_fault:
            raise ValueError(f"{kind} is not a plugin fault")
        text = {
            Down.BAD_INPUT: "bad hook input",
            Down.PYTHON_MISSING: "no Python >= 3.14 found (set GATE_INFRA_PYTHON)",
            Down.INTERNAL_ERROR: f"internal error: {detail or 'unknown'}",
        }[kind]
        return cls("ask", f"plugin-fault: {text}", kind, detail)

    @classmethod
    def unavailable(cls, *failures: Failure) -> HookOut:
        """Every usable backend was tried and none answered (D11, D15): ask with one
        `<backend>-unavailable: <detail>` per backend joined by `; `; the warning is the last one's (model)."""
        if not failures:
            raise ValueError("unavailable needs at least one failure")
        last = failures[-1]
        return cls("ask", "; ".join(f.reason for f in failures), last.kind, last.detail, tuple(failures))

    @classmethod
    def halt(cls, issues: list[str]) -> HookOut:
        """No usable backend (D17): deny the call and stop Claude. `issues` names each backend's problem
        (`clef: disabled by GATE_CLEF_ENABLED=0`, `jev: TYPESAFE_API_KEY is not set`)."""
        listed = "; ".join(issues)
        return cls("deny", f"no-classifier: {listed}", Down.NO_CLASSIFIER, listed)

    def message(self) -> str | None:
        """The `systemMessage` text, or None without a warning."""
        w = self.warning
        if w is None:
            return None
        d = self.detail or ""
        if w is Down.NO_CLASSIFIER:
            return (
                f"destructive-guard: no classifier available ({d}): this session is halted. "
                "Fix the setting or set GATE_ENABLED=0."
            )
        if w is Down.ENV_REFERENCE:
            return "destructive-guard: call mentions a .env file and was not sent to Jev; Claude Code's permissions apply"
        if w.jev_unavailable:
            failures = self.failures or (Failure("jev", w, d),)
            text = "; ".join(f.text for f in failures)
        else:
            text = {
                Down.BAD_INPUT: "could not read the hook input",
                Down.PYTHON_MISSING: "no Python >= 3.14 found (set GATE_INFRA_PYTHON)",
                Down.INTERNAL_ERROR: f"internal error ({d or 'unknown'})",
            }[w]
        return f"destructive-guard: {text}; asking you instead. {self.OFF_SWITCH}"

    def to_json(self) -> str | None:
        """ask/deny -> permissionDecision; deny also stops Claude (`continue: false`, `stopReason`);
        a warning -> systemMessage; nothing -> None (no output)."""
        out: dict = {}
        message = self.message()
        if self.decision == "deny":
            out["continue"] = False
            out["stopReason"] = message
        if self.decision in ("ask", "deny"):
            out["hookSpecificOutput"] = {
                "hookEventName": "PreToolUse",
                "permissionDecision": self.decision,
                "permissionDecisionReason": self.reason,
            }
        if message is not None:
            out["systemMessage"] = message
        if not out:
            return None
        return json.dumps(out, ensure_ascii=False, separators=(",", ":"))
