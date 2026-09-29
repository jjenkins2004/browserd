#!/bin/bash
# Run suites one after another as one batch, detached from the shell that starts it and kept awake, so closing that
# shell or a Claude Code window does not stop it:
#
#     bench/chain.sh <name> <arms> "<suite>[:<run.py arguments>]"...
#     bench/chain.sh final next,playwright,devtools,agentbrowser "mcpuniverse:--k 2" formfactory botwall
#
# Suite <suite> runs as experiment <name>-<suite>, its log in the data folder's results/<name>-<suite>.log; each start,
# stop and end goes to results/chain.log. A suite whose batch stopped is resumed once, 2 minutes later, since
# Playwright's server can fail to start a few times in a row; if it stops again the chain halts.

BENCH=$(cd "$(dirname "$0")" && pwd)
DATA=$(python3 -c "import sys; sys.path.insert(0, sys.argv[1]); import paths; print(paths.DATA)" "$BENCH")
if [ $# -lt 3 ]; then
  sed -n 5,6p "$0"
  exit 1
fi
if [ -z "${CHAIN_DETACHED:-}" ]; then
  mkdir -p "$DATA/results"
  CHAIN_DETACHED=1 python3 -c "import subprocess, sys; subprocess.Popen(['caffeinate', '-i', 'bash'] + sys.argv[2:],
start_new_session=True, stdin=subprocess.DEVNULL, stdout=open(sys.argv[1], 'a'), stderr=subprocess.STDOUT)" \
    "$DATA/results/chain-$1.out" "$0" "$@"
  echo "chain $1 started; progress in $DATA/results/chain.log"
  exit 0
fi

name=$1 arms=$2
shift 2
log() { echo "$(date '+%Y-%m-%d %H:%M') $*" >> "$DATA/results/chain.log"; }

suite() {  # the suite, then run.py's own arguments
  local exp=$name-$1 out="$DATA/results/$name-$1.log" before
  for try in 1 2; do
    before=$(cat "$out" 2> /dev/null | wc -l)
    python3 "$BENCH/run.py" --exp "$exp" --suite "$1" --arms "$arms" "${@:2}" >> "$out" 2>&1
    if [ $? -eq 0 ] && ! tail -n +$((before + 1)) "$out" | grep -q "the batch stops"; then
      log "$exp done"
      return 0
    fi
    log "$exp stopped (try $try)"
    [ "$try" -eq 1 ] && sleep 120 && echo "--- resumed" >> "$out"
  done
  return 1
}

log "chain $name starts: arms $arms; $*"
for spec in "$@"; do
  IFS=: read -r which given <<< "$spec"
  # shellcheck disable=SC2086  # given holds run.py's arguments, split on spaces
  suite "$which" $given || { log "chain $name halts"; exit 1; }
done
log "chain $name done"
