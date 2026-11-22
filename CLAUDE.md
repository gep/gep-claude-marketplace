# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

A **public** Claude Code plugin marketplace (`gep-claude-marketplace`): a catalog (`.claude-plugin/marketplace.json`) plus plugin directories under `plugins/`. Users consume it with:

```text
/plugin marketplace add gep/gep-claude-marketplace
/plugin install <plugin-name>@gep-claude-marketplace
```

## The concept: gates and workflow are separate

The two plugins are independent and installable on their own:

| Plugin | What it is | Ships |
| --- | --- | --- |
| `destructive-guard` | `PreToolUse` approval gate decided by the Jev classifier. Installed once, globally; protects every session (human or agent). Configured **only through env variables**; needs `TYPESAFE_API_KEY` to do anything beyond the `.env` guard. | hooks, Jev client and prompts, Lean model, `check` skill |
| `gated-dev-simple-workflow` | Ticket (file or Jira issue) → spec → plan → dev → review → PR pipeline (`/ticket`), and the subtasks of a Jira parent run in turn (`/ticket-flow`). Knows nothing about gate internals; works with or without the gate and treats any gate prompt or denial as a checkpoint, never something to work around. | `ticket` and `ticket-flow` skills, four stage agents, launcher, flow CLI, PR watcher, `Notification` hook |

### `destructive-guard` (`plugins/destructive-guard/`)

- `hooks/hooks.json`: `Bash|mcp__.*` → `hooks/gate.sh` (finds Python via `GATE_INFRA_PYTHON`, `python3.14`, `python3`, runs `lib/gate_main.py` with `-I`; asks with a warning when it finds no Python or the engine fails); `Read|Edit|Write` → `hooks/env-guard.sh` (blocks `.env*` files except `.env.example`); `SessionStart` → `hooks/session-start.sh` (one warning when the Jev setting is invalid, e.g. no `TYPESAFE_API_KEY`, or removed 0.8 variables are set).
- Jev only, no rules and no shell parser. The redacted call goes to Jev: risky (`risk > GATE_JEV_ESCALATE_THRESHOLD`) → `ask`, otherwise no decision, so Claude Code's own permissions apply. The hook never prints `allow` or `deny` (`outcome.HookOut` cannot represent them). **Jev configured but unavailable** (timeout, HTTP, network, malformed answer) and **plugin faults** (bad input, no Python, internal error) → warning **and** `ask`. A call mentioning `.env` is never sent: no decision plus a warning. Misconfigured (no key, bad URL or prompt) → no decision per call, one warning at session start.
- `lib/destructive_guard/config.py` is the single place that turns the gate's env vars into config; `jev/config.py` does the Jev ones. Main vars: `GATE_ENABLED` (the only off switch), `GATE_ENV_GUARD`, `GATE_LOG`, `GATE_INFRA_PYTHON`, `TYPESAFE_API_KEY`, `GATE_JEV_*` (URL, MODEL, TIMEOUT_MS, ESCALATE_THRESHOLD, PROMPT, INSTRUCTIONS, CONTEXT, POLICY). The 0.8 variables (`GATE_JEV`, `GATE_CHECKS`, `GATE_RULES*`, `GATE_DISABLE_RULES`, `GATE_STRICT`, …) are removed; `session.py` lists them for the warning.
- `formal/SimpleGate.lean` is the spec (theses S1–S10, Claude Code assumptions A1–A7 with the checked version). Where code and model disagree, fix the model first. `hook.py`, `session.py` and `outcome.py` carry model→code tables; `tests/test_lean_conformance.py` runs the real hook against the model.
- Optional prompts in `jev/*.json` (`destructive` default, `cloud-change`); a test enforces generic MCP names there.
- `skills/check` + `lib/check.py`: dry-run a command/MCP call through the hook (always contacts Jev) or print effective config (`/destructive-guard:check`).
- Live tests: `tests/live/` talks to the real Jev (marker `live_jev`, off by default, needs `TYPESAFE_API_KEY`; the CI `jev-live` job runs them, never for fork PRs). Run the normal suite with `env -u TYPESAFE_API_KEY` so nothing reaches the real service.
- The README is the user-facing reference for behaviour, warnings and configuration; keep it in sync (`tests/test_readme.py` checks variables and prompts).
- `formal/` is dev-only: ships as source but users never build it, and the plugin runs without Lean. Build with `elan` (`~/.elan`, toolchain pinned in `lean-toolchain`): `cd plugins/destructive-guard/formal && PATH="$HOME/.elan/bin:$PATH" lake build --wfail`; keep it free of errors and `sorry`. `.lake/` is gitignored.

### `gated-dev-simple-workflow` (`plugins/gated-dev-simple-workflow/`)

