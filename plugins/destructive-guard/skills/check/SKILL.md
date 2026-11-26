---
name: check
description: Dry-run a Bash command or MCP tool call through the destructive-guard hook and report whether it would ask and why, or print the effective configuration (gate settings and both classifier backends, Clef and Jev, with API keys never shown). Use when the user asks "would the gate ask for X", "why did the gate ask", or when planning which commands will need approval. Never runs the checked command; a call check sends the redacted call to Clef and, if Clef does not answer, to Jev (up to one request each).
---

# destructive-guard: check

Answers what the hook would do, without running anything. It reads the
configuration exactly like the hook (same env variables) and calls the same
decision code. The gate has two classifier backends, tried in order: Clef first,
then Jev when Clef is disabled, misconfigured or does not answer. A call check
runs that same chain, so it **contacts Clef and, if Clef fails, Jev** (up to one
request each); never put secrets in the checked call. A call that mentions a
`.env` file is not sent (reported with a warning). When no backend is usable the
session is halted: the check reports the halt and contacts nothing.

Script: `${CLAUDE_PLUGIN_ROOT}/lib/check.py` (needs Python >= 3.14).

If `python3` on `PATH` is older, the script exits with
`needs Python >= 3.14 (found X.Y at ...)`. Run it with a 3.14 interpreter
instead (for example `python3.14 -I "${CLAUDE_PLUGIN_ROOT}/lib/check.py" ...`)
and tell the user to point `GATE_INFRA_PYTHON` at that interpreter in the
`env` block of `~/.claude/settings.json`, so the hook finds it too.

## Usage

Pass the command to check as **one single-quoted argument**.

```bash
# A Bash command
python3 "${CLAUDE_PLUGIN_ROOT}/lib/check.py" 'git push --force origin main'

# An MCP call: tool name, then its input as one JSON argument
python3 "${CLAUDE_PLUGIN_ROOT}/lib/check.py" --mcp mcp__db__execute_sql '{"query":"DROP TABLE users"}'

# Effective configuration: order, both backends, shared settings, halt (also the default with no arguments)
python3 "${CLAUDE_PLUGIN_ROOT}/lib/check.py" --config
```

Option: `--json` prints machine-readable output (`decision`, `reason`, `risk`,
`threshold`, `warning`, `backend`, `fallback`, `halted`; a config adds `order`,
`backends`, `shared`). With several commands to check, run the script once per command.

## Reading the result

A call check:

- `decision: ask` with `reason: <backend>: <choice> p=… risk=…` — the hook would ask.
  `answered: clef|jev` names the backend that answered; each `fallback: <backend>: <error> (<ms> ms)`
  line is a backend tried before it that gave no answer (timeout, HTTP error, network, malformed answer).
- `decision: ask (clef-unavailable: …; jev-unavailable: …)` — no backend answered, so
  the hook asks; a warning follows.
- `decision: none (risk 0.04 ≤ 0.5)` — the answering backend judged it below the
  threshold; no decision, Claude Code's own permission rules decide.
- `decision: deny (halted)` with a `halted:` line — no backend is usable (disabled,
  missing key, bad value or an unreadable `GATE_*` flag), so the hook denies and
  stops; nothing was contacted.
- `decision: none — warning: …` — nothing was sent: a `.env` call is skipped.

`--config` lists the order (`clef -> jev`) and, per backend, its state (`usable`,
`disabled by GATE_<NAME>_ENABLED=0`, or the setting error), URL, model, timeout and
api key as `[set]` or `[none]` (values are never printed), then the shared prompt and
threshold. A `halted:` line appears when nothing is usable.

Report the result as-is; never run the checked command as part of this skill.
