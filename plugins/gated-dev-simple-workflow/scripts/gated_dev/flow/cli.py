"""Command line of the /ticket-flow helper.

    flow.py status --parent P [--max N] [--candidate KEY]...
    flow.py launch --parent P KEY [--summary TEXT] [--reason TEXT] [--max N] [--mode MODE] [--remote-control] [--dry-run]
    flow.py skip   --parent P KEY [--reason TEXT]
    flow.py retry  --parent P KEY
    flow.py wait   --parent P [--poll-seconds N] [--stale-hours N] [--lost-watch-minutes N]

Every command prints JSON lines on stdout (progress and diagnostics go to stderr). `wait` prints one
line with an `outcome` (slot-free, lost-watch, merged-orphan, stale: exit 0; error: exit 1);
usage errors print `{"outcome": "usage"}` and exit 2; other failures print `{"outcome": "error"}`
and exit 1.
"""

import json
import os
import sys
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from typing import Any, ClassVar

from gated_dev.errors import FlowError, LaunchError
from gated_dev.flow.observer import FlowObserver
from gated_dev.flow.state import FlowState
from gated_dev.flow.waiter import FlowWaiter
from gated_dev.launch.launcher import TicketLauncher
from gated_dev.launch.ticket_project import TicketProject
from gated_dev.process import ProcessRunner
from gated_dev.watch.pr_client import PrClient