- `skills/ticket/SKILL.md` is the orchestrator: it runs in the main session, never writes product code, and drives one subagent per stage in a worktree per ticket (`git worktree`, Orca or iTerm2).
- `skills/ticket-flow/SKILL.md` (`/ticket-flow PARENT`, needs `tracker`) is the driver for one Jira parent: it picks the next ready subtask, launches its `/ticket` session, waits and repeats. The deterministic part is `scripts/flow.py` + `scripts/gated_dev/flow/`; flow state lives in the git common dir; settings in the `flow` config key; the pick rules are in `skills/ticket/references/flow.md`. Jira writes stay in `/ticket`.
- Agents in `agents/`: `ticket-spec` → `spec.md`, `ticket-planner` → `plan.md`, `ticket-dev` → commits + `dev-report.md`, `ticket-reviewer` → `review.md`. Each ends with `RESULT: OK <file>` or a `NEEDS_DECISION` block.
- File-based: the ticket is `docs/tickets/<KEY>/ticket.md` by default, and stages read/write files next to it. Optional Jira tracker (`tracker` in `.claude/gated-dev.json`, behaviour in `skills/ticket/references/jira.md`): the orchestrator fetches the issue into `ticket.md` and moves it To Do → In Progress → In Review → Done using the session's own Jira tools. The stage agents stay tracker-free. Never name an MCP server or its tools in shipped files (`tests/test_no_named_mcp.py`).
- Plan approval is always a stop point; after it the run commits, pushes and opens a PR with `gh`, then watches the PR (`scripts/watch_pr.py`, background): new feedback → follow-up round (`--address-review`, dev `MODE=fix-pr`, replies marked `<!-- gated-dev -->`), merge → notification and worktree removal (branch kept).
- With `destructive-guard` enabled the planner dry-runs its calls through `destructive-guard:check` and lists the ones that will ask in the plan's `## Gated operations` table.
- `hooks/notify.py` via `hooks/run-python.sh` (`Notification` hook; Python >= 3.14, silent without it): desktop notification for launcher-started sessions (`TICKET_FLOW_KEY`); macOS `osascript`, Linux `notify-send`, silent elsewhere; `GATED_DEV_NOTIFY=0` disables. Tests in `tests/test_notify.py`, `tests/test_run_python.py`.
- Per-project settings: `.claude/gated-dev.json` (see `skills/ticket/references/config.md`); pipeline contract in `skills/ticket/references/pipeline-spec.md`.

## Work in progress

Both plugins run on Python; the repo contains no JavaScript. Design decisions and history of each plugin (origin, binding decisions, Python port, class refactor) are in the "Design decisions and history" section of its README; read it before changing a plugin.
## Public repo hygiene (binding)

- Never write the source project's app name, ticket keys, cloud/database project ids or branch names here. Use neutral placeholders (`my-app-dev`, `apps/web`, `PROJ-1`).
- No provider-specific packs, rules, prompt text or tests for Supabase or GCP/gcloud. Tests use fictional tools (`cloudctl`, `blobctl`, `dwh`, `dbhost`, `acmecloud`).
- Keep MCP generic: no named MCP servers in prompts, packs or tests (the workflow's Jira support included); use made-up tool names (`mcp__tracker__editIssue`, `mcp__db__execute_sql`). A test enforces this for `jev/*.json`.

## Test and validate

There is no build step. Before committing:

```bash
claude plugin validate .                                    # marketplace + plugin manifests
jq . .claude-plugin/marketplace.json

uv sync                                                     # once: dev tools (pytest, ruff); needs uv and Python 3.14
uv run pytest                                               # Python tests
uv run ruff check .
uv run pytest -m live_jev                                  # optional: tests against the real Jev; needs TYPESAFE_API_KEY (skips without it)
```

`destructive-guard` needs Python >= 3.14 (standard library only; tests: `uv run pytest plugins/destructive-guard`). Keep existing tests green and add tests with every behaviour change. `gated-dev-simple-workflow` needs Python >= 3.14 too (launcher, PR watcher, notification hook; tests: `uv run pytest plugins/gated-dev-simple-workflow`); its skills and agents (Markdown) have none.

## Adding or changing a plugin

Every plugin lives in two places that must stay in sync:

1. **Plugin directory** `plugins/<name>/` with `.claude-plugin/plugin.json` (unique `name`, `version`, `description`, `author`, `license` Apache-2.0, `keywords`).
   - Skills: `skills/<skill-name>/SKILL.md` with YAML frontmatter `name` and `description`.
   - Agents in `agents/`, hooks in `hooks/hooks.json` (reference files via `${CLAUDE_PLUGIN_ROOT}`); commands and MCP config are also allowed.
2. **Catalog entry** in `.claude-plugin/marketplace.json` under `plugins`, with `source: "./plugins/<name>"` and the same `version` and `description`.

When a plugin changes, bump its `version` and update its description in **both** `plugin.json` and the catalog entry, and bump `metadata.version` in `marketplace.json`.

## Git

One commit per work package; push only when the user asks.
