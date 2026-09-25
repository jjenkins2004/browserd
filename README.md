# browserd

The browser MCP server and everything it needs: it owns Joshua's School Chrome and serves the tools a
Claude session reads and drives pages with. Nothing here knows what a job is; it is handed a tab, and
records each `queue` call in that tab's record folder, `.run/calls/<tab>/`.

    ./start    the server and the School Chrome together, in the background
    ./stop     stops the server, which quits the School Chrome with it

## Layout

    start, stop             launchers for browser/service.py
    browser/                the server: tabs, the queue, and its own README
    package.json            chrome-devtools-mcp, pinned; `npm ci` once, into node_modules/
    .run/                   gitignored: server.pid, server.log, start.lock, devtools-*.log, calls/<tab>/
    tests/                  check_browser.py, check_server.py

`browser/README.md` is the one to read before changing any of it: the Chrome proof, the tab ids, the
queue's checked steps, and what each tool refuses. Agents connect over HTTP at `http://127.0.0.1:9230/mcp`,
registered once at user scope so every Claude Code session has it:

    claude mcp add -s user --transport http browserd http://127.0.0.1:9230/mcp

After a restart that changed the tools, a session already open lists them again at its next browserd
call and has them from its next turn; a session that connected to a browserd from before
`tools.listChanged` needs `/mcp` to reconnect once. `browser/README.md` says why.

## Tests

    python3 tests/check_browser.py    59 checks: framing, the School Chrome proof, launch
    python3 tests/check_server.py     protocol, tab ids, focus, queue, recording, service

The live groups need the School Chrome up, and `check_server.py`'s queue checks need `npm ci` done.
Nothing listening on 9223 skips them.
