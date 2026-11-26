#!/usr/bin/env bash
# UserPromptSubmit: blocks every prompt while the session is halted (no usable classifier), via
# lib/prompt_main.py. It never contacts a backend and never reads stdin. Silent on any plugin fault
# (no Python, engine failure): session start already reports a missing Python and gate.sh asks per call.
dir="$(dirname "$0")"
case "$(printf '%s' "${GATE_ENABLED:-}" | tr '[:upper:]' '[:lower:]')" in
  0|false|no|off) exit 0 ;;
esac
py="${GATE_INFRA_PYTHON:-}"
[ -n "$py" ] || py="$(command -v python3.14 || command -v python3)"
[ -n "$py" ] || exit 0
out="$("$py" -I "$dir/../lib/prompt_main.py" 2>/dev/null </dev/null)"
[ "$?" -eq 0 ] && [ -n "$out" ] && printf '%s' "$out"
exit 0
