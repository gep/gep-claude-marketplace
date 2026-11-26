"""Dry run for destructive-guard: what would the hook do with a call, and what is the effective
configuration? Used by the `check` skill. Never runs the call; a call check runs the hook's chain and sends the redacted call to
Clef, then Jev if Clef does not answer (up to one request each).

    check.py [--json] -- <command...>       a Bash command
    check.py [--json] --mcp <tool> [<json input>]       an MCP call
    check.py [--json] --config                          effective config

No call given -> --config. Exit code 0 on success (whatever the decision), 2 on usage errors.
"""

import json
import os
import sys
from collections.abc import Mapping
from typing import Any

from ..errors import UsageError
from .dry_run import GateDryRun
from .report import CheckReport


class CheckCli:
    """Parses the check command line, runs the dry run and prints its answer."""

    @staticmethod
    def parse_args(argv: list[str]) -> dict[str, Any]:
        opts: dict[str, Any] = {"json": False, "mode": "config"}
        args = list(argv)
        while args:
            a = args.pop(0)
            if a == "--json":
                opts["json"] = True
            elif a == "--jev":
                raise UsageError("--jev is no longer needed: a call check always runs the full chain")
            elif a == "--config":
                opts["mode"] = "config"
            elif a == "--mcp":
                tool = args.pop(0) if args else None
                if not tool or not tool.startswith("mcp__"):
                    raise UsageError("--mcp needs a tool name starting with mcp__")
                raw = " ".join(args) if args else "{}"
                try:
                    tool_input = json.loads(raw, parse_constant=GateDryRun._reject_constant)
                except ValueError:
                    raise UsageError(f"MCP input is not valid JSON: {raw}") from None
                if not isinstance(tool_input, dict):
                    raise UsageError("MCP input must be a JSON object")
                return {**opts, "mode": "mcp", "tool": tool, "input": tool_input}
            elif a == "--":
                if not args:
                    raise UsageError("no command after --")
                return {**opts, "mode": "bash", "command": " ".join(args)}
            elif a.startswith("--"):
                raise UsageError(f"unknown option {a}")
            else:
                return {**opts, "mode": "bash", "command": " ".join([a, *args])}
        return opts

    @classmethod
    def render(cls, argv: list[str], *, env: Mapping[str, str], cwd: str) -> tuple[int, str]:
        """(exit code, output text without the trailing newline)."""
        try:
            opts = cls.parse_args(argv)
        except UsageError as e:
            return 2, f"usage error: {e}"
        if opts["mode"] == "config":
            c = GateDryRun(env).effective_config()
            return 0, CheckReport._dump(c) if opts["json"] else CheckReport.format_config(c)
        r = GateDryRun(env).check_call(opts)
        return 0, CheckReport._dump(r) if opts["json"] else CheckReport.format_check(r)

    @classmethod
    def run(cls, argv: list[str], *, env: Mapping[str, str], cwd: str) -> int:
        code, out = cls.render(argv, env=env, cwd=cwd)
        sys.stdout.buffer.write((out + "\n").encode("utf-8", errors="replace"))
        sys.stdout.buffer.flush()
        return code

    @classmethod
    def main(cls) -> int:
        return cls.run(sys.argv[1:], env=os.environ, cwd=os.getcwd())


def main() -> int:
    return CheckCli.main()
