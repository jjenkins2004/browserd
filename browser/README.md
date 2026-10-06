# browser

## Module TL;DR

The browser MCP server: it starts and owns one Chrome per profile and serves tools to agents over HTTP on `ports.MCP`
(9230 unless `browserd setup` chose another), so pages are read and driven with that profile's logins; `browserd mcp`
relays an agent's MCP over stdio to that port, starting the server when nothing listens. An agent calls
`session_start {profile, label}` first and passes the session id it gets to every other tool but `profile_new` and
`profile_delete`. `tab_open`, `tab_list`, `tab_close` and `tab_needs_input` work on the session's own tabs by short tab
ids; `queue` runs a list of steps on one tab, and records each call in that tab's record folder,
`calls/<profile>/<session>-<label>/<tab>/` in the records folder. The browserd page (the dashboard the command
prints), on `ports.PAGE` (9231), shows every profile's sessions and tabs, and makes and deletes profiles. Python's
standard library, plus Node for chrome-devtools-mcp (pinned in `../package.json`; `npm ci`).

## Directory Layout

A folder per domain; each folder, and tools.py and server.py, imports only those above it in this list. A bare
"README.md" in a module's docstring means the nearest one up the tree.

    browser/
      system/        what differs by OS, behind one set of names; its own README
      config/        paths.py: ROOT, RUN (the records folder), the version; ports.py: the two ports
      protocol/
        ws.py        a websocket client for one local, trusted, text-only connection
        mcp.py       MCP over HTTP: one JSON-RPC message per POST, tool dispatch; log, which writes one line of the
                     server's log
      chrome/        each profile's Chrome; its own README
      records/
        state.py     state.db: the profiles, the sessions, the tabs and their needs_input marks
        record.py    one queue call's numbered files in its record folder
      tabs/          sessions, tab ids, and each tab's chrome-devtools-mcp; its own README
      steps/         the queue's steps; its own README
      dashboard/
        page.py      the browserd page's server: GET /, GET /state, a POST per action
        ui/          the browserd page itself; its own README
        preview/     every state of ui/'s parts from made-up data, never installed; its own README
      tools.py       the tools agents call, their descriptions, and the queue's body
      server.py      the server process (`python -m browser.server`): serves the tools and the browserd page
      cli/
        service.py   the browserd command; its help lists its commands
        installs.py  what each installer put where, for uninstall
        relay.py     browserd mcp: MCP over stdio, relayed to the HTTP port

## Core Abstractions & Shared Pieces

- **A tool** is a dict that `mcp.Server` serves (its `__init__` gives the shape). tools.py wraps every tool in
  `_refusing`, and every tool that takes a session in `_in_session` too; their docstrings say why.
- **A queue call** checks its tab id and that the tab is the session's, opens a `record.Call` in the tab's record
  folder, loads and checks its steps (`steps/`), proves the tab still open (`Tabs.target`), and runs the steps on the
  tab's `Worker` (`tabs/`) with `steps.run`; `queue_steps`' comments say what is recorded when. `tab_open` given steps
  runs them the same way on its new tab.
- **`state.State`** is state.db's one SQLite connection, shared by the server's threads; a `Profile` is a profile's
  row (`chrome/profiles.py` makes and deletes one), a `Session` one agent's task on one profile (`tabs/sessions.py` has
  its rules), a `Tab` one page of a profile's Chrome (`tabs/tabs.py` gives out its ids).
- **The records folder** is `paths.RUN`: `config/paths.py` says where, and `../README.md`'s Use what it holds. Record
  folders and devtools logs are never removed.
- **The ports** change only through `browserd setup` (`service.setup`). browserd never edits an agent's settings. An
  agent registered with `service.CONNECT` runs `browserd mcp` (`cli/relay.py`), which reads the port for every message,
  so only an agent connected over HTTP needs the new port.
- **The browserd page.** `page.Page` serves it on a thread of the server's own; `Page.snapshot`'s docstring says what
  `GET /state` answers, `Page.act` what each POST does, and `dashboard/ui/README.md` the browserd page itself.
- **`browserd uninstall`**: `service.uninstall` and `installs.py` say what it removes and keeps.
- **Server lifecycle**: `server.serve` starts the server in the order its comments explain; both ports are bound
  exclusively (`mcp.Exclusive`), so a second server fails before it writes server.pid. A session counts as paused after
  `sessions.PAUSE_AFTER` without a call, and another thread, every `server.PAUSE_POLL`, stops its tabs'
  chrome-devtools-mcp processes. No Chrome starts with the server, and one quitting leaves it up. A stop (`browserd
  stop`, Ctrl+C, and on a Mac SIGTERM) stops every tab's process, quits every running Chrome (`Chromes.quit_all`) and
  closes every open session and tab. A restart alone (`browserd restart`) stops only the tabs' processes: every Chrome
  keeps running and every session stays open for the next server, whose listings find the same tabs under the same
  ids. A server killed outright leaves the same.

## Agent Gotchas & Invariants (⚠️)

- **Every folder's `__init__.py` is empty but system/'s**, which holds its names.
- **No agent ends a session.** An agent would end one while its task still needed it, so only the user closes one,
  on the browserd page (Close session, or Delete profile for every session of its profile) or with `browserd stop`.
  No tool closes one, and `profile_delete` is refused while its profile has one open. Sessions of one profile share
  its logins and cookies: one signing out of a site signs every one out.
- **The MCP port refuses any request with an `Origin` header, a `Host` other than `127.0.0.1:<port>` or
  `localhost:<port>`, or a Content-Type other than `application/json`**: a web page open in any Chrome could otherwise
  POST to it and drive the browser.
- **The browserd page has a port of its own, `ports.PAGE`,** so the MCP port can go on refusing every request with an
  `Origin`. A request to the browserd page must carry its port's `Host` and no `Origin` but the browserd page's own,
  which every POST must carry; `GET /state` and every POST must also carry `X-Browserd-Token`, written into the
  browserd page as it is served and new each start. A web page cannot read the token, since the browserd page sends no
  CORS headers.
- **Claude Code cuts a tool's description at about 2,000 characters.** `tools.QUEUE_HELP` stays under it (a check
  holds it there); `tools.STEPS_HELP` and the step catalog, `steps.describe`, make up the `steps` argument's
  description, which Claude Code passes whole. `QUEUE_HELP` must still say the catalog's tools run only as steps:
  without its "Never pass pageId", "every tool a step may name" and "never tools to call by themselves" lines, agents
  call a step's name, like `navigate_page`, as a top-level tool (`../experiments/findings/queue-descriptions.md`, "What
  the rewrites broke, and why").
- **Claude Code reads the tool list when it connects, and again only when told it changed**, so the MCP server
  declares `tools.listChanged` and tells a session from before a restart that the list changed (`protocol/mcp.py`
  says how). Not the 404 the spec gives an unknown session id: Claude Code answers that by initializing again, without
  listing the tools. A turn already running, a subagent's included, keeps the old list. A Claude Code session that
  connected to a browserd from before `listChanged` never hears it, and needs `/mcp` to reconnect; the queue's refusal
  of an argument it does not take says so.
- **No tool types a password safely.** Every step's arguments are recorded in the tab's record folder, and a refused
  call's in server.log too.
