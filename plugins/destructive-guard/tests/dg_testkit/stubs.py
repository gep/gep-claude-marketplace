"""Shared logic of the entry-stub tests."""

import ast
import re
import subprocess
from pathlib import Path
from typing import ClassVar

from . import DG


class EntryStubs:
    # (stub path, package module the stub imports)
    STUBS: ClassVar[list[tuple[Path, str]]] = [
        (DG / "lib" / "gate_main.py", "gate_main"),
        (DG / "lib" / "session_main.py", "session_main"),
        (DG / "lib" / "prompt_main.py", "prompt_main"),
        (DG / "lib" / "check.py", "check"),
    ]
    TOO_OLD = re.compile(r"^needs Python >= 3\.14 \(found 3\.\d+ at .+\); set GATE_INFRA_PYTHON\n$")

    @classmethod
    def stubs_of(cls, plugin_dir: Path) -> list[tuple[Path, str]]:
        return [(p, m) for p, m in cls.STUBS if plugin_dir in p.parents]

    @staticmethod
    def stub_id(param: tuple[Path, str]) -> str:
        return param[0].name

    @staticmethod
    def check_parses_as_37(stub: Path) -> None:
        ast.parse(stub.read_text(), filename=str(stub), feature_version=(3, 7))

    @classmethod
    def check_old_python_exit_3(cls, old_python: str, stub: Path) -> None:
        proc = subprocess.run([old_python, "-I", str(stub)], capture_output=True, text=True, check=False)
        assert proc.returncode == 3, proc.stderr
        assert cls.TOO_OLD.match(proc.stderr), proc.stderr
        assert proc.stdout == ""

