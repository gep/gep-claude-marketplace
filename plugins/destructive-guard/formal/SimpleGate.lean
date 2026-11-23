/-!
# Lean gate: Clef, then Jev, decide; if neither can, a person does; with neither usable, halt

Model of the `destructive-guard` gate (decisions D1-D17: see "Design decisions and history" in the
README; D13-D17 add Clef and the halt and supersede D1, D2 and part of D3):

1. Two classifier backends in a fixed order, Clef first, Jev second (D14). Each one is `off`
   (its `GATE_*_ENABLED=0`), `bad` (enabled but misconfigured: no key, bad URL, prompt or value)
   or `ok`. Only `ok` backends are usable (D16).
2. call → the usable backends in order (redacted) → the first answer decides: risky: `ask` /
   not risky: no decision, so Claude Code's own permissions apply. A backend that does not
   answer (timeout, HTTP error, network error, malformed answer) falls through to the next
   (D15).
3. Every usable backend down → a warning (`systemMessage`) **and** `ask` (D11); the warning is
   the last backend's.
4. A plugin fault (unreadable hook input, no suitable Python, internal error) → a warning
   **and** `ask`, whatever the setting (D12).
5. A `bad` backend is skipped per call without a warning; `sessionStart` names it once (D3,
   D16). `GATE_ENABLED=0` is the only switch for the whole gate and is outside the model: the
   hooks do nothing.
6. No usable backend (D17): the session is halted. `sessionStart` says so, `promptGate`
   blocks every prompt, and every Bash/MCP call gets `deny` (with `continue: false`, A10).
   This is the only `deny`.
7. A call that mentions a `.env` file is never sent to a backend: no decision plus a warning;
   Claude Code's own permissions apply.

Everything sent is redacted first.

## Theses (all proved below)

| # | Name | Statement |
|---|------|-----------|
| S1 | `never_allows` | The hook never returns `allow`, and it returns `deny` **iff** the call is a tool call and no backend is usable (halt). |
| S2 | `asks_iff` | The hook asks **iff** it is a plugin fault, or a backend is usable, the call was sent (no `.env` reference) and the consulted chain either answered with `risk > escalate` or ended down for an asking reason. |
| S3 | `warning_decision` | With a warning, the hook asks iff the reason is `jevUnavailable` (any backend) or `pluginFault`; it denies only with `noClassifier`; the `.env` warning has no decision. |
| S4 | `fallback_is_claude` | With the `.env` warning the final permission is exactly Claude Code's own. |
| S5 | `never_weaker` | The final permission is never weaker than Claude Code's own (allow < ask < deny): the plugin can only add prompts, or deny. |
| S6 | `threshold_monotone` | Lowering `escalate` never removes an ask. |
| S7 | `sees_only_redacted` | The outcome depends on the backends only through redacted payloads that mention no `.env` file, sent to usable backends. |
| S8 | `answer_no_warning` | An answer from the chain never shows a warning. |
| S9 | `session_warns_iff`, `hook_only_usable` | `sessionStart` speaks iff a backend is `bad` or none is usable, and lists exactly the bad ones; the hook depends on the setting only through `usable s`, so a bad backend never makes it warn. |
| S10 | `unavailable_asks` | Every usable backend unavailable ⇒ ask with a warning: unreachable classifiers never silently let a call through. |
| S11 | `falls_back`, `clef_first`, `skips_unusable` | Clef usable and answering decides; Clef down and Jev usable ⇒ Jev's answer is judged; an unusable Clef is skipped, leaving Jev alone. |
| S12 | `halts_iff`, `halted` | Halt (session and prompt gate) iff no backend is usable; then every tool call denies and every prompt is blocked. |

## Assumptions about Claude Code (not proved by Lean)

Checked in L0 against the documentation and a throwaway hook; evidence and
Claude Code version are in the table at the end of this comment.

* A1 A hook `ask` makes Claude Code prompt even when a settings `allow` rule
  matches the call (`effective`).
* A2 A settings `deny` wins over a hook `ask` (`effective`).
* A3 No hook decision leaves the call to Claude Code's permission rules and mode.
* A4 `bypassPermissions` is modelled as Claude's own permission being `allow`;
  a hook `ask` still prompts there (so S5 holds in that mode too).
* A5 Auto mode: a hook `ask` is handled like any `ask` (see the evidence).
* A6 `systemMessage` from `PreToolUse` and `SessionStart` is shown to the user.
* A7 One `PreToolUse` output may carry both `systemMessage` and
  `hookSpecificOutput.permissionDecision: "ask"`; both take effect (D11, D12).
* A8 `SessionStart` cannot block or stop the session; its `systemMessage` is
  emitted (C0, for the halt).
