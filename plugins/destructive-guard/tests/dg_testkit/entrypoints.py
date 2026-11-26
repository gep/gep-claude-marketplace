"""The command lines tests use to run each plugin entry point."""

import os
from pathlib import Path

from . import DG
from .pythons import Pythons


class Entrypoints:
    """Shipped hooks and scripts as argv lists, run on a Python >= 3.14 (`GATE_INFRA_PYTHON` first)."""

    @staticmethod
    def _python() -> str:
        return Pythons.require_new_python(os.environ)

    @classmethod
    def _script(cls, path: Path) -> list[str]:
        return [cls._python(), "-I", str(path)]

    @staticmethod
    def gate_hook() -> list[str]:
        return ["bash", str(DG / "hooks" / "gate.sh")]

    @staticmethod
    def session_hook() -> list[str]:
        return ["bash", str(DG / "hooks" / "session-start.sh")]

    @staticmethod
    def prompt_hook() -> list[str]:
        return ["bash", str(DG / "hooks" / "prompt-gate.sh")]

    @staticmethod
    def env_guard() -> list[str]:
        return ["bash", str(DG / "hooks" / "env-guard.sh")]

    @classmethod
    def check(cls) -> list[str]:
        return cls._script(DG / "lib" / "check.py")

    @classmethod
    def env(cls) -> dict[str, str]:
        return {"GATE_INFRA_PYTHON": cls._python()}
