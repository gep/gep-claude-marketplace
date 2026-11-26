"""Subprocess environments that never inherit the developer's gate settings."""

import os
import re
from collections.abc import Mapping


class IsolatedEnv:
    _DROP = re.compile(r"^(GATE_|TYPESAFE_)")
    # Clef's default URL is a local service (localhost:11434) that may really run on a developer's machine.
    # Tests point it at a closed loopback port instead: Clef stays enabled and usable, like the default, but
    # refuses the connection ("no local Clef"). A test that wants a Clef mock or no Clef overrides it.
    NO_CLEF_URL = "http://127.0.0.1:1/v1/systemone"

    @classmethod
    def clean(cls, *overlays: Mapping[str, str]) -> dict[str, str]:
        """os.environ minus GATE_*, TYPESAFE_* and CLAUDE_PROJECT_DIR, plus GATE_CLEF_URL at a closed port,
        then each overlay in order."""
        env = {k: v for k, v in os.environ.items() if not cls._DROP.match(k) and k != "CLAUDE_PROJECT_DIR"}
        env["GATE_CLEF_URL"] = cls.NO_CLEF_URL
        for overlay in overlays:
            env.update({k: str(v) for k, v in overlay.items()})
        return env
