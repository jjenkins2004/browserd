# browserd

The browser MCP server and everything it needs: it owns one Chrome per profile and serves the tools a
Claude Code agent reads and drives pages with. Nothing here knows what a job is; it is handed a tab, and
records each `queue` call in that tab's record folder, `.run/calls/<profile>/<session>-<label>/<tab>/`. An
agent starts with `session_start {profile, label}`, and sees only its session's tabs; asked to, it makes or
deletes a profile with `profile_new` or `profile_delete`.

    ./start    the server, in the background; each profile's Chrome starts on its first use. The
               browserd page, http://127.0.0.1:9231/, makes profiles and shows their sessions and tabs
    ./stop     stops the server, which quits every profile's Chrome with it and closes every session
    ./restart  restarts the server alone: every Chrome keeps running and every session stays open

## Layout

    start, stop, restart    launchers for browser/service.py
    browser/                the server: tabs, the queue, and its own README
    package.json            chrome-devtools-mcp, pinned; `npm ci` once, into node_modules/
    .run/                   gitignored: server.pid, server.log, start.lock, state.db (profiles, sessions, tabs, needs_input), devtools-*.log, calls/<profile>/<session>-<label>/<tab>/
    tests/                  check_browser.py, check_server.py; throwaway.py, the live checks' own Chrome
    preview/                every state of the page's parts from made-up data: `python3 preview/preview.py`, then http://127.0.0.1:9320/

`browser/README.md` is the one to read before changing any of it: the Chrome proof, the tab ids, the
queue's checked steps, and what each tool refuses. Agents connect over HTTP at `http://127.0.0.1:9230/mcp`,
registered once at user scope so every Claude Code session has it:

    claude mcp add -s user --transport http browserd http://127.0.0.1:9230/mcp

After a restart that changed the tools, a Claude Code session already open lists them again at its next
browserd call and has them from its next turn; one that connected to a browserd from before
`tools.listChanged` needs `/mcp` to reconnect once. `browser/README.md` says why.

## Tests

    python3 tests/check_browser.py    66 checks: framing, a profile's Chrome proof, launch
    python3 tests/check_server.py     protocol, tab ids, sessions, focus, queue, recording, profiles, page, service

The live groups start a Chrome of their own on a new folder and a free port, and quit it after, so
they never touch a profile's Chrome; `check_server.py`'s queue checks need `npm ci` done.
