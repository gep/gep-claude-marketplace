"""Reads TYPESAFE_API_KEY and the GATE_JEV_* variables into the Jev settings dict."""

import os
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any, ClassVar
from urllib.parse import urlsplit

from ..errors import JevConfigError


class JevConfig:
    """GATE_JEV_* environment variables → the Jev settings dict."""

    JEV_URL = "https://api.typesafe.ai/v1/systemone"

    DEFAULTS: ClassVar[dict[str, Any]] = {
        "escalateThreshold": 0.5,
        "model": "jev-latest",
        "timeoutMs": 1500,
    }
    MAX_TIMEOUT_MS = 30000
    LOOPBACK: ClassVar[set[str]] = {"localhost", "127.0.0.1", "::1"}

    @staticmethod
    def _optional(env: Mapping[str, str], name: str) -> str | None:
        """Empty optional variables count as unset."""
        v = env.get(name)
        if v is None or v.strip() == "":
            return None
        return v.strip()


    _PROBABILITY = re.compile(r"0(\.[0-9]+)?|1(\.0+)?|\.[0-9]+")


    @classmethod
    def _probability(cls, env: Mapping[str, str], name: str, fallback: float) -> float:
        raw = cls._optional(env, name)
        if raw is None:
            return fallback
        if not cls._PROBABILITY.fullmatch(raw):
            raise JevConfigError(f"{name} must be a number from 0 to 1")
        n = float(raw)
        if not 0 <= n <= 1:
            raise JevConfigError(f"{name} must be a number from 0 to 1")
        return n


    @classmethod
    def _check_url(cls, url: str, var: str = "GATE_JEV_URL") -> None:
        try:
            parts = urlsplit(url)
            parts.port  # noqa: B018 - raises ValueError for a bad port
            host = parts.hostname
        except ValueError:
            raise JevConfigError(f"{var} is not a valid URL", "badUrl") from None
        scheme = parts.scheme.lower()
        if not scheme or (scheme in ("http", "https") and not host):
            raise JevConfigError(f"{var} is not a valid URL", "badUrl")
        # The API key must not travel in clear text, except to a local test server.
        if scheme != "https" and not (scheme == "http" and host in cls.LOOPBACK):
            raise JevConfigError(f"{var} must use https", "badUrl")

    @classmethod
    def read(cls, env: Mapping[str, str], *, cwd: str | Path | None = None) -> dict:
        """The Jev settings (adapter over backends.py: the Jev entry only, ignores GATE_JEV_ENABLED and Clef).
        Raises JevConfigError (with a `kind`, no backend label) when the key is missing or a value is invalid.
        C3 replaces the hook's use of this with `Backends.read`."""
        from .backends import JEV, BackendConfig, SharedSettings

        cwd = cwd if cwd is not None else os.getcwd()
        cfg = BackendConfig.read(JEV, env, SharedSettings(env, cwd))
        del cfg["backend"]
        return cfg
