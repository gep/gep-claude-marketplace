"""The gate's dry run: what would the hook do with a call, and what is the effective configuration.

A call check runs the hook's own chain
(`Halt.of_env` -> `GateHook.decide` over the usable backends, Clef first), so it may contact both
backends; there is no rule engine to consult.
"""

import os
from collections.abc import Mapping
from typing import Any

from ..config import GateConfig
from ..errors import ConfigError
from ..halt import Halt
from ..hook import GateHook
from ..jev.backends import Backends, SharedSettings
from ..outcome import HookOut
from ..session import SessionCheck


class GateDryRun:
    """Answers for one environment without running anything."""

    def __init__(self, env: Mapping[str, str]) -> None:
        self.env = env

    @staticmethod
    def _reject_constant(name: str) -> Any:
        raise ValueError(f"invalid JSON constant {name}")

    @staticmethod
    def _num(x: Any) -> Any:
        """JS prints 1 for 1.0."""
        return int(x) if isinstance(x, float) and x.is_integer() else x

    @staticmethod
    def _state_text(name: str, state: Any) -> str:
        if state.kind == "ok":
            return "usable"
        if state.kind == "off":
            return f"disabled by GATE_{name.upper()}_ENABLED=0"
        return str(state.error)

    def _backend_summary(self, spec: Any, state: Any) -> dict[str, Any]:
        """One backend's settings for display; the key is only ever `[set]` or `[none]`."""
        env = self.env
        cfg = state.config
        if cfg is not None:
            url, model, timeout = cfg["url"], cfg["model"], cfg["timeoutMs"]
        else:  # not usable: show what the environment says, defaults included
            url = (env.get(spec.url_var) or "").strip() or spec.default_url
            model = (env.get(spec.model_var) or "").strip() or spec.default_model
            raw = (env.get(spec.timeout_var) or "").strip()
            timeout = int(raw) if raw.isdigit() else (raw or spec.default_timeout_ms)
        return {
            "enabled": state.kind != "off",
            "state": self._state_text(spec.name, state),
            "usable": state.kind == "ok",
            "url": url,
            "model": model,
            "timeoutMs": timeout,
            "apiKey": "[set]" if (env.get(spec.key_var) or "").strip() else "[none]",
        }

    def _shared_summary(self) -> dict[str, Any]:
        shared = SharedSettings(self.env, os.getcwd())
        out: dict[str, Any] = {"prompt": (self.env.get("GATE_JEV_PROMPT") or "").strip() or "destructive"}
        if shared.error is not None:
            out["error"] = str(shared.error)
            return out
        assert shared.prompt is not None
        out.update(
            escalateThreshold=GateDryRun._num(shared.escalate_threshold),
            question=shared.prompt["question"],
            ask=shared.prompt["ask"],
        )
        return out

    def effective_config(self) -> dict[str, Any]:
        """Gate settings, backend order and both backends (keys redacted), shared settings, the halt and the session warning."""
        out: dict[str, Any] = {}
        try:
            out.update(GateConfig.read(self.env))
        except ConfigError as e:
            out["error"] = str(e)
        halt = Halt.of_env(self.env)
        out["order"] = list(Backends.ORDER)
        if halt.backends is not None:
            out["backends"] = {s.name: self._backend_summary(s, halt.backends.state(s.name)) for s in Backends.SPECS}
        out["shared"] = self._shared_summary()
        out["halted"] = Halt.text(halt.issues) if halt.halted else None
        out["sessionWarning"] = SessionCheck.message(self.env)
        return out

    def check_call(self, call: Mapping[str, Any]) -> dict[str, Any]:
        """The hook's answer for one call: {tool, command|input, decision, reason, warning, risk, backend, fallback, halted}."""
        if call["mode"] == "bash":
            hook_call: dict[str, Any] = {"tool": "Bash", "command": call["command"]}
            target: dict[str, Any] = {"tool": "Bash", "command": call["command"]}
        else:
            hook_call = {"tool": call["tool"], "input": call["input"]}
            target = {"tool": call["tool"], "input": call["input"]}
        try:
            enabled = GateConfig._flag(self.env, "GATE_ENABLED", True)
        except ConfigError:
            enabled = True  # unreadable: the gate stays on and the halt below reports it (as the hook does)
        none = {**target, "decision": "none", "reason": None, "warning": None, "risk": None, "backend": None, "fallback": [], "halted": None}
        if not enabled:
            return {**none, "reason": "gate disabled (GATE_ENABLED=0)"}
        halt = Halt.of_env(self.env)
        if halt.halted:  # nothing is contacted
            out = HookOut.halt(halt.issues)
            return {**none, "decision": out.decision, "reason": out.reason, "warning": out.message(), "halted": Halt.text(halt.issues)}
        record: dict[str, Any] = {}
        out = GateHook.decide(hook_call, halt.usable, record)
        answered = next((c for c in halt.usable if c["backend"] == record.get("backend")), halt.usable[0])
        return {
            **target,
            "decision": out.decision,
            "reason": out.reason,
            "warning": out.message(),
            "risk": record.get("risk"),
            "threshold": answered["escalateThreshold"],
            "backend": record.get("backend"),
            "fallback": record.get("fallback", []),
            "halted": None,
        }
