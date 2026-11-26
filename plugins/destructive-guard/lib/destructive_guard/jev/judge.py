"""A backend's (Clef's or Jev's) answer -> hook output (the Lean model's `judge`).

Lean model (formal/SimpleGate.lean) -> code:

| Lean                                   | Here                                                         |
| -------------------------------------- | ------------------------------------------------------------ |
| `judge c`                              | `JevJudge.judge`                                             |
| `JevResult.answer risk`                | a result with `probabilities`; `risk` is the sum of `ask`    |
| `c.escalate < risk` -> `ask`           | `risk > cfg["escalateThreshold"]` -> `HookOut("ask", ...)`   |
| otherwise -> no decision               | `HookOut("none")`                                            |
| `JevResult.down why`                   | no `probabilities`; `JevJudge.failure` picks `Down`          |
| `Down.timeout/http/network/malformed`  | `Down.TIMEOUT/HTTP/NETWORK/MALFORMED` -> ask + warning (D11) |

The reason names the backend that answered (`cfg["backend"]`, `jev` when unset): `clef: <choice> p=… risk=…`
or `jev: …`. The chain (`consult`) is in `hook.GateHook.decide`.
"""

from collections.abc import Mapping
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from ..outcome import Down, Failure, HookOut
from .request import JevRequest


class JevJudge:
    """Risky answer -> ask; safe or uncertain answer -> no decision; backend down -> ask with a warning (D11)."""

    @staticmethod
    def _fmt(p: float) -> str:
        """JS `toFixed(2)`: ties round up."""
        return str(Decimal(p).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))

    @staticmethod
    def _top_of(probs: Mapping[str, float], choices: list[str]) -> str:
        """Ties go to the choice listed last (the stronger one in the built-in prompts)."""
        best = choices[0]
        for c in choices[1:]:
            if probs[c] >= probs[best]:
                best = c
        return best

    @staticmethod
    def risk(probs: Mapping[str, float], prompt: Mapping[str, Any]) -> float:
        """The sum of the prompt's `ask` choices."""
        n = 0.0
        for c in prompt["ask"]:
            n += probs[c]
        return n

    @classmethod
    def top(cls, probs: Mapping[str, float], prompt: Mapping[str, Any]) -> str:
        return cls._top_of(probs, prompt["ask"])

    @staticmethod
    def backend_of(cfg: Mapping[str, Any]) -> str:
        return str(cfg.get("backend") or "jev")

    @classmethod
    def failure(cls, error: str, cfg: Mapping[str, Any]) -> Failure:
        """A client error (`timeout`, `http 503`, `network`, `malformed`) -> the backend's `Failure`."""
        backend = cls.backend_of(cfg)
        if error == "timeout":
            return Failure(backend, Down.TIMEOUT, f"timeout after {cfg['timeoutMs']} ms")
        if error.startswith("http "):
            return Failure(backend, Down.HTTP, error)
        if error == "malformed":
            return Failure(backend, Down.MALFORMED, "malformed answer")
        return Failure(backend, Down.NETWORK, "network error")

    @classmethod
    def judge(cls, result: Mapping[str, Any], cfg: Mapping[str, Any]) -> HookOut:
        """One backend's result -> hook output (a down result alone: `HookOut.unavailable` of that backend)."""
        probs = result.get("probabilities")
        if not probs:
            return HookOut.unavailable(cls.failure(str(result.get("error") or "network"), cfg))
        prompt = JevRequest._prompt_of(cfg)
        risk = cls.risk(probs, prompt)
        if risk > cfg["escalateThreshold"]:
            top = cls.top(probs, prompt)
            return HookOut("ask", f"{cls.backend_of(cfg)}: {top} p={cls._fmt(probs[top])} risk={cls._fmt(risk)}")
        return HookOut("none")
