# destructive-guard

A `PreToolUse` approval gate for Claude Code. Before a Bash command or an MCP
tool call runs, the gate sends it (redacted) to **Jev**, a classifier, and
**asks you first** when Jev judges that the call could delete, overwrite or
irreversibly change data or resources, on the local machine or in a remote
service. It also blocks Read/Edit/Write of `.env` files.

**Privacy: once `TYPESAFE_API_KEY` is set, every Bash command and MCP tool
input (redacted, see [What is sent](#what-is-sent)) leaves your machine and
goes to Jev.** Without a key nothing is sent.

- **Asks, never allows or denies.** The hook prints `ask` or nothing. When it
  prints nothing, Claude Code's own permission rules and mode decide, so the
  plugin can only add prompts and never removes one.
- **Jev decides; if Jev cannot, a person does.** When Jev is configured but
  does not answer (timeout, HTTP error, network error, malformed answer), the
  call asks with a warning, until Jev is back or you set `GATE_ENABLED=0`.
- **No rules, no parser.** Version 1.0 has no rule packs and no shell parser.
  It judges the command text as Jev sees it. Without a key it only blocks
  `.env` files and says so once per session.
- **`.env` files are protected twice.** Read/Edit/Write of them is blocked, and
  a Bash or MCP call that mentions one is never sent to Jev.

## Install

```text
/plugin marketplace add gep/gep-claude-marketplace
/plugin install destructive-guard@gep-claude-marketplace
```

Install it once at user scope to protect every session, then set your Jev key
in the `env` block of `~/.claude/settings.json`:

```json
{ "env": { "TYPESAFE_API_KEY": "..." } }
```

Requires **Python >= 3.14** (standard library only, no packages to install).
The hooks use `GATE_INFRA_PYTHON` if set, else `python3.14`, else `python3`
from `PATH`, and start it in isolated mode (`-I`). With no suitable Python
(missing, or older than 3.14, as the system `python3` often is), every gated
call asks with `plugin-fault: no Python >= 3.14 found` and a warning; it never
allows. Install 3.14 with Homebrew (`brew install python@3.14`),
`uv python install 3.14`, pyenv (`pyenv install 3.14`) or from python.org, and,
if it is not first on `PATH` as `python3.14`/`python3`, set `GATE_INFRA_PYTHON`
to its path.

## What it does

| Call | Result |
| --- | --- |
| Bash command or MCP tool call, Jev answers "risky" (risk above `GATE_JEV_ESCALATE_THRESHOLD`) | **ask**, reason `jev: <choice> p=... risk=...` |
| Same, Jev answers "not risky" or is unsure | no decision: Claude Code's own permissions apply |
| Same, Jev is configured but gives no usable answer | **ask** with a warning, reason `jev-unavailable: <cause>` |
| Call that mentions a `.env` file | not sent to Jev; no decision, plus a warning |
| Plugin fault: unreadable hook input, no suitable Python, internal error | **ask** with a warning, reason `plugin-fault: <cause>` |
| No `TYPESAFE_API_KEY`, or another invalid Jev setting | no decision, silent per call; one warning at session start |
| Read, Edit or Write of `.env`, `.env.*`, `*.env` (not `.env.example`) | **blocked** by the env guard (`GATE_ENV_GUARD=0` turns it off). Only the file name is matched, so `config.environment.ts` is fine. |
| Any other tool | not gated |

Jev never emits `allow` or `deny`, and the hook cannot print them. That is the
claim the formal model proves (see [Formal model](#formal-model)).

### What it no longer does

Version 0.8 had rule packs, a shell parser, a script scanner and fail-closed
asks on configuration errors. All of that is gone (see
[Migration from 0.8](#migration-from-08)). In particular, **without a key the
plugin is not a destructive-command gate**: it blocks `.env` files and warns
once at session start that it does nothing else. Claude Code's own permissions
are your only protection until you set the key.

### Known blind spots

- **Scripts a command runs.** `bash cleanup.sh` is judged by its text only;
  Jev does not see what `cleanup.sh` contains (decision D4). Same for
  `psql -f file.sql`.
- **`.env` calls are never judged.** A call that mentions `.env` is not sent
  to Jev (so its content is not leaked), which also means Jev does not judge
  it; Claude Code's own permissions apply.
- **A broken install asks on every gated call** (bad hook input, no Python,
  internal error) until it is fixed or `GATE_ENABLED=0` is set (D12).
- **Jev outage asks on every gated call**, after waiting out the timeout
  budget. Unattended agents stop at the first call; that is intended.
- Calls from tools other than Bash and `mcp__*` are not gated.

## Warnings and what to do

Warnings are `systemMessage` texts shown to you. Each one starts with
`destructive-guard:`.

| When | Warning | What to do |
| --- | --- | --- |
| Session start | `TYPESAFE_API_KEY is not set: destructive-guard only blocks .env files this session` | Set the key in `env` of your settings, or ignore it if you want only the `.env` guard. |
| Session start | `GATE_JEV_URL must use https`, `GATE_JEV_TIMEOUT_MS must be an integer from 1 to 30000`, `GATE_JEV_ESCALATE_THRESHOLD must be a number from 0 to 1`, an unreadable or invalid prompt file, a bad `GATE_ENABLED`/`GATE_ENV_GUARD` value | Fix the named variable. Until then the gate gives no decisions. `/destructive-guard:check --config` shows what the gate sees. |
| Session start | `ignoring the removed 0.8 variables ...` | Delete those variables from your settings (see [Migration](#migration-from-08)). |
| Session start | `cannot write GATE_LOG <path> (<reason>): no log this session` | Point `GATE_LOG` at a writable file. Missing directories are created; this warning means the path itself cannot be written (a file in the way, no permission). |
| Session start | `no Python >= 3.14 found (set GATE_INFRA_PYTHON): every Bash and MCP call will ask this session` | Install Python 3.14 or set `GATE_INFRA_PYTHON`. |
| Per call | `Jev did not answer in time (1500 ms); asking you instead.` | Answer the prompt. Raise `GATE_JEV_TIMEOUT_MS` if it repeats. |
| Per call | `Jev returned http 503; asking you instead.` | Answer the prompt; check the key and `GATE_JEV_URL` if it repeats (a 401/403 means a wrong key). |
| Per call | `Jev could not be reached; asking you instead.` | Check the network. During a long outage set `GATE_ENABLED=0`. |
| Per call | `Jev's answer was malformed; asking you instead.` | Answer the prompt. If it repeats, check `GATE_JEV_URL`/`GATE_JEV_MODEL` and report it. |
| Per call | `call mentions a .env file and was not sent to Jev; Claude Code's permissions apply` | Nothing; this is information. |
| Per call | `could not read the hook input`, `no Python >= 3.14 found`, `internal error (...)`; reason `plugin-fault: ...` | The install is broken: fix Python or update the plugin. `GATE_ENABLED=0` turns the gate off meanwhile. |

Every warning except the `.env` one ends with `GATE_ENABLED=0 turns the gate off.`

## Configuration

Only env variables; no config file is needed. Set them in the `env` block of
`~/.claude/settings.json` for every project, or of a project's
`.claude/settings.json` to override per project. `GATE_LOG` and a file path in
`GATE_JEV_PROMPT` take an absolute path or one relative to the project:
`CLAUDE_PROJECT_DIR` (Claude Code sets it for hooks), else the git root of the
current directory.

| Variable | Default | Meaning |
| --- | --- | --- |
| `GATE_ENABLED` | `1` | `0` turns both hooks and the session warning off (no Python is started). The only off switch. |
| `GATE_ENV_GUARD` | `1` | `0` allows Read/Edit/Write of env files. |
| `GATE_LOG` | unset | File to append one JSON line per handled call: `ts`, `tool`, `command` or `input` (redacted; `null` when it mentions `.env`), `probabilities`, `risk`, `usage`, `latencyMs`, `decision` (`ask`/`none`), `reason`, `warning`, and `misconfigured` when a setting is invalid (a bad `GATE_ENABLED`/`GATE_ENV_GUARD` value included). Missing directories are created. Not logged: calls when the gate is off, plugin faults that stop before Python runs (no Python, engine exit), and `.env` blocks of Read/Edit/Write. |
| `GATE_INFRA_PYTHON` | unset | Python interpreter for the hooks and the check script (see [Install](#install)). A broken value is a plugin fault, with no fallback. |
| `TYPESAFE_API_KEY` | unset | Your Jev key. Sent only as a bearer token. Not set = misconfigured (no decisions, one session warning). |
| `GATE_JEV_URL` | `https://api.typesafe.ai/v1/systemone` | Endpoint. Must be `https` (plain `http` only to `localhost`/`127.0.0.1`/`[::1]`). |
| `GATE_JEV_MODEL` | `jev-latest` | Model name sent with the request. |
| `GATE_JEV_TIMEOUT_MS` | `1500` | Total time budget including retries on 429/529, 1-30000. Exceeding it is "Jev unavailable": the call asks. |
| `GATE_JEV_ESCALATE_THRESHOLD` | `0.5` | Ask when the risk (the `ask` choices together) is above this, 0-1. |
| `GATE_JEV_PROMPT` | `destructive` | Built-in prompt (`destructive`, `cloud-change`) or a path to your own prompt JSON. |
| `GATE_JEV_INSTRUCTIONS` | unset | Replaces the prompt's question text. |
| `GATE_JEV_CONTEXT` | unset | Replaces `context.project` (describe your project: what is precious, what is regenerable). |
| `GATE_JEV_POLICY` | unset | Replaces `context.policy` (what needs a person). |

Booleans accept `0/1`, `true/false`, `yes/no`, `on/off`. An invalid value of a
Jev variable counts as a misconfigured setting: no decisions, one warning at
session start. Empty values count as unset.

## Checking a call: `/destructive-guard:check`

Ask Claude *"would the gate ask for `git push --force`?"* or run the skill
directly. It runs a call through the hook's decision code with the effective
configuration and never runs the call:

```text
$ python3 lib/check.py 'rm -rf build'
Bash: rm -rf build
decision: ask
reason:   jev: destructive p=0.97 risk=0.97
```

A call check **always contacts Jev** (one request, the redacted call), so never
put secrets in it. With Jev unreachable it prints `decision: ask
(jev-unavailable: ...)` and the warning; with a `.env` mention or a
misconfigured setting it prints `decision: none` and why, without a request.
`--mcp <tool> '<json>'` checks an MCP call, `--config` (or no argument) prints
the effective configuration with the Jev API key redacted and the session
warning, `--json` gives machine-readable output.

## Jev

### What is sent

One HTTPS `POST` per gated call, with `authorization: Bearer $TYPESAFE_API_KEY`
and a JSON body:

- `model`, and the prompt: question id, instructions and criteria
  (choices with descriptions and examples).
- `state.context`: the prompt's `project` and `policy` text.
- For Bash: the **command** as text (redacted, at most 8000 characters) and
  notes on what was cut. Nothing is parsed and no script is read.
- For MCP: the **tool name** and its **input** as JSON (redacted, at most
  8000 characters; SQL string literals and dollar-quoted bodies replaced).

Redaction replaces credentials in URLs (`https://user:pass@`), `Bearer`/
`Basic`/`Token` values, values of secret-looking names (containing
`token`, `password`/`pwd`, `secret`, `api_key`, `auth...`, `credential`,
`private_key`) as `NAME=...`, `NAME: ...`, `--password ...` or JSON keys, all
shell env assignments, long hex strings and long base64-like runs. **A Bash
or MCP call that mentions an `.env` file is never sent**, whatever the
redaction would leave. Nothing else (no file, no conversation text) leaves the
machine.

### The question

Default prompt `destructive` (`jev/destructive.json`): *"Could this call
delete, overwrite or irreversibly change data or resources, on the local
machine or in any remote service?"* with choices `read_only` and
`reversible_change` (not risky) and `destructive` (ask). The risk is the
probability Jev gives the ask choices together. The prompt `cloud-change`
(`jev/cloud-change.json`) asks before any change to cloud resources,
infrastructure or databases instead. Select one with `GATE_JEV_PROMPT`.

A prompt file has the shape of `jev/destructive.json`: `question` (snake_case
id), `instructions`, `context` (`project`, `policy` strings), `criteria`
(choice -> description and examples), and `ask` (the choices whose
probabilities add up to the risk) and optionally `safe` (the other choices,
disjoint from `ask`).

## Migration from 0.8

Version 1.0 removes the rule gate. These variables are **ignored**, and the
session warning names the ones you still have set:

| Removed | Why |
| --- | --- |
| `GATE_JEV` | Jev is on whenever the gate is enabled (D2). No key = misconfigured. Use `GATE_ENABLED=0` to turn everything off. |
| `GATE_CHECKS`, `GATE_RULES_FILE`, `GATE_RULES`, `GATE_DISABLE_RULES` | No rule packs, no rules files, no rule ids. To change what asks, change the Jev prompt (`GATE_JEV_PROMPT`, `GATE_JEV_POLICY`, `GATE_JEV_CONTEXT`) or the threshold. |
| `GATE_STRICT` | No parser, so nothing is "unresolvable". |
| `GATE_JEV_ALLOW_THRESHOLD`, `GATE_JEV_CAN_LIFT` | Jev never lifts an ask; it can only add one. |
| `GATE_JEV_SCOPE` | Bash and MCP calls are both always sent. |
| `GATE_JEV_LOG` | One `GATE_LOG` now carries the Jev fields. |

| Situation | 0.8 | 1.0 |
| --- | --- | --- |
| Decision source | rule packs + script scan, Jev optional on top | Jev only |
| Jev says safe | lifts a rule ask | no decision |
| Jev unsure | the rule result stands | no decision |
| Jev down, timeout, HTTP or network error, malformed answer | the rule result stands, silent | **ask** with a warning |
| No key or invalid Jev setting | `ask gate-config:` on every call | no decision; one warning at session start |
| No suitable Python | `ask unparsable: python unavailable` | `ask plugin-fault: no Python >= 3.14 found` with a warning |
| Engine exception, bad hook input | `ask gate-config:` / `ask unparsable:` | `ask plugin-fault: <cause>` with a warning |
| `.env` in a Bash call | not sent; the rule result stands | not sent; no decision plus a warning |
| `.env` in an MCP call | `.env` lines dropped, the rest sent | not sent; no decision plus a warning |
| Scripts a command runs | collected and sent to Jev | not collected |
| Without a key | rules protect against destructive commands | only the `.env` guard |
| Ask reasons | start with a rule id (`git-push-force: ...`) | `jev: ...`, `jev-unavailable: ...`, `plugin-fault: ...` |

The `check` skill lost `--jev` (a call check always asks Jev) and `--cwd`.

## Troubleshooting

- **Why did it ask?** The reason says: `jev:` Jev judged it risky,
  `jev-unavailable:` Jev did not answer, `plugin-fault:` the install is broken.
  Run `/destructive-guard:check '<command>'`, or set
  `GATE_LOG=/tmp/gate.jsonl` to see risk, latency and warnings per call.
- **It never asks.** Run `/destructive-guard:check --config`: without
  `TYPESAFE_API_KEY` (or with another invalid Jev variable) the gate only
  guards `.env` files.
- **It asks too often or too rarely.** Move `GATE_JEV_ESCALATE_THRESHOLD`
  (lower = more asks) or describe your project with `GATE_JEV_CONTEXT` and
  `GATE_JEV_POLICY`.
- **Turn it off for one project:** `"GATE_ENABLED": "0"` in that project's
  `.claude/settings.json` `env`.

## Development

```bash
uv sync                                    # once: dev tools (pytest, ruff); needs uv and Python 3.14
uv run pytest plugins/destructive-guard    # tests (live Jev tests are skipped)
cd plugins/destructive-guard/formal && PATH="$HOME/.elan/bin:$PATH" lake build --wfail   # Lean model (needs elan)
```

The hook tests run against a local mock Jev, so no test needs a key.
`tests/live/` talks to the real Jev (marker `live_jev`; needs
`TYPESAFE_API_KEY`, run with `uv run pytest -m live_jev`); CI runs it in the
`jev-live` job. `tests/test_lean_conformance.py` checks the real hook against
the Lean model.

| Path | What |
| --- | --- |
| `hooks/hooks.json` | `Bash\|mcp__.*` -> `gate.sh`; `Read\|Edit\|Write` -> `env-guard.sh`; `SessionStart` -> `session-start.sh` |
| `hooks/gate.sh` | Interpreter lookup, runs the hook; asks with a warning on a plugin fault (no Python, engine failure) |
| `hooks/session-start.sh` | Runs the one-time configuration warning |
| `hooks/env-guard.sh` | Env-file guard |
| `lib/gate_main.py`, `lib/session_main.py`, `lib/check.py` | Entry stubs (check the Python version, then the package) |
| `lib/destructive_guard/config.py` | Env -> `GATE_ENABLED`, `GATE_ENV_GUARD`, `GATE_LOG` |
| `lib/destructive_guard/hook.py` | `GateHook`: the model's `hook` function |
| `lib/destructive_guard/session.py` | `SessionCheck`: the model's `sessionStart` |
| `lib/destructive_guard/outcome.py`, `down.py` | `HookOut` (cannot represent allow or deny) and `Down` |
| `lib/destructive_guard/jev/` | Jev configuration, prompts, redaction, request, client, judge |
| `lib/destructive_guard/check/` | The dry run behind `lib/check.py` |
| `jev/*.json` | Built-in Jev prompts |
| `formal/SimpleGate.lean` | The Lean 4 model (dev-only) |

### Formal model

`formal/SimpleGate.lean` is a Lean 4 model of the gate. It states and proves
theses S1-S10: the hook never allows or denies (S1), it asks exactly when the
plugin failed or Jev is configured, was reached and answered risky or did not
answer (S2, S10), it can only add prompts (S5), a misconfigured setting warns
once at session start and never per call (S9), and Jev sees only redacted
payloads that mention no `.env` file (S7). The code files carry tables mapping
each Lean function to the code that implements it, and
`tests/test_lean_conformance.py` runs the real hook against the model. Lean is
a development aid: it ships as source, you never build it, and the plugin runs
without it.

#### Claude Code assumptions

The proofs cover the hook's output. What Claude Code does with that output is
assumed, and was checked on 2026-11-22 against Claude Code **2.1.330**, using
the docs ([hooks](https://code.claude.com/docs/en/hooks),
[permissions](https://code.claude.com/docs/en/permissions),
[permission modes](https://code.claude.com/docs/en/permission-modes)) and a
throwaway hook run with `claude -p`. The full evidence table is in the header
of `formal/SimpleGate.lean`. Re-check these after a Claude Code upgrade that
touches hooks or permissions.

| # | Assumption | Result | Source |
| --- | --- | --- | --- |
| A1 | A hook `ask` makes Claude Code prompt even when a settings `allow` rule matches the call. | verified | [hooks: PreToolUse decision control](https://code.claude.com/docs/en/hooks#pretooluse-decision-control) |
| A2 | A settings `deny` wins over a hook `ask`; hooks cannot bypass deny. | verified | [permissions: precedence among rules, hooks and mods](https://code.claude.com/docs/en/permissions#precedence-among-rules-hooks-and-mods) |
| A3 | No hook decision leaves the call to Claude Code's permission rules and mode. | verified | [hooks: how a hook resolves](https://code.claude.com/docs/en/hooks#how-a-hook-resolves) |
| A4 | In `bypassPermissions` mode a hook `ask` still prompts. | verified by test only; the docs do not state it | [permission modes](https://code.claude.com/docs/en/permission-modes) |
| A5 | In auto mode a hook `ask` is handled like any `ask` (a prompt). Protected-path and unsandboxed-retry exceptions are not modelled. | verified | [hooks: ask decisions in auto mode](https://code.claude.com/docs/en/hooks#pretooluse-ask-decisions-in-auto-mode) |
| A6 | `systemMessage` from `PreToolUse` and `SessionStart` is shown to the user. | `PreToolUse` verified; `SessionStart` reaches the harness, display in the interactive UI is documented but not verified (no TTY in `-p`) | [hooks: JSON output](https://code.claude.com/docs/en/hooks#json-output) |
| A7 | One `PreToolUse` output may carry both `systemMessage` and `permissionDecision: "ask"`; both take effect. | verified in default, bypass, auto and dontAsk modes | [hooks: JSON output](https://code.claude.com/docs/en/hooks#json-output) |

If A1 or A4 stopped holding, the gate could not ask on allow-listed commands
or in bypass mode; S5 (the plugin never weakens a permission) would still
hold, because the plugin never allows.

## Design decisions and history

This section replaces the finished plan files that used to sit in
`docs/plans/` (the lean gate plan, one file per work package, included).
Git history keeps the full text.

### Origin and binding decisions

The gate started as an in-repo hook of a private project. It was ported
into this marketplace as a plugin of its own, separate from the ticket
workflow, so it protects every session, whether a person or an agent runs it.
Decisions that still bind:

- **Configured only through env variables.** Files are referenced *by* env
  variables (`GATE_JEV_PROMPT`, `GATE_LOG`), never required. Relative paths
  resolve against `CLAUDE_PROJECT_DIR`, else the git root.
- **Asks or has no opinion, never allows.** This held for every version and is
  now a proved thesis (S1, S5).
- **The `.env` guard lives here,** on by default.
- **Public repo hygiene.** No source-project names, no provider-specific
  prompts, and no named MCP servers. Tests use fictional tools (`cloudctl`,
  `mcp__db__execute_sql`).

Versions 0.x also had these decisions, all superseded by 1.0: the default
`destructive` rule pack had to be useful with zero config; the gate failed
closed on any configuration or engine error (`gate-config:`); `GATE_STRICT`
defaulted to `0` to keep a global gate quiet; Jev was an optional layer where a
confident answer won and the rules decided the rest.

### Python port (0.8, November 2026)

The engine was rewritten from JavaScript to Python, and the repo now contains
no JavaScript at all. Behaviour didn't change. How:

- **Runtime:** Python >= 3.14, standard library only, no venv for users.
  3.11 was considered and rejected; the cost is that most machines must
  install 3.14 first. Jev uses `urllib.request`.
- **Interpreter lookup:** `GATE_INFRA_PYTHON`, else `python3.14`, else
  `python3`, always run with `-I` so a planted `json.py` in the project
  can't hijack the gate. The entry stubs parse on Python 3.7, check the
  version and exit `3` when it's too old. The bash wrappers stay because
  "no interpreter -> ask" has to live outside the interpreter.
- **Tests first:** a contract suite and a 537-case corpus were made green
  against the old implementation, then each module was ported under them.
- **Latency** (full hook call, p50/p95): about 79/84 ms on Python against
  118/127 ms on the old runtime, before Jev was involved.

### Class refactor (November 2026)

Every function moved into a class, one class per file, with no change in
behaviour. The shared exceptions are in `errors.py`. Most of this code was
deleted again in 1.0; what remains (`jev/`, `config.py`, `hook.py`, the
class-per-file layout) keeps the shape.

### Formal model

A Lean 4 model of the gate was built first (November 2026), before the code was
changed to match it. An earlier plan to model the 0.8 rule gate was dropped
unbuilt. The model's theses S1-S10 are machine-checked, CI builds it with
`lake build --wfail`, the code files carry model-to-code tables, and
`tests/test_lean_conformance.py` runs the real hook against the model. Where
code and model disagree, fix the model first, then the code.

### The lean gate (1.0, November 2026)

Goal: the gate behaves exactly like the model. Jev alone decides: risky means
ask, otherwise no decision. When Jev cannot answer, a person decides. About
2 600 of 4 400 lines of the engine, shell parser, script scanner, rule loader
and fail-closed gate were deleted. Decisions, all taken with the author:

| # | Question | Decision |
| --- | --- | --- |
| D1 | Adopt the design, which drops the fail-closed guarantee? | Yes. Without a key the plugin only blocks `.env` files; the README says so plainly. |
| D2 | Is "Jev off" its own state? | No. `GATE_JEV` was removed. Jev is on whenever the gate is enabled; no key is a misconfiguration. `GATE_ENABLED=0` is the only off switch. |
| D3 | Warn on every call when misconfigured? | No. A `SessionStart` hook warns once; per call a misconfigured gate is silent. Runtime failures, `.env` skips and bad input warn per call. |
| D4 | Keep sending the scripts a command runs to Jev? | No (final). No parser and no script scanning: the command text goes to Jev as it is, redacted. `bash cleanup.sh` is judged by its text only; this is a documented blind spot. There is no parse-error case. |
| D5 | `check` without rules? | Kept. `--config` shows the Jev settings and session warnings; a call check always asks Jev and prints `ask`/`none` with the risk. |
| D6 | gated-dev planner's `## Gated operations` table | The column is `Gate`, filled from `check` only when `.claude/gated-dev.json` has `"dryRunGate": true` (each check is a Jev request that sends the command); otherwise `unknown`. |
| D7 | Claude Code assumptions in the model | Verified against the docs and a throwaway hook; recorded in [Claude Code assumptions](#claude-code-assumptions) with the version. |
| D8 | Ship Lean with the plugin? | Source only (about 10 KB), dev-only, `.lake/` gitignored. |
| D9 | MCP input that mentions `.env` | Skipped like Bash: nothing is sent, a warning is shown. (0.8 dropped the `.env` lines and sent the rest.) |
| D10 | Noise during a Jev outage | Every gated call prompts while Jev is down; that is intended, and the ask reason names the cause on every prompt. The warning names `GATE_ENABLED=0` as the way out. A rate limit for the warning was kept as an option if the trial finds it too noisy. |
| D11 | Jev configured but unavailable | Warning **and** ask (reason `jev-unavailable: <cause>`). The first draft fell back to Claude Code's own permissions there and was changed. `.env` calls are never sent, so they are not "unavailable". |
| D12 | Bad hook input, no Python, internal error | Ask with the error as the reason, always, with or without a key. `gate.sh` asks by itself when it finds no Python. S1 and S5 still hold. |

Work packages, in order: L0 model and Claude Code assumptions, LJ0 tests
against the real Jev (a CI job with the key as a secret, never on fork PRs),
L1 the Jev-only hook, session warning and conformance tests, L2 deletion of
the rule gate, L3 model-to-code cross-references, L4 the `check` skill on Jev,
L5 the gated-dev follow-up (`Gate` column instead of `Rule ID`), L6 this
README and 1.0.0.

Risks accepted: every Bash and MCP call now waits on Jev (default budget
1500 ms); every call's redacted text leaves the machine once a key is set;
an outage or a broken install means prompts, not silence; scripts and
`.env`-mentioning calls are judged only by Claude Code's own permissions.

### Known gaps

- Statically nothing is caught any more: Jev judges the text of the command
  or MCP input. Destructive work hidden in a script (`bash cleanup.sh`),
  `psql -f file.sql` or a SQL file fed to a client is invisible to it.
- The built-in prompts still tell Jev that a `scripts` field may be present;
  the gate no longer sends one.
