#!/bin/bash
# Fill the bench's data folder (paths.DATA): each benchmark's own repo at the commit this bench was built against,
# WebGames' site built, WebGames' task file, and a venv with Flask for ffserver.py; then fetch the playwright arm's
# pinned package and say what the agentbrowser and devtools arms still need. Safe to run again: it skips what is there.
set -euo pipefail
BENCH=$(cd "$(dirname "$0")" && pwd)
DATA=$(python3 -c "import sys; sys.path.insert(0, sys.argv[1]); import paths; print(paths.DATA)" "$BENCH")
mkdir -p "$DATA/results" "$DATA/webgames-data"

clone() {  # folder, repo, commit
  [ -d "$DATA/$1/.git" ] || git clone -q "$2" "$DATA/$1"
  git -C "$DATA/$1" checkout -q "$3"
}
clone MCP-Universe https://github.com/SalesforceAIResearch/MCP-Universe.git 48b4530
clone webgames https://github.com/convergence-ai/webgames 309866f
clone formfactory https://github.com/formfactory-ai/formfactory b7ef0d6
clone miniwob-plusplus https://github.com/Farama-Foundation/miniwob-plusplus 33c3b4d

[ -d "$DATA/webgames/webgames/dist" ] || (cd "$DATA/webgames/webgames" && pnpm i && pnpm build)
TASKS="$DATA/webgames-data/hf-test.jsonl"
[ -f "$TASKS" ] || curl -fsSL https://huggingface.co/datasets/convergence-ai/webgames/resolve/main/test.jsonl -o "$TASKS"
echo "d76d51fffb6e69dba399f658a1fca80b0501703d7e46e20fd6848f8d81a3a03c  $TASKS" | shasum -a 256 -c -
"$DATA/.venv/bin/python" -c "import flask" 2> /dev/null || (python3 -m venv "$DATA/.venv" && "$DATA/.venv/bin/pip" install -q Flask==2.3.3)

npx -y @playwright/mcp@0.0.82 --help > /dev/null  # the playwright arm's pinned version, fetched before any run
command -v agent-browser > /dev/null || echo "the agentbrowser arm needs: npm i -g agent-browser@0.38.1 && agent-browser install"
[ -x "$BENCH/../node_modules/.bin/chrome-devtools-mcp" ] || echo "the devtools arm needs browserd's own: npm ci, in $BENCH/.."
echo "data folder ready: $DATA"
