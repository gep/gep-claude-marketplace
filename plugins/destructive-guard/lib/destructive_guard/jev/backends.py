"""The classifier backends' settings: Clef and Jev, each read by the same per-backend reader.

Model -> code (formal/SimpleGate.lean, `BState`, `Setting`, `usable`, `issues`):

| Lean                                   | Python                                                                  |
| -------------------------------------- | ----------------------------------------------------------------------- |
| `Backend.clef` / `Backend.jev`         | `BackendSpec.name` `"clef"` / `"jev"`; `Backends.ORDER` is `order`      |
| `BState.off`                           | `BackendState.kind == "off"` (`GATE_<NAME>_ENABLED=0`)                  |
| `BState.bad why`                       | `kind == "bad"`, `error` a `JevConfigError` whose `kind` is the `why`   |
| `BState.ok`                            | `kind == "ok"`, `config` the settings dict                              |
| `Setting`                              | `Backends` (`clef`, `jev` states)                                       |
| `usable s`                             | `Backends.usable` (configs of the `ok` states, Clef first)              |
| `issues s`                             | `Backends.issues` (`[(name, JevConfigError)]`, `bad` states only)       |

`SessionIssue` (noKey, badUrl, badPrompt, badValue) is `JevConfigError.kind`. A bad shared value
(threshold, prompt) makes every enabled backend `bad`; a disabled backend stays `off`.
"""

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..config import GateConfig
from ..errors import JevConfigError
from .config import JevConfig


@dataclass(frozen=True)
class BackendSpec:
    """What differs between the backends."""

    name: str
    url_var: str
    default_url: str
    model_var: str
    default_model: str
    timeout_var: str
    default_timeout_ms: int
    key_var: str
    key_required: bool


CLEF = BackendSpec(
    name="clef",
    url_var="GATE_CLEF_URL",
    default_url="http://localhost:11434/v1/systemone",
    model_var="GATE_CLEF_MODEL",
    default_model="clef",
    timeout_var="GATE_CLEF_TIMEOUT_MS",
    default_timeout_ms=3000,
    key_var="GATE_CLEF_API_KEY",
    key_required=False,
)
JEV = BackendSpec(
    name="jev",
    url_var="GATE_JEV_URL",
    default_url=JevConfig.JEV_URL,
    model_var="GATE_JEV_MODEL",
    default_model=JevConfig.DEFAULTS["model"],
    timeout_var="GATE_JEV_TIMEOUT_MS",
    default_timeout_ms=JevConfig.DEFAULTS["timeoutMs"],
    key_var="TYPESAFE_API_KEY",
    key_required=True,
)


@dataclass(frozen=True)
class BackendState:
    """Mirrors Lean `BState`: `off`, `bad` (with `error`) or `ok` (with `config`)."""

    kind: str
    config: dict[str, Any] | None = None
    error: JevConfigError | None = None


class SharedSettings:
    """The values both backends use (threshold, prompt), read once; a bad value is kept as the error."""

    def __init__(self, env: Mapping[str, str], cwd: str | Path) -> None:
        from .prompt import JevPrompt

        self.escalate_threshold: float | None = None
        self.prompt: dict | None = None
        self.error: JevConfigError | None = None
        try:
            self.escalate_threshold = JevConfig._probability(
                env, "GATE_JEV_ESCALATE_THRESHOLD", JevConfig.DEFAULTS["escalateThreshold"]
            )
            try:
                self.prompt = JevPrompt.prompt_from_env(env, cwd=cwd)
            except JevConfigError as e:
                raise JevConfigError(str(e), "badPrompt") from None
        except JevConfigError as e:
            self.error = e


class BackendConfig:
    """Reads one backend's settings."""

    @staticmethod
    def _timeout(spec: BackendSpec, env: Mapping[str, str]) -> int:
        msg = f"{spec.timeout_var} must be an integer from 1 to {JevConfig.MAX_TIMEOUT_MS}"
        raw = JevConfig._optional(env, spec.timeout_var)
        if raw is None:
            return spec.default_timeout_ms
        if not re.fullmatch(r"[0-9]+", raw):
            raise JevConfigError(msg)
        n = int(raw)
        if n < 1 or n > JevConfig.MAX_TIMEOUT_MS:
            raise JevConfigError(msg)
        return n

    @classmethod
    def read(cls, spec: BackendSpec, env: Mapping[str, str], shared: SharedSettings) -> dict[str, Any]:
        """The backend's settings dict (apiKey is None for an optional key that is unset).
        Raises JevConfigError (unlabelled, with a `kind`) for a missing key or an invalid value."""
        api_key = JevConfig._optional(env, spec.key_var)
        if spec.key_required and not api_key:
            raise JevConfigError(f"{spec.key_var} is not set", "noKey")
        timeout_ms = cls._timeout(spec, env)
        url = JevConfig._optional(env, spec.url_var) or spec.default_url
        JevConfig._check_url(url, spec.url_var)
        if shared.error is not None:
            raise shared.error
        return {
            "backend": spec.name,
            "apiKey": api_key,
            "url": url,
            "model": JevConfig._optional(env, spec.model_var) or spec.default_model,
            "escalateThreshold": shared.escalate_threshold,
            "timeoutMs": timeout_ms,
            "prompt": shared.prompt,
        }


class Backends:
    """Clef and Jev settings: the Lean `Setting`. Clef first (D14)."""

    SPECS = (CLEF, JEV)
    ORDER = tuple(s.name for s in SPECS)

    def __init__(self, clef: BackendState, jev: BackendState) -> None:
        self.clef = clef
        self.jev = jev

    def state(self, name: str) -> BackendState:
        return {"clef": self.clef, "jev": self.jev}[name]

    @property
    def usable(self) -> list[dict[str, Any]]:
        """Lean `usable`: the configs of the `ok` backends, in order."""
        return [st.config for n in self.ORDER if (st := self.state(n)).kind == "ok" and st.config is not None]

    @property
    def issues(self) -> list[tuple[str, JevConfigError]]:
        """Lean `issues`: the enabled but misconfigured backends, in order; each error names its backend."""
        return [(n, st.error) for n in self.ORDER if (st := self.state(n)).kind == "bad" and st.error is not None]

    @classmethod
    def read(cls, env: Mapping[str, str], *, cwd: str | Path | None = None) -> Backends:
        """env -> both backends' states. Raises ConfigError for a bad GATE_*_ENABLED flag."""
        cwd = cwd if cwd is not None else os.getcwd()
        gate = GateConfig.read(env, cwd=cwd)
        enabled = {"clef": gate["clefEnabled"], "jev": gate["jevEnabled"]}
        shared = SharedSettings(env, cwd)
        states = {}
        for spec in cls.SPECS:
            if not enabled[spec.name]:
                states[spec.name] = BackendState("off")
                continue
            try:
                states[spec.name] = BackendState("ok", config=BackendConfig.read(spec, env, shared))
            except JevConfigError as e:
                states[spec.name] = BackendState("bad", error=e.for_backend(spec.name))
        return cls(states["clef"], states["jev"])
