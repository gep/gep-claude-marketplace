"""Command line of the launcher.

    launch.py [--dry-run] [--remote-control] [--mode MODE] KEY...
    launch.py [--dry-run] [--remote-control] [--mode MODE] --all
"""

import os
import sys
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from gated_dev.errors import LaunchError
from gated_dev.launch.launcher import TicketLauncher
from gated_dev.launch.naming import LaunchNaming
from gated_dev.launch.ticket_project import TicketProject
from gated_dev.process import ProcessRunner


class LaunchCli:
    """Parses the arguments and runs the launcher for each ticket."""

    # Arguments of the `/ticket` prompt. `--mode` takes one of MODES as its value.
    ALLOWED_ARGS = frozenset(("--resume", "--mode"))
    MODES = TicketProject.MODE_NAMES

    USAGE = """Usage: launch.py [--dry-run] [--remote-control] [--mode MODE] (KEY... | --all)

    Opens one Claude Code session per ticket, running /ticket <KEY>, in a new
    Orca or iTerm2 tab. A ticket with a run is resumed in its worktree.

      KEY...            tickets to open (<ticketsDir>/<KEY>/ticket.md, or a Jira key when "tracker" is configured)
      --all             every ticket file in the main checkout that has no run yet
      --dry-run         print what would be opened; run nothing
      --remote-control  also start each session with --remote-control
      --mode MODE       heavy, medium or light for every key: the session starts on
                        modes.<MODE>.session (claude --model) and gets /ticket KEY --mode MODE;
                        without it the session decides the mode. Refused for a key with a run
                        (a resume uses the run's recorded mode)
      --cwd DIR         a directory inside the repository (default: current)"""

    @staticmethod
    def parse_args(argv: Sequence[str]) -> dict:
        opts: dict = {"keys": [], "all": False, "dryRun": False, "remoteControl": False, "cwd": None, "mode": None, "help": False}
        i = 0
        while i < len(argv):
            a = argv[i]
            if a == "--all":
                opts["all"] = True
            elif a in ("--dry-run", "-n"):
                opts["dryRun"] = True
            elif a == "--remote-control":
                opts["remoteControl"] = True
            elif a in ("--help", "-h"):
                opts["help"] = True
            elif a == "--cwd":
                i += 1
                if i >= len(argv) or not argv[i]:
                    raise LaunchError("--cwd needs a directory")
                opts["cwd"] = argv[i]
            elif a == "--mode":
                i += 1
                if i >= len(argv) or argv[i] not in LaunchCli.MODES:
                    raise LaunchError("--mode needs heavy, medium or light")
                opts["mode"] = argv[i]
            elif a.startswith("-"):
                raise LaunchError(f"unknown option {a}")
            else:
                opts["keys"].append(a)
            i += 1
        if not opts["help"] and opts["all"] == bool(opts["keys"]):
            raise LaunchError("give ticket keys or --all (not both)")
        return opts


    @classmethod
    def check_ticket_args(cls, args: Sequence[str]) -> None:
        """Raises unless every `/ticket` argument is allowed; `--mode` must be followed by a mode name."""
        i = 0
        while i < len(args):
            arg = args[i]
            if arg not in cls.ALLOWED_ARGS:
                raise LaunchError(f"Argument not allowed: {LaunchNaming._json(arg)}")
            if arg == "--mode":
                i += 1
                if i >= len(args) or args[i] not in cls.MODES:
                    raise LaunchError(f"--mode needs heavy, medium or light: {LaunchNaming._json(args[i] if i < len(args) else None)}")
            i += 1


    @staticmethod
    def describe(r: Mapping) -> str:
        lines = [f"{r['key']}: {r['mode']}{' (opened)' if r['launched'] else ''}"]
        if "host" in r:
            lines.append(f"  host:    {r['host'] or 'none (dry run only)'}")
        if r.get("base"):
            lines.append(f"  base:    {r['base']}")
        if r.get("cwd"):
            lines.append(f"  cwd:     {r['cwd']}")
        if r.get("title"):
            lines.append(f"  title:   {r['title']}")
        if "ticketMode" in r:
            lines.append(f"  mode:    {r['ticketMode'] or ('not recorded' if r['mode'] == 'resume' else 'decided by the session')}")
            lines.append(f"  model:   {r.get('model') or 'default'}")
        if r.get("command"):
            lines.append(f"  command: {r['command']}")
        if r.get("note"):
            lines.append(f"  note:    {r['note']}")
        return "\n".join(lines)


    @staticmethod
    def _write(stream: Any) -> Callable[[str], None]:
        def write(line: str) -> None:
            stream.write(f"{line}\n")
            stream.flush()

        return write


    @classmethod
    def main(
        cls,
        argv: Sequence[str] | None = None,
        *,
        env: Mapping[str, str] | None = None,
        run: ProcessRunner.Run = ProcessRunner.default_run,
        out: Callable[[str], None] | None = None,
        err: Callable[[str], None] | None = None,
    ) -> int:
        """Runs the CLI; returns the exit code."""
        argv = sys.argv[1:] if argv is None else argv
        env = os.environ if env is None else env
        out = out or LaunchCli._write(sys.stdout)
        err = err or LaunchCli._write(sys.stderr)
        try:
            opts = LaunchCli.parse_args(argv)
        except LaunchError as error:
            err(f"{error}\n\n{LaunchCli.USAGE}")
            return 2
        if opts["help"]:
            out(LaunchCli.USAGE)
            return 0
        try:
            root = TicketProject.find_root(cwd=opts["cwd"] or os.getcwd(), run=run)
            project = TicketProject(root, run)
            notice = None if opts["dryRun"] else project.ensure_config()
            config = project.read_config()
        except (LaunchError, OSError) as error:
            err(str(error))
            return 1
        for line in (notice, project.config_notice):
            if line:
                err(line)
        keys = opts["keys"]
        if opts["all"]:
            keys = []
            for key in TicketProject(root, run).list_ticket_keys(config=config):
                try:
                    if not TicketProject(root, run).find_run(key=key, config=config):
                        keys.append(key)
                except LaunchError:
                    pass
            if not keys:
                out(f"no ticket without a run in {config['ticketsDir']}")
                return 0
        failed = 0
        for key in keys:
            try:
                result = TicketLauncher.launch_ticket(
                    root=root,
                    key=key,
                    config=config,
                    remote_control=opts["remoteControl"],
                    dry_run=opts["dryRun"],
                    ticket_mode=opts["mode"],
                    env=env,
                    run=run,
                    log=err,
                )
                out(LaunchCli.describe(result))
            except (LaunchError, OSError) as error:
                failed += 1
                err(f"{key}: {error}")
        return 1 if failed else 0


def main(argv: Sequence[str] | None = None, **kwargs: Any) -> int:
    """Module-level entry point: runs `LaunchCli.main`."""
    return LaunchCli.main(argv, **kwargs)
