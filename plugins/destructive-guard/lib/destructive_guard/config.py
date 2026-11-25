"""The gate's own settings from environment variables: GATE_ENABLED, GATE_ENV_GUARD, GATE_LOG and the
backend switches GATE_CLEF_ENABLED / GATE_JEV_ENABLED.

The per-backend variables are read by jev/backends.py. `read` is pure (env in, config out) and raises
ConfigError for a value it does not understand.
"""

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any, ClassVar

from .errors import ConfigError
from .paths import ProjectDir


class GateConfig:
    _TRUE: ClassVar[set[str]] = {"1", "true", "yes", "on"}
    _FALSE: ClassVar[set[str]] = {"0", "false", "no", "off"}

    @staticmethod
    def _value(env: Mapping[str, str], name: str) -> str | None:
        """Unset or empty means "use the default"."""
        v = env.get(name)
        if v is None:
            return None
        s = str(v).strip()
        return s or None

    @classmethod
    def _flag(cls, env: Mapping[str, str], name: str, fallback: bool) -> bool:
        v = cls._value(env, name)
        if v is None:
            return fallback
        lower = v.lower()
        if lower in cls._TRUE:
            return True
        if lower in cls._FALSE:
            return False
        raise ConfigError(f'{name} must be 0 or 1, got "{v}"')

    @classmethod
    def read(cls, env: Mapping[str, str], *, cwd: str | Path | None = None) -> dict[str, Any]:
        """env -> {enabled, envGuard, log, clefEnabled, jevEnabled}. Raises ConfigError for a value it does not understand."""
        log = cls.log_path(env, cwd=cwd)
        enabled = cls._flag(env, "GATE_ENABLED", True)
        env_guard = cls._flag(env, "GATE_ENV_GUARD", True)
        clef_enabled = cls._flag(env, "GATE_CLEF_ENABLED", True)
        jev_enabled = cls._flag(env, "GATE_JEV_ENABLED", True)
        return {
            "enabled": enabled,
            "envGuard": env_guard,
            "log": log,
            "clefEnabled": clef_enabled,
            "jevEnabled": jev_enabled,
        }

    @classmethod
    def log_path(cls, env: Mapping[str, str], *, cwd: str | Path | None = None) -> str | None:
        """GATE_LOG as an absolute path, or None. Never raises ConfigError, so a bad flag is still logged."""
        value = cls._value(env, "GATE_LOG")
        return None if value is None else ProjectDir.project_path(value, env, cwd if cwd is not None else os.getcwd())
