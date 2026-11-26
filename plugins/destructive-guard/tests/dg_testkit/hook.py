"""Run the gate hook (gate.sh) as Claude Code does: JSON on stdin, decision on stdout."""

import json
import subprocess
from collections.abc import Mapping
from pathlib import Path

from .entrypoints import Entrypoints
from .envs import IsolatedEnv
from .hook_result import HookResult


class HookRunner:
    """Builds hook inputs, runs the gate hook and reads its decision."""

    @staticmethod
    def bash_input(command: str, cwd: Path | str) -> str:
        return json.dumps({"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(cwd)})

    @staticmethod
    def mcp_input(tool: str, tool_input: dict, cwd: Path | str) -> str:
        return json.dumps({"tool_name": tool, "tool_input": tool_input, "cwd": str(cwd)})

    @staticmethod
    def run(
        stdin: str | bytes,
        *env: Mapping[str, str],
        cwd: Path | str | None = None,
        timeout: float = 30,
    ) -> HookResult:
        data = stdin.encode() if isinstance(stdin, str) else stdin
        proc = subprocess.run(
            Entrypoints.gate_hook(),
            input=data,
            capture_output=True,
            env=IsolatedEnv.clean(Entrypoints.env(), *env),
            cwd=cwd,
            timeout=timeout,
            check=False,
        )
        return HookResult(proc.returncode, proc.stdout.decode("utf-8", errors="replace"))

    @staticmethod
    def run_session(
        *env: Mapping[str, str],
        cwd: Path | str | None = None,
        timeout: float = 30,
    ) -> HookResult:
        """The SessionStart hook (session-start.sh): no stdin that matters, a systemMessage or nothing."""
        proc = subprocess.run(
            Entrypoints.session_hook(),
            input=b"{}",
            capture_output=True,
            env=IsolatedEnv.clean(Entrypoints.env(), *env),
            cwd=cwd,
            timeout=timeout,
            check=False,
        )
        return HookResult(proc.returncode, proc.stdout.decode("utf-8", errors="replace"))

    @staticmethod
    def run_prompt(
        *env: Mapping[str, str],
        cwd: Path | str | None = None,
        timeout: float = 30,
    ) -> HookResult:
        """The UserPromptSubmit hook (prompt-gate.sh): `{"decision":"block",...}` or nothing."""
        proc = subprocess.run(
            Entrypoints.prompt_hook(),
            input=json.dumps({"hook_event_name": "UserPromptSubmit", "prompt": "hello"}).encode(),
            capture_output=True,
            env=IsolatedEnv.clean(Entrypoints.env(), *env),
            cwd=cwd,
            timeout=timeout,
            check=False,
        )
        return HookResult(proc.returncode, proc.stdout.decode("utf-8", errors="replace"))

    @staticmethod
    def decision(stdout: str) -> dict:
        return json.loads(stdout)["hookSpecificOutput"]
