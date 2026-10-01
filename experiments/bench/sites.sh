#!/bin/bash
# Start or stop the local sites the suites run against, each detached with its log in the data folder:
#
#     experiments/bench/sites.sh start     WebGames 4380, FormFactory 5055, MiniWoB++ 4390, clicks 4395, haystack 4396
#     experiments/bench/sites.sh stop
set -o pipefail
BENCH=$(cd "$(dirname "$0")" && pwd)
DATA=$(python3 -c "import sys; sys.path.insert(0, sys.argv[1]); import paths; print(paths.DATA)" "$BENCH")
PORTS="4380 5055 4390 4395 4396"

detach() {  # log name, folder, command...
  python3 -c "import subprocess, sys; subprocess.Popen(sys.argv[3:], cwd=sys.argv[2], start_new_session=True,
stdin=subprocess.DEVNULL, stdout=open(sys.argv[1], 'a'), stderr=subprocess.STDOUT)" "$DATA/$1.log" "$2" "${@:3}"
}

case "${1:-}" in
  start)
    for port in $PORTS; do
      lsof -tiTCP:"$port" -sTCP:LISTEN > /dev/null && echo "port $port is taken already; stop what holds it first" && exit 1
    done
    detach webgames-preview "$DATA/webgames/webgames" pnpm exec vite preview --host 127.0.0.1 --port 4380 --strictPort
    detach ffserver "$BENCH" "$DATA/.venv/bin/python" ffserver.py
    detach mwserver "$BENCH" python3 mwserver.py
    detach clickserver "$BENCH" python3 clickserver.py
    detach hayserver "$BENCH" python3 hayserver.py
    sleep 5
    for port in $PORTS; do lsof -tiTCP:"$port" -sTCP:LISTEN > /dev/null || echo "port $port did not come up; see $DATA/*.log"; done
    ;;
  stop)
    for port in $PORTS; do
      pids=$(lsof -tiTCP:"$port" -sTCP:LISTEN) && kill $pids
    done
    ;;
  *)
    echo "usage: experiments/bench/sites.sh start|stop"
    exit 1
    ;;
esac