* A9 `UserPromptSubmit` printing `{"decision":"block","reason":...}` rejects the
  prompt: the model never runs and the reason is shown (C0).
* A10 `PreToolUse` printing `permissionDecision: "deny"` with `continue: false`
  and `stopReason`: the call does not run and Claude stops; this wins over a
  settings `allow` rule and over `bypassPermissions` (C0).

### Evidence (L0, 2026-11-22, Claude Code 2.1.330)

Docs: hooks <https://code.claude.com/docs/en/hooks> (#pretooluse-decision-control,
#json-output, #how-a-hook-resolves, #pretooluse-ask-decisions-in-auto-mode),
permissions <https://code.claude.com/docs/en/permissions>
(#manage-permissions, #precedence-among-rules-hooks-and-mods), modes
<https://code.claude.com/docs/en/permission-modes>.
Empirical: throwaway hook via `claude -p --settings <tmp.json> --setting-sources ""
--output-format stream-json --verbose`. `-p` has nobody to answer a prompt, so an
`ask` shows as a `permission_denied` event with `decision_reason_type: "hook"`
and the call does not run; "prompted" below means exactly that.

| # | Result | Evidence |
|---|--------|----------|
| A1 | verified | allow rule `Bash(touch m_*)`: no hook runs the command; hook `ask` is prompted and the command does not run. Docs: "force a prompt". |
| A2 | verified | deny rule + hook `ask`: "Permission ... has been denied", not run. Docs: deny, then ask, then allow; hooks do not bypass deny. |
| A3 | verified | hook silent: allow rule runs the command; no rule asks via Claude Code's own check; deny rule denies; bypass runs it. Docs: "no output means the hook has no decision". |
| A4 | verified | `--permission-mode bypassPermissions`: hook silent runs; hook `ask` prompted, not run; deny rule still denies. The docs do not state the hook case; only the test shows it. |
| A5 | verified | `--permission-mode auto`: hook silent runs; hook `ask` prompted, not run. Docs: "a PreToolUse hook's ask normally sends the tool call to the permission prompt"; exceptions for protected paths and unsandboxed retries are not modelled. |
| A6 | partly verified | `PreToolUse` verified in stream-json (`informational`: "PreToolUse:Bash says: ..."); `SessionStart`: output reaches the harness (`hook_response` event), display in the interactive UI documented, not verified (no TTY in `-p`). |
| A7 | verified | one output with `systemMessage` and `permissionDecision: "ask"` gave both the informational message and the prompt (default, bypass, auto, dontAsk modes). |
| A8 | verified | C0, 2.1.330, `-p`: `SessionStart` output `{"systemMessage":"SS-MSG-A8"}` arrives as a `hook_response` event; with `continue:false`, `stopReason` and `decision:"block"` added the session still ran (assistant turn, `stop_reason: end_turn`). Display in the interactive UI is documented, not verified (no TTY). |
| A9 | verified | C0, 2.1.330, `-p`: `{"decision":"block","reason":"UPS-REASON-A9"}` gave `informational` "UserPromptSubmit operation blocked by hook: UPS-REASON-A9" with `prompt_submit_outcome.kind: "blocked_by_hook"`, `prompt_kept: false`, `num_turns: 0`, cost 0: no assistant turn. Interactive mode not verified (no TTY). |
| A10 | verified | C0, 2.1.330, `-p`, marker file `touch m_a10`: hook `deny` + `continue:false` + `stopReason` left no marker and no assistant turn after the call (result `stop_reason: "tool_use"`); `tool_result` "PreToolUse:Bash hook error: <reason>", `permission_decision` source `hook`. Same with an allow rule plus `--allowedTools` and with `--permission-mode bypassPermissions`. Control without hook in bypass ran the command. `stopReason` text itself is not shown in `-p` output; only the deny reason is. |

Also seen: `dontAsk` with an allow rule and a hook `ask` is prompted (denied
under `dontAsk`); not modelled.

-/

namespace SimpleGate

/-- Why there is no classifier answer, why the hook skipped the classifiers, or why the
session is halted. Each one is shown as a warning. -/
inductive Down where
  | timeout | http | network | malformed | envReference
  | badInput | pythonMissing | internalError | noClassifier
  deriving DecidableEq, Repr

/-- A usable backend (Clef or Jev; the name is kept from the Jev-only model) did not give a
usable answer. -/
def Down.jevUnavailable : Down → Bool
  | .timeout | .http | .network | .malformed => true
  | _ => false

/-- The plugin itself failed (D12). -/
def Down.pluginFault : Down → Bool
  | .badInput | .pythonMissing | .internalError => true
  | _ => false

/-- Reasons that come with an `ask`: everything except the `.env` skip and the halt. -/
def Down.asks : Down → Bool
  | .envReference | .noClassifier => false
  | _ => true

theorem Down.asks_iff (w : Down) :
    w.asks = true ↔ (w.jevUnavailable = true ∨ w.pluginFault = true) := by
  cases w <;> simp [Down.asks, Down.jevUnavailable, Down.pluginFault]

/-- What a classifier request returned (Clef and Jev share the request and answer shape).
`risk` is the per-mille sum of the prompt's ask choices (e.g. `cloud_or_data_change +
destructive`). -/
inductive JevResult where
  | down (why : Down)
  | answer (risk : Nat)
  deriving DecidableEq, Repr

/-- What is wrong with a misconfigured backend; shown once at session start. -/
inductive SessionIssue where
  | noKey | badUrl | badPrompt | badValue
  deriving DecidableEq, Repr

/-- The classifier backends, in the fixed order of D14. -/
inductive Backend where
  | clef | jev
  deriving DecidableEq, Repr

/-- One backend's configuration: switched off (`GATE_*_ENABLED=0`), enabled but misconfigured,
or usable (D13, D16). -/
inductive BState where
  | off
  | bad (why : SessionIssue)
  | ok
  deriving DecidableEq, Repr

def BState.isOk : BState → Bool
  | .ok => true
  | _ => false

/-- The whole classifier setting. `GATE_ENABLED=0` removes the gate altogether and is not a
setting here. -/
structure Setting where
  clef : BState
  jev : BState
  deriving DecidableEq, Repr

def Setting.state (s : Setting) : Backend → BState
  | .clef => s.clef
  | .jev => s.jev

/-- The fixed order (D14). -/
def order : List Backend := [.clef, .jev]

/-- The backends a call is sent to, in order: the `ok` ones. -/
def usable (s : Setting) : List Backend :=
  order.filter fun b => (s.state b).isOk

/-- The misconfigured backends and why, in order. -/
def issues (s : Setting) : List (Backend × SessionIssue) :=
  order.filterMap fun b =>
    match s.state b with
    | .bad w => some (b, w)
    | _ => none

/-- `GATE_JEV_ESCALATE_THRESHOLD` in per mille (shared by both backends). -/
structure Config where
  escalate : Nat := 500
  deriving Repr

/-- The hook protocol's decisions. `allow` exists so that S1 says something: the gate could
print it, it just never does. `deny` is only printed in the halted state. -/
inductive HookDecision where
  | none | ask | allow | deny
  deriving DecidableEq, Repr

/-- What the hook prints: a decision and an optional warning (`systemMessage`). -/
structure HookOut where
  decision : HookDecision
  warning : Option Down
  deriving DecidableEq, Repr

/-- A Bash command or MCP call (its text as sent to the classifiers), hook stdin that could
not be read, a missing interpreter (`gate.sh`) or an exception in the engine. -/
inductive Call where
  | tool (payload : String)
  | badInput
  | pythonMissing
  | crash
  deriving DecidableEq, Repr

/-- The redactor and the `.env` detector, left abstract. -/
structure Env where
  redact : String → String
  mentionsEnv : String → Bool

/-- Ask the backends in order: the first answer wins; when every one is down, the last one's
reason. An empty list cannot answer (the hook never consults it: it halts first). -/
def consult (q : Backend → String → JevResult) (p : String) : List Backend → JevResult
  | [] => .down .noClassifier
  | [b] => q b p
  | b :: b' :: bs =>
    match q b p with
    | .answer r => .answer r
    | .down _ => consult q p (b' :: bs)

/-- A classifier result → hook output. A risky answer asks; a safe answer and the uncertain
band give no decision. Unavailable classifiers and plugin faults ask with a warning (D11,
D12); the `.env` skip stays silent. Never `allow` or `deny`. -/
def judge (c : Config) : JevResult → HookOut
  | .down why => if why.asks then ⟨.ask, some why⟩ else ⟨.none, some why⟩
  | .answer risk => if c.escalate < risk then ⟨.ask, none⟩ else ⟨.none, none⟩

/-- The whole `PreToolUse` hook. `q b` is the request to backend `b`. -/
def hook (e : Env) (c : Config) (s : Setting) (q : Backend → String → JevResult) :
    Call → HookOut
  | .badInput => ⟨.ask, some .badInput⟩
  | .pythonMissing => ⟨.ask, some .pythonMissing⟩
  | .crash => ⟨.ask, some .internalError⟩
  | .tool p =>
    if usable s = [] then ⟨.deny, some .noClassifier⟩
    else if e.mentionsEnv p = true then ⟨.none, some .envReference⟩
    else judge c (consult q (e.redact p) (usable s))

/-- What `SessionStart` reports: the misconfigured backends, and whether the session is
halted. -/
structure SessionOut where
  issues : List (Backend × SessionIssue)
  halt : Bool
  deriving DecidableEq, Repr

/-- The `SessionStart` hook: silent when nothing is misconfigured and a backend is usable. -/
def sessionStart (s : Setting) : Option SessionOut :=
  if issues s = [] ∧ usable s ≠ [] then none
  else some ⟨issues s, decide (usable s = [])⟩

/-- The `UserPromptSubmit` hook: `true` blocks the prompt. It never contacts a backend. -/
def promptGate (s : Setting) : Bool :=
  decide (usable s = [])

/-! ## Claude Code's side -/

/-- Claude Code's own permission for a call (settings rules + mode). -/
inductive Perm where
  | allow | ask | deny
  deriving DecidableEq, Repr

def rank : Perm → Nat
  | .allow => 0
  | .ask => 1
  | .deny => 2

/-- Final permission: hook decision on top of Claude Code's own (A1-A4, A10). -/
def effective : HookDecision → Perm → Perm
  | .none, p => p
  | .ask, .deny => .deny
  | .ask, _ => .ask
  | .allow, .deny => .deny
  | .allow, _ => .allow
  | .deny, _ => .deny

/-! ## Lemmas about `judge`, `consult` and the setting -/

theorem judge_not_allow (c : Config) (r : JevResult) :
    (judge c r).decision ≠ .allow ∧ (judge c r).decision ≠ .deny := by
  cases r with
  | down w => cases w <;> simp [judge, Down.asks]
  | answer k =>
    by_cases h : c.escalate < k <;> simp [judge, h]

theorem judge_ask (c : Config) (r : JevResult) :
    (judge c r).decision = .ask ↔
      (∃ k, r = .answer k ∧ c.escalate < k) ∨ ∃ w, r = .down w ∧ w.asks = true := by
  cases r with
  | down w => cases w <;> simp [judge, Down.asks]
  | answer k =>
    by_cases h : c.escalate < k <;> simp [judge, h]

/-- A warning comes with an `ask` exactly when its reason asks. -/
theorem judge_warning (c : Config) (r : JevResult) (w : Down)
    (h : (judge c r).warning = some w) :
    ((judge c r).decision = .ask ↔ w.asks = true) ∧
    ((judge c r).decision = .none ∨ (judge c r).decision = .ask) := by
  cases r with
  | down v =>
    cases v <;> simp [judge, Down.asks] at h <;> subst h <;> simp [judge, Down.asks]
  | answer k =>
    by_cases hk : c.escalate < k <;> simp [judge, hk] at h

/-- Backends that agree on `x` give the same chain result. -/
theorem consult_congr (q₁ q₂ : Backend → String → JevResult) (x : String) :
    (l : List Backend) → (∀ b ∈ l, q₁ b x = q₂ b x) → consult q₁ x l = consult q₂ x l
  | [], _ => rfl
  | [b], h => by simp [consult, h b (by simp)]
  | b :: b' :: bs, h => by
    have hb : q₁ b x = q₂ b x := h b (by simp)
    have hr := consult_congr q₁ q₂ x (b' :: bs) (fun d hd => h d (List.mem_cons_of_mem _ hd))
    simp only [consult, hb, hr]

/-- When every backend in a non-empty chain is down for a reason with property `P`, the chain
is down for such a reason. -/
theorem consult_all_down (q : Backend → String → JevResult) (x : String) (P : Down → Prop) :
    (l : List Backend) → l ≠ [] → (∀ b ∈ l, ∃ w, q b x = .down w ∧ P w) →
      ∃ w, consult q x l = .down w ∧ P w
  | [], hne, _ => absurd rfl hne
  | [b], _, h => by simpa [consult] using h b (by simp)
  | b :: b' :: bs, _, h => by
    obtain ⟨w, hw, _⟩ := h b (by simp)
    have hr := consult_all_down q x P (b' :: bs) (by simp)
      (fun d hd => h d (List.mem_cons_of_mem _ hd))
    simpa [consult, hw] using hr

theorem consult_nil (q : Backend → String → JevResult) (x : String) :
    consult q x [] = .down .noClassifier := rfl

/-- The usable list in closed form. -/
theorem usable_eq (s : Setting) :
    usable s = (if s.clef = .ok then [Backend.clef] else []) ++
               (if s.jev = .ok then [Backend.jev] else []) := by
  cases hc : s.clef <;> cases hj : s.jev <;>
    simp [usable, order, Setting.state, BState.isOk, hc, hj]

theorem usable_nil_iff (s : Setting) : usable s = [] ↔ s.clef ≠ .ok ∧ s.jev ≠ .ok := by
  rw [usable_eq]
  by_cases hc : s.clef = .ok <;> by_cases hj : s.jev = .ok <;> simp [hc, hj]

/-- A misconfigured backend is never consulted. -/
theorem bad_not_usable (s : Setting) (b : Backend) (w : SessionIssue)
    (h : s.state b = .bad w) : b ∉ usable s := by
  simp [usable, h, BState.isOk]

/-- `issues` lists exactly the misconfigured backends. -/
theorem mem_issues (s : Setting) (b : Backend) (w : SessionIssue) :
    (b, w) ∈ issues s ↔ s.state b = .bad w := by
  cases b <;> cases hc : s.clef <;> cases hj : s.jev <;>
    simp [issues, order, Setting.state, hc, hj] <;> exact eq_comm

/-! ## Theses -/

/-- S1: never `allow`; `deny` exactly for a tool call in the halted state. -/
theorem never_allows (e : Env) (c : Config) (s : Setting) (q : Backend → String → JevResult)
    (call : Call) :
    (hook e c s q call).decision ≠ .allow ∧
    ((hook e c s q call).decision = .deny ↔ ∃ p, call = .tool p ∧ usable s = []) := by
  cases call with
  | badInput => simp [hook]
  | pythonMissing => simp [hook]
  | crash => simp [hook]
  | tool p =>
    by_cases hu : usable s = []
    · simp [hook, hu]
    · by_cases hm : e.mentionsEnv p = true
      · simp [hook, hu, hm]
      · have hj := judge_not_allow c (consult q (e.redact p) (usable s))
        simp only [hook, hu, hm, ite_false]
        exact ⟨hj.1, by simp [hj.2]⟩

/-- S2: the only ways to get a prompt from the plugin. -/
theorem asks_iff (e : Env) (c : Config) (s : Setting) (q : Backend → String → JevResult)
    (call : Call) :
    (hook e c s q call).decision = .ask ↔
      call = .badInput ∨ call = .pythonMissing ∨ call = .crash ∨
      ∃ p, call = .tool p ∧ usable s ≠ [] ∧ e.mentionsEnv p = false ∧
        ((∃ r, consult q (e.redact p) (usable s) = .answer r ∧ c.escalate < r) ∨
         ∃ w, consult q (e.redact p) (usable s) = .down w ∧ w.asks = true) := by
  cases call with
  | badInput => simp [hook]
  | pythonMissing => simp [hook]
  | crash => simp [hook]
  | tool p =>
    by_cases hu : usable s = []
    · simp [hook, hu]
    · by_cases hm : e.mentionsEnv p = true
      · simp [hook, hu, hm]
      · have hf : e.mentionsEnv p = false := by simpa using hm
        simp [hook, hu, hf, judge_ask]

/-- S3 -/
theorem warning_decision (e : Env) (c : Config) (s : Setting)
    (q : Backend → String → JevResult) (call : Call) (w : Down)
    (h : (hook e c s q call).warning = some w) :
    ((hook e c s q call).decision = .ask ↔
        (w.jevUnavailable = true ∨ w.pluginFault = true)) ∧
    ((hook e c s q call).decision = .deny → w = .noClassifier) ∧
    (w = .envReference → (hook e c s q call).decision = .none) := by
  rw [← Down.asks_iff]
  cases call with
  | badInput => simp [hook] at h; subst h; simp [hook, Down.asks]
  | pythonMissing => simp [hook] at h; subst h; simp [hook, Down.asks]
  | crash => simp [hook] at h; subst h; simp [hook, Down.asks]
  | tool p =>
    by_cases hu : usable s = []
    · simp [hook, hu] at h; subst h; simp [hook, hu, Down.asks]
    · by_cases hm : e.mentionsEnv p = true
      · simp [hook, hu, hm] at h; subst h; simp [hook, hu, hm, Down.asks]
      · simp only [hook, hu, hm, ite_false] at h ⊢
        have hw := judge_warning c _ w h
        refine ⟨hw.1, ?_, ?_⟩
        · intro hd; rcases hw.2 with h' | h' <;> simp [h'] at hd
        · intro he; subst he
          rcases hw.2 with h' | h'
          · exact h'
          · exact absurd (hw.1.1 h') (by simp [Down.asks])

/-- S4: the `.env` skip leaves the call exactly to Claude Code. -/
theorem fallback_is_claude (e : Env) (c : Config) (s : Setting)
    (q : Backend → String → JevResult) (call : Call) (claude : Perm)
    (h : (hook e c s q call).warning = some .envReference) :
    effective (hook e c s q call).decision claude = claude := by
  rw [(warning_decision e c s q call _ h).2.2 rfl]
  cases claude <;> rfl

theorem effective_monotone (d : HookDecision) (claude : Perm) (hd : d ≠ .allow) :
    rank claude ≤ rank (effective d claude) := by
  cases d <;> cases claude <;> simp_all [effective, rank]

/-- S5: the plugin only ever adds prompts (or, halted, denies). -/
theorem never_weaker (e : Env) (c : Config) (s : Setting) (q : Backend → String → JevResult)
    (call : Call) (claude : Perm) :
    rank claude ≤ rank (effective (hook e c s q call).decision claude) :=
  effective_monotone _ claude (never_allows e c s q call).1

/-- S6 -/
theorem threshold_monotone (e : Env) (c₁ c₂ : Config) (s : Setting)
    (q : Backend → String → JevResult) (call : Call) (h : c₂.escalate ≤ c₁.escalate) :
    (hook e c₁ s q call).decision = .ask → (hook e c₂ s q call).decision = .ask := by
  rw [asks_iff, asks_iff]
  rintro (h1 | h1 | h1 | ⟨p, hc, hu, he, ⟨r, hq, hr⟩ | hw⟩)
  · exact Or.inl h1
  · exact Or.inr (Or.inl h1)
  · exact Or.inr (Or.inr (Or.inl h1))
  · exact Or.inr (Or.inr (Or.inr ⟨p, hc, hu, he, Or.inl ⟨r, hq, Nat.lt_of_le_of_lt h hr⟩⟩))
  · exact Or.inr (Or.inr (Or.inr ⟨p, hc, hu, he, Or.inr hw⟩))

/-- S7: two sets of endpoints that agree on every redacted, `.env`-free payload at every
usable backend give the same hook output, so nothing else can influence (or reach) a
classifier, and an unusable backend never matters. -/
theorem sees_only_redacted (e : Env) (c : Config) (s : Setting)
    (q₁ q₂ : Backend → String → JevResult) (call : Call)
    (h : ∀ b ∈ usable s, ∀ p, e.mentionsEnv p = false → q₁ b (e.redact p) = q₂ b (e.redact p)) :
    hook e c s q₁ call = hook e c s q₂ call := by
  cases call with
  | badInput => rfl
  | pythonMissing => rfl
  | crash => rfl
  | tool p =>
    by_cases hu : usable s = []
    · simp [hook, hu]
    · by_cases hm : e.mentionsEnv p = true
      · simp [hook, hu, hm]
      · have hf : e.mentionsEnv p = false := by simpa using hm
        simp only [hook, hu, hm, ite_false]
        rw [consult_congr q₁ q₂ _ _ (fun b hb => h b hb p hf)]

/-- S8 -/
theorem answer_no_warning (e : Env) (c : Config) (s : Setting)
    (q : Backend → String → JevResult) (p : String) (k : Nat)
    (hm : e.mentionsEnv p = false) (hq : consult q (e.redact p) (usable s) = .answer k) :
    (hook e c s q (.tool p)).warning = none := by
  have hu : usable s ≠ [] := by
    intro hu; rw [hu, consult_nil] at hq; cases hq
  by_cases h : c.escalate < k <;> simp [hook, hu, hm, hq, judge, h]

/-- S9 (session side): `sessionStart` speaks iff a backend is misconfigured or none is usable,
and then lists exactly the misconfigured ones. -/
theorem session_warns_iff (s : Setting) :
    ((sessionStart s).isSome = true ↔ (issues s ≠ [] ∨ usable s = [])) ∧
    (∀ o, sessionStart s = some o → o.issues = issues s ∧ (o.halt = true ↔ usable s = [])) := by
  by_cases hi : issues s = [] <;> by_cases hu : usable s = [] <;>
    simp [sessionStart, hi, hu]

/-- S9 (hook side): the hook depends on the setting only through `usable s`; so a misconfigured
backend, which is never usable (`bad_not_usable`), never changes a decision or a warning. -/
theorem hook_only_usable (e : Env) (c : Config) (s₁ s₂ : Setting)
    (q : Backend → String → JevResult) (call : Call) (h : usable s₁ = usable s₂) :
    hook e c s₁ q call = hook e c s₂ q call := by
  cases call <;> simp [hook, h]

/-- S10: when every usable backend is unavailable, the hook asks with a warning; unreachable
classifiers never silently let a sent call through. -/
theorem unavailable_asks (e : Env) (c : Config) (s : Setting)
    (q : Backend → String → JevResult) (p : String)
    (hu : usable s ≠ []) (hm : e.mentionsEnv p = false)
    (hd : ∀ b ∈ usable s, ∃ w, q b (e.redact p) = .down w ∧ w.jevUnavailable = true) :
    ∃ w, w.jevUnavailable = true ∧ hook e c s q (.tool p) = ⟨.ask, some w⟩ := by
  obtain ⟨w, hq, hw⟩ := consult_all_down q (e.redact p) (fun w => w.jevUnavailable = true)
    (usable s) hu hd
  have ha : w.asks = true := (Down.asks_iff w).2 (Or.inl hw)
  exact ⟨w, hw, by simp [hook, hu, hm, hq, judge, ha]⟩

/-- S11: Clef first; when it is down, Jev's answer is judged (fallback, D15). -/
theorem falls_back (e : Env) (c : Config) (s : Setting) (q : Backend → String → JevResult)
    (p : String) (w : Down) (hc : s.clef = .ok) (hj : s.jev = .ok)
    (hm : e.mentionsEnv p = false) (hq : q .clef (e.redact p) = .down w) :
    hook e c s q (.tool p) = judge c (q .jev (e.redact p)) := by
  have hu : usable s = [.clef, .jev] := by simp [usable_eq, hc, hj]
  simp [hook, hu, hm, consult, hq]

/-- S11: a usable Clef that answers decides; Jev is not consulted. -/
theorem clef_first (e : Env) (c : Config) (s : Setting) (q : Backend → String → JevResult)
    (p : String) (k : Nat) (hc : s.clef = .ok) (hm : e.mentionsEnv p = false)
    (hq : q .clef (e.redact p) = .answer k) :
    hook e c s q (.tool p) = judge c (.answer k) := by
  by_cases hj : s.jev = .ok
  · have hu : usable s = [.clef, .jev] := by simp [usable_eq, hc, hj]
    simp [hook, hu, hm, consult, hq]
  · have hu : usable s = [.clef] := by simp [usable_eq, hc, hj]
    simp [hook, hu, hm, consult, hq]

/-- S11: an unusable Clef (off or misconfigured) is skipped: with Jev usable, the hook is the
Jev-only hook of 1.x. -/
theorem skips_unusable (e : Env) (c : Config) (s : Setting) (q : Backend → String → JevResult)
    (p : String) (hc : s.clef ≠ .ok) (hj : s.jev = .ok) (hm : e.mentionsEnv p = false) :
    hook e c s q (.tool p) = judge c (q .jev (e.redact p)) := by
  have hu : usable s = [.jev] := by simp [usable_eq, hc, hj]
  simp [hook, hu, hm, consult]

/-- S12: the session halts (session start and prompt gate) exactly when no backend is usable,
that is when neither is `ok`. -/
theorem halts_iff (s : Setting) :
    (promptGate s = true ↔ usable s = []) ∧
    ((sessionStart s).map SessionOut.halt = some true ↔ usable s = []) ∧
    (usable s = [] ↔ s.clef ≠ .ok ∧ s.jev ≠ .ok) := by
  refine ⟨by simp [promptGate], ?_, usable_nil_iff s⟩
  by_cases hi : issues s = [] <;> by_cases hu : usable s = [] <;>
    simp [sessionStart, hi, hu]

/-- S12: halted, every tool call is denied (whatever it mentions and whatever the backends
would say), every prompt is blocked and session start says so. -/
theorem halted (e : Env) (c : Config) (s : Setting) (q : Backend → String → JevResult)
    (hu : usable s = []) :
    (∀ p, hook e c s q (.tool p) = ⟨.deny, some .noClassifier⟩) ∧
    promptGate s = true ∧
    ∃ o, sessionStart s = some o ∧ o.halt = true := by
  refine ⟨fun p => by simp [hook, hu], by simp [promptGate, hu], ?_⟩
  exact ⟨⟨issues s, true⟩, by simp [sessionStart, hu], rfl⟩

/-! ## Examples (default `escalate = 500`), one per row of the conformance table.
Each `example` is preceded by a `-- example: NAME` line; `tests/test_lean_conformance.py` fails when a
name has no test, so a new example needs a test of the same name -/

private def env₀ : Env := ⟨id, fun s => s == ".env"⟩
/-- Every backend gives `r`. -/
private def q₀ (r : JevResult) : Backend → String → JevResult := fun _ _ => r
/-- Clef gives `rc`, Jev gives `rj`. -/
private def q₂ (rc rj : JevResult) : Backend → String → JevResult
  | .clef, _ => rc
  | .jev, _ => rj
/-- The 1.x setting: Jev alone. -/
private def jevOnly : Setting := ⟨.off, .ok⟩
private def both : Setting := ⟨.ok, .ok⟩

-- Jev alone answered
-- example: answer_0
example : hook env₀ {} jevOnly (q₀ (.answer 0)) (.tool "ls") = ⟨.none, none⟩ := by decide
-- example: answer_500
example : hook env₀ {} jevOnly (q₀ (.answer 500)) (.tool "ls") = ⟨.none, none⟩ := by decide
-- example: answer_501
example : hook env₀ {} jevOnly (q₀ (.answer 501)) (.tool "ls") = ⟨.ask, none⟩ := by decide
-- Jev alone unavailable: warning and ask
-- example: down_timeout
example : hook env₀ {} jevOnly (q₀ (.down .timeout)) (.tool "ls") = ⟨.ask, some .timeout⟩ := by decide
-- example: down_http
example : hook env₀ {} jevOnly (q₀ (.down .http)) (.tool "ls") = ⟨.ask, some .http⟩ := by decide
-- example: down_network
example : hook env₀ {} jevOnly (q₀ (.down .network)) (.tool "ls") = ⟨.ask, some .network⟩ := by decide
-- example: down_malformed
example : hook env₀ {} jevOnly (q₀ (.down .malformed)) (.tool "ls") = ⟨.ask, some .malformed⟩ := by decide
-- the chain: Clef first, Jev on fallback
-- example: clef_answers
example : hook env₀ {} both (q₂ (.answer 900) (.answer 0)) (.tool "ls") = ⟨.ask, none⟩ := by decide
-- example: clef_down_jev_answers
example : hook env₀ {} both (q₂ (.down .network) (.answer 900)) (.tool "ls") = ⟨.ask, none⟩ := by decide
-- example: both_down_last_warning
example : hook env₀ {} both (q₂ (.down .network) (.down .timeout)) (.tool "ls") = ⟨.ask, some .timeout⟩ := by decide
-- example: clef_off_jev_ok
example : hook env₀ {} ⟨.off, .ok⟩ (q₂ (.answer 900) (.answer 0)) (.tool "ls") = ⟨.none, none⟩ := by decide
-- example: clef_bad_jev_ok
example : hook env₀ {} ⟨.bad .badUrl, .ok⟩ (q₂ (.answer 900) (.answer 0)) (.tool "ls") = ⟨.none, none⟩ := by decide
-- no usable backend: halt
-- example: both_off_deny
example : hook env₀ {} ⟨.off, .off⟩ (q₀ (.answer 0)) (.tool "ls") = ⟨.deny, some .noClassifier⟩ := by decide
-- example: clef_off_jev_no_key_deny
example : hook env₀ {} ⟨.off, .bad .noKey⟩ (q₀ (.answer 0)) (.tool "ls") = ⟨.deny, some .noClassifier⟩ := by decide
-- example: halt_env_mention_denies
example : hook env₀ {} ⟨.off, .off⟩ (q₀ (.answer 0)) (.tool ".env") = ⟨.deny, some .noClassifier⟩ := by decide
-- example: prompt_gate_block
example : promptGate ⟨.off, .bad .noKey⟩ = true := by decide
-- example: prompt_gate_pass
example : promptGate both = false := by decide
-- session start
-- example: session_start_halt
example : sessionStart ⟨.off, .bad .noKey⟩ = some ⟨[(.jev, .noKey)], true⟩ := by decide
-- example: session_start_clef_bad
example : sessionStart ⟨.bad .badUrl, .ok⟩ = some ⟨[(.clef, .badUrl)], false⟩ := by decide
-- example: session_start_on
example : sessionStart jevOnly = none := by decide
-- `.env`: not sent, warning, no decision
-- example: env_mention_not_sent
example : hook env₀ {} jevOnly (q₀ (.answer 900)) (.tool ".env") = ⟨.none, some .envReference⟩ := by decide
-- plugin faults: ask with a warning, whatever the setting (halted included)
-- example: bad_input_on
example : hook env₀ {} jevOnly (q₀ (.answer 0)) .badInput = ⟨.ask, some .badInput⟩ := by decide
-- example: bad_input_misconfigured
example : hook env₀ {} ⟨.off, .bad .noKey⟩ (q₀ (.answer 0)) .badInput = ⟨.ask, some .badInput⟩ := by decide
-- example: python_missing
example : hook env₀ {} jevOnly (q₀ (.answer 0)) .pythonMissing = ⟨.ask, some .pythonMissing⟩ := by decide
-- example: crash
example : hook env₀ {} jevOnly (q₀ (.answer 0)) .crash = ⟨.ask, some .internalError⟩ := by decide
-- Claude Code's side
-- example: effective_ask_allow
example : effective .ask .allow = .ask := by decide
-- example: effective_ask_deny
example : effective .ask .deny = .deny := by decide
-- example: effective_none_allow
example : effective .none .allow = .allow := by decide
-- example: effective_deny_allow
example : effective .deny .allow = .deny := by decide

end SimpleGate
