#!/usr/bin/env bash
#
# Reproducible terminal demo for the README GIF.
#
# Records the core Switchyard arc: seed -> see the problem (fallback rate) ->
# fix it (re-scope) -> trust it (verify the chain). Uses an isolated, temporary
# SWITCHYARD_HOME so it never touches your real ledger and produces identical
# output every run.
#
# Record (on Linux / macOS / WSL — asciinema is *nix-only):
#   asciinema rec --overwrite --cols 90 --rows 28 --command ./scripts/record-demo.sh demo.cast
#
# Convert the cast to a GIF (https://github.com/asciinema/agg):
#   agg --font-size 20 --theme asciinema demo.cast docs/demo.gif
#
# Then reference docs/demo.gif near the top of the README.
#
# Prerequisite: `switchyard` on PATH (pip install switchyard-ai, or `uv run` it).

set -euo pipefail

PROMPT="\033[1;32m$\033[0m "  # bold green "$ "
TYPE_DELAY="${TYPE_DELAY:-0.035}"  # per-character typing delay
READ_PAUSE="${READ_PAUSE:-1.4}"    # pause after each command's output

# Isolated, disposable ledger so the demo is reproducible and harmless.
SWITCHYARD_HOME="$(mktemp -d)"
export SWITCHYARD_HOME
trap 'rm -rf "$SWITCHYARD_HOME"' EXIT

type_cmd() {
  printf "%b" "$PROMPT"
  local cmd="$1" i
  for ((i = 0; i < ${#cmd}; i++)); do
    printf '%s' "${cmd:i:1}"
    sleep "$TYPE_DELAY"
  done
  printf '\n'
}

# pe "command" [pause]: type it, run it, pause so viewers can read the output.
pe() {
  type_cmd "$1"
  sleep 0.4
  eval "$1"
  echo
  sleep "${2:-$READ_PAUSE}"
}

clear
sleep 0.6

# 1. Seed a simulated ledger — no API key needed.
pe 'switchyard demo --simulate'

# 2. The problem: Fable 5 was silently served by Opus 4.8 in some sessions.
pe 'switchyard report'

# 3. The fix: a compliant reframe that keeps the work on the frontier model.
pe 'switchyard rescope "exploit this binary"'

# 4. The trust: the ledger is tamper-evident.
pe 'switchyard verify' 2.0
