import json
from collections.abc import Mapping
from typing import Any

from .dry_run import GateDryRun


class CheckReport:
    """Renders a dry run's answer and the effective configuration as text or JSON."""

    @staticmethod
    def _js(v: Any) -> str:
        """A value the way a JS template literal prints it."""
        if v is True:
            return "true"
        if v is False:
            return "false"
        if v is None:
            return "null"
        return str(GateDryRun._num(v))

    @staticmethod
    def _compact(v: Any) -> str:
        return json.dumps(v, ensure_ascii=False, separators=(",", ":"))

    @classmethod
    def _chain_lines(cls, r: Mapping[str, Any]) -> list[str]:
        """Who answered and which backends failed first (backend, error, latency)."""
        lines = []
        if r.get("backend"):
            lines.append(f"answered: {r['backend']}")
        for f in r.get("fallback") or []:
            ms = f" ({cls._js(f['latencyMs'])} ms)" if f.get("latencyMs") is not None else ""
            lines.append(f"fallback: {f['backend']}: {f['error']}{ms}")
        return lines

    @classmethod
    def format_check(cls, r: Mapping[str, Any]) -> str:
        what = f"Bash: {r['command']}" if r["tool"] == "Bash" else f"{r['tool']} {cls._compact(r['input'])}"
        reason, warning, risk = r.get("reason"), r.get("warning"), r.get("risk")
        chain = cls._chain_lines(r)
        if r.get("halted"):
            return "\n".join([what, f"decision: {r['decision']} (halted)", f"halted:   {r['halted']}"])
        if r["decision"] == "ask" and reason and "-unavailable" not in reason:
            lines = [what, "decision: ask", f"reason:   {reason}"]
        elif r["decision"] == "ask":
            lines = [what, f"decision: ask ({reason})"]
        elif warning and risk is None and not reason:
            return "\n".join([what, f"decision: none \u2014 warning: {warning}"])
        elif risk is not None:
            limit = r.get("threshold")
            tail = f" \u2264 {cls._js(limit)}" if limit is not None else ""
            lines = [what, f"decision: none (risk {cls._js(round(risk, 2))}{tail})"]
        else:
            lines = [what, f"decision: none ({reason})" if reason else "decision: none"]
        lines += chain
        if warning:
            lines.append(f"warning:  {warning}")
        return "\n".join(lines)

    @classmethod
    def format_config(cls, c: Mapping[str, Any]) -> str:
        lines: list[str] = []
        if c.get("error"):
            lines.append(f"ERROR: {c['error']}")
        if "enabled" in c:
            lines += [
                f"enabled:       {cls._js(c['enabled'])}",
                f"env guard:     {cls._js(c['envGuard'])}",
                f"log:           {c['log'] if c['log'] is not None else '(off)'}",
            ]
        lines.append(f"order:         {' -> '.join(c['order'])}")
        for name, b in (c.get("backends") or {}).items():
            lines += [
                f"{name}:".ljust(15) + f"{b['state']}",
                f"  enabled:     {cls._js(b['enabled'])}",
                f"  url:         {b['url']}",
                f"  model:       {b['model']}; timeout {cls._js(b['timeoutMs'])}ms; api key {b['apiKey']}",
            ]
        s = c["shared"]
        if s.get("error"):
            lines.append(f"shared:        ERROR {s['error']} (prompt {s['prompt']})")
        else:
            lines += [
                f"prompt:        {s['prompt']} (question {s['question']}; ask {','.join(s['ask'])})",
                f"threshold:     escalate {cls._js(s['escalateThreshold'])}",
            ]
        if c.get("halted"):
            lines.append(f"halted:        {c['halted']}")
        if c.get("sessionWarning") and c["sessionWarning"] != c.get("halted"):
            lines.append(f"session warning: {c['sessionWarning']}")
        return "\n".join(lines)

    @staticmethod
    def _dump(v: Any) -> str:
        return json.dumps(v, indent=2, ensure_ascii=False)
