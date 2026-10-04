#!/bin/sh
# One recorded run of a long task, in Claude Code as a fresh install has it: no user or project settings, CLAUDE.md,
# rules, memory, skills, hooks or plugins, browserd its only tool (less profile_new and profile_delete, which no task
# needs) and claude-sonnet-5-5 its model. The task's prompt is sent as the session's first message, and record.py
# captures the tab the agent works in until Claude Code exits.
#
#     experiments/long-tasks/run.sh capex|trip <name>
#
# The run's folder, <data>/<name>/, is where Claude Code runs, and holds the recording (frames/, frames.tsv, video.mp4)
# and record.log, and for trip flights-before.json, Google Flights as the run starts, for grade.py's --before. <data> is
# ../browserd-long-tasks beside the repo, or $BROWSERD_LONG_TASKS_DATA. The last line names the session's transcript,
# which grade.py trip reads.
set -e
HERE=$(cd "$(dirname "$0")" && pwd)
task=$1
name=$2
if [ ! -f "$HERE/$task/prompt.md" ] || [ -z "$name" ]; then
    echo "usage: $0 capex|trip <name>" >&2
    exit 2
fi
DATA=${BROWSERD_LONG_TASKS_DATA:-$HERE/../../../browserd-long-tasks}
run=$DATA/$name
if [ -e "$run" ]; then
    echo "$run exists: give this run a new name" >&2
    exit 2
fi
mkdir -p "$run"
label=$(sed -n 's/.*with the label "\([^"]*\)".*/\1/p' "$HERE/$task/prompt.md")
if [ "$task" = trip ]; then
    python3 "$HERE/grade.py" flights "$run/flights-before.json"
fi
id=$(uuidgen | tr '[:upper:]' '[:lower:]')

# Started in the background of a script, the recorder ignores Ctrl-C, which stays Claude Code's; SIGTERM stops it.
python3 "$HERE/record.py" "$label" "$run" > "$run/record.log" 2>&1 &
recorder=$!
cd "$run"
# The prompt goes first: --disallowedTools and --mcp-config take lists, and would take it as one more value.
claude "$(cat "$HERE/$task/prompt.md")" --model claude-sonnet-5-5 --setting-sources "" --tools "" \
    --allowedTools mcp__browserd --disallowedTools mcp__browserd__profile_new mcp__browserd__profile_delete \
    --strict-mcp-config --mcp-config '{"mcpServers": {"browserd": {"type": "http", "url": "http://127.0.0.1:9230/mcp"}}}' \
    --session-id "$id" || true
kill -TERM "$recorder"
wait "$recorder" || true
tail -1 "$run/record.log"
echo "transcript: $(find "$HOME/.claude/projects" -name "$id.jsonl" | head -1)"