class FlowCli:
    """Parses the arguments and runs one flow command."""

    COMMANDS = ("status", "launch", "skip", "retry", "wait")
    DEFAULTS: ClassVar[dict[str, float]] = {"max": 1, "pollSeconds": 60, "staleHours": 3, "lostWatchMinutes": 10}
    """Used when the config file has no `flow` section (or no such key)."""
    LIMITS: ClassVar[dict[str, tuple[float, float]]] = {"max": (1, 10), "pollSeconds": (15, 3600)}
    VALUE_OPTIONS: ClassVar[dict[str, str]] = {
        "--parent": "parent",
        "--max": "max",
        "--summary": "summary",
        "--reason": "reason",
        "--mode": "mode",
        "--candidate": "candidates",
        "--poll-seconds": "pollSeconds",
        "--stale-hours": "staleHours",
        "--lost-watch-minutes": "lostWatchMinutes",
        "--cwd": "cwd",
    }
    ALLOWED: ClassVar[dict[str, frozenset[str]]] = {
        "status": frozenset(("parent", "max", "candidates", "cwd")),
        "launch": frozenset(("parent", "max", "summary", "reason", "mode", "remoteControl", "dryRun", "cwd")),
        "skip": frozenset(("parent", "reason", "cwd")),
        "retry": frozenset(("parent", "cwd")),
        "wait": frozenset(("parent", "max", "pollSeconds", "staleHours", "lostWatchMinutes", "cwd")),
    }
    EXIT: ClassVar[dict[str, int]] = {"slot-free": 0, "lost-watch": 0, "merged-orphan": 0, "stale": 0, "error": 1}

    USAGE = """Usage: flow.py <command> --parent PARENT [options]

    status  [--max N] [--candidate KEY]...   observe the launched runs, save, print the flow
    launch  KEY [--summary TEXT] [--reason TEXT] [--max N] [--mode heavy|medium|light]
                  [--remote-control] [--dry-run]
                                             open the session of KEY (with that pipeline mode) and record it as launched
    skip    KEY [--reason TEXT]              never pick KEY
    retry   KEY                              forget a skipped or aborted KEY
    wait    [--poll-seconds N] [--stale-hours N] [--lost-watch-minutes N]
                                             poll until slot-free, lost-watch, merged-orphan, stale or error

      --parent PARENT   the Jira parent of the flow
      --max N           runs holding a slot at once (1-10); stored in the flow state
      --cwd DIR         a directory inside the repository (default: current)"""

    @classmethod
    def parse_args(cls, argv: Sequence[str]) -> dict:
        """Raises LaunchError with a usage message."""
        opts: dict = {"command": None, "keys": [], "candidates": [], "help": False, "dryRun": False, "remoteControl": False}
        seen: set[str] = set()
        i = 0
        while i < len(argv):
            a = argv[i]
            if a in ("--help", "-h"):
                opts["help"] = True
            elif a in ("--dry-run", "-n"):
                opts["dryRun"] = True
                seen.add("dryRun")
            elif a == "--remote-control":
                opts["remoteControl"] = True
                seen.add("remoteControl")
            elif a in cls.VALUE_OPTIONS:
                name = cls.VALUE_OPTIONS[a]
                i += 1
                if i >= len(argv) or not argv[i] or argv[i].startswith("--"):
                    raise LaunchError(f"{a} needs a value")
                seen.add(name)
                if name == "candidates":
                    opts["candidates"].append(argv[i])
                else:
                    opts[name] = argv[i]
            elif a.startswith("-"):
                raise LaunchError(f"unknown option {a}")
            elif opts["command"] is None:
                opts["command"] = a
            else:
                opts["keys"].append(a)
            i += 1
        if opts["help"]:
            return opts
        command = opts["command"]
        if command not in cls.COMMANDS:
            raise LaunchError(f"unknown command {command!r}" if command else f"give a command ({', '.join(cls.COMMANDS)})")
        for name in sorted(seen - cls.ALLOWED[command]):
            raise LaunchError(f"{command}: option for {name} does not apply")
        if not opts.get("parent"):
            raise LaunchError("--parent is required")
        wants_key = command in ("launch", "skip", "retry")
        if wants_key and len(opts["keys"]) != 1:
            raise LaunchError(f"{command} needs exactly one ticket key")
        if not wants_key and opts["keys"]:
            raise LaunchError(f"{command} takes no ticket key")
        if "mode" in opts and opts["mode"] not in TicketProject.MODE_NAMES:
            raise LaunchError(f"mode must be one of {', '.join(TicketProject.MODE_NAMES)}, got {opts['mode']!r}")
        for name in ("max", "pollSeconds", "staleHours", "lostWatchMinutes"):
            if name in opts:
                opts[name] = cls._number(name, opts[name])
        return opts

    @classmethod
    def _number(cls, name: str, raw: str) -> float:
        try:
            value = float(raw)
        except ValueError:
            raise LaunchError(f"{name} must be a number, got {raw!r}") from None
        low, high = cls.LIMITS.get(name, (0, float("inf")))
        if not low <= value <= high or (name == "max" and value != int(value)):
            raise LaunchError(f"{name} must be {'a whole number ' if name == 'max' else ''}between {low:g} and {high:g}, got {raw}")
        return int(value) if name == "max" else value

    @staticmethod
    def _write(stream: Any) -> Callable[[str], None]:
        def write(line: str) -> None:
            stream.write(f"{line}\n")
            stream.flush()

        return write

    def __init__(
        self,
        *,
        env: Mapping[str, str],
        run: ProcessRunner.Run,
        gh_run: Callable[..., str],
        sleep: Callable[[float], None],
        now: Callable[[], datetime],
        is_alive: Callable[[int], bool],
        out: Callable[[str], None],
        err: Callable[[str], None],
    ) -> None:
        self.env = env
        self.run = run
        self.gh_run = gh_run
        self.sleep = sleep
        self.now = now
        self.is_alive = is_alive
        self.out = out
        self.err = err

    def _emit(self, payload: dict) -> None:
        self.out(json.dumps(payload))

    def _settings(self, config: Mapping, opts: Mapping) -> dict:
        """Flow settings: the config file's `flow` section over DEFAULTS, then the flags."""
        section = config.get("flow") if isinstance(config.get("flow"), Mapping) else {}
        merged = {name: section.get(name, default) for name, default in self.DEFAULTS.items()}
        merged.update({name: opts[name] for name in self.DEFAULTS if name in opts})
        return merged

    def _open_session(self, *, root: str, key: str, config: Mapping, parent: str, summary: str, mode: str | None = None, remote_control: bool, dry_run: bool) -> dict:
        """The launcher call: the summary names the ticket's worktree, the parent resolves `baseFromParent`."""
        return TicketLauncher.launch_ticket(
            root=root,
            key=key,
            config=config,
            remote_control=remote_control,
            dry_run=dry_run,
            summary=summary or None,
            ticket_mode=mode,
            parent=parent,
            env=self.env,
            run=self.run,
            log=self.err,
        )

    def execute(self, opts: Mapping) -> int:
        command = opts["command"]
        cwd = opts.get("cwd") or os.getcwd()
        root = TicketProject.find_root(cwd=cwd, run=self.run)
        project = TicketProject(root, self.run)
        notice = None if opts.get("dryRun") else project.ensure_config()
        config = project.read_config()
        for line in (notice, project.config_notice):
            if line:
                self.err(line)
        TicketProject.check_key(opts["parent"], config)
        settings = self._settings(config, opts)
        state = FlowState(
            directory=FlowState.flow_dir(root=root, run=self.run),
            parent=opts["parent"],
            now=self.now,
            is_alive=self.is_alive,
            max_runs=int(settings["max"]),
        )
        observer = FlowObserver(project, run=self.gh_run)
        if command == "wait":
            return self._wait(state, observer, config, settings, opts)
        for key in opts["keys"]:
            TicketProject.check_key(key, config)
        if command == "status":
            return self._status(state, observer, config, opts)
        if command == "launch":
            return self._launch(state, project, config, opts, root)
        return self._change(state, command, opts)

    def _wait(self, state: FlowState, observer: FlowObserver, config: Mapping, settings: Mapping, opts: Mapping) -> int:
        if not state.exists():
            raise FlowError(f"no flow state for {state.parent}; run status or launch first")
        if "max" in opts:
            with state.transaction():
                state.max = opts["max"]
                state.save()
        result = FlowWaiter(observer, sleep=self.sleep, log=self.err).wait(
            state=state,
            config=config,
            poll_seconds=settings["pollSeconds"],
            stale_hours=settings["staleHours"],
            lost_watch_minutes=settings["lostWatchMinutes"],
        )
        self._emit(result)
        return self.EXIT[result["outcome"]]

    def _status(self, state: FlowState, observer: FlowObserver, config: Mapping, opts: Mapping) -> int:
        """Observes and saves; when another process holds the lock, reports without saving."""
        try:
            state.acquire()
            locked = True
        except FlowError as error:
            self.err(f"not saving: {error}")
            locked = False
        try:
            existed = state.load()
            if "max" in opts:
                state.max = opts["max"]
            report = observer.observe(state, config=config, candidates=opts["candidates"])
            save = locked and (existed or "max" in opts)
            if save:
                state.save()
        finally:
            if locked:
                state.release()
        tickets = {key: {**ticket, "mode": ticket.get("mode") or "-", **({"changedAt": report["observations"][key]["changedAt"], "heartbeatAt": report["observations"][key]["heartbeatAt"]} if key in report["observations"] else {})} for key, ticket in state.tickets.items()}
        self._emit(
            {
                "command": "status",
                "parent": state.parent,
                "new": not existed,
                "saved": save,
                "max": state.max,
                "slotsUsed": report["slotsUsed"],
                "freeSlots": report["freeSlots"],
                "tickets": tickets,
                "foreign": report["foreign"],
                "lastPick": state.picks[-1] if state.picks else None,
                "errors": report["errors"],
            }
        )
        return 0

    def _launch(self, state: FlowState, project: TicketProject, config: Mapping, opts: Mapping, root: str) -> int:
        key = opts["keys"][0]
        summary, reason, mode = opts.get("summary", ""), opts.get("reason", ""), opts.get("mode")

        def attempt() -> dict:
            if "max" in opts:
                state.max = opts["max"]
            if key in state.tickets:
                raise FlowError(f"{key} is already in the flow of {state.parent} ({state.tickets[key]['status']}); --retry only frees skipped or aborted tickets")
            if project.find_run(key=key, config=config) is not None:
                raise FlowError(f"{key} already has a run; open it with /ticket {key} --resume")
            return self._open_session(root=root, key=key, config=config, parent=state.parent, summary=summary, mode=mode, remote_control=opts["remoteControl"], dry_run=opts["dryRun"])

        if opts["dryRun"]:
            state.load()  # reads only: a dry run takes no lock and writes nothing
            planned = attempt()
            self._emit({"command": "launch", "parent": state.parent, "key": key, "dryRun": True, "launched": False, "plan": planned})
            return 0
        with state.transaction():
            result = attempt()
            worktree = result.get("worktree")
            state.add_launched(key, summary=summary, reason=reason, worktree=worktree.get("path") if isinstance(worktree, Mapping) else None, mode=mode)
            state.record_pick(key=key, reason=reason or "launched")
            state.save()
        self._emit({"command": "launch", "parent": state.parent, "key": key, "dryRun": False, "launched": result["launched"], "mode": result["mode"], "cwd": result.get("cwd"), "title": result.get("title")})
        return 0

    def _change(self, state: FlowState, command: str, opts: Mapping) -> int:
        key = opts["keys"][0]
        with state.transaction():
            if command == "skip":
                state.skip(key, opts.get("reason", ""))
                changed = True
            else:
                changed = state.retry(key)
                if not changed:
                    raise FlowError(f"{key} is not skipped or aborted in the flow of {state.parent}")
            state.save()
        self._emit({"command": command, "parent": state.parent, "key": key, "ok": changed})
        return 0

    @classmethod
    def main(
        cls,
        argv: Sequence[str] | None = None,
        *,
        env: Mapping[str, str] | None = None,
        run: ProcessRunner.Run = ProcessRunner.default_run,
        gh_run: Callable[..., str] = PrClient.default_run,
        sleep: Callable[[float], None] = FlowWaiter._default_sleep,
        now: Callable[[], datetime] = FlowState._default_now,
        is_alive: Callable[[int], bool] = FlowState._default_is_alive,
        out: Callable[[str], None] | None = None,
        err: Callable[[str], None] | None = None,
    ) -> int:
        """Runs the CLI; returns the exit code."""
        argv = sys.argv[1:] if argv is None else argv
        out = out or cls._write(sys.stdout)
        err = err or cls._write(sys.stderr)
        try:
            opts = cls.parse_args(argv)
        except LaunchError as error:
            out(json.dumps({"outcome": "usage", "message": str(error)}))
            err(cls.USAGE)
            return 2
        if opts["help"]:
            out(cls.USAGE)
            return 0
        cli = cls(
            env=os.environ if env is None else env, run=run, gh_run=gh_run, sleep=sleep, now=now, is_alive=is_alive, out=out, err=err
        )
        try:
            return cli.execute(opts)
        except (FlowError, LaunchError, OSError) as error:
            out(json.dumps({"outcome": "error", "message": str(error)}))
            return 1


def main(argv: Sequence[str] | None = None, **kwargs: Any) -> int:
    """Module-level entry point: runs `FlowCli.main`."""
    return FlowCli.main(argv, **kwargs)
