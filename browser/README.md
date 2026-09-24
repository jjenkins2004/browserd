# browser

## Module TL;DR

The browser MCP server: it starts and owns Joshua's School Chrome and serves tools to Claude
sessions over HTTP on `127.0.0.1:9230`, so application forms are read and filled inside his
logged-in session. `tab_open`, `tab_list` and `tab_close` manage tabs by short tab ids; `queue` runs
a list of steps on one tab through that tab's own chrome-devtools-mcp process. `queue` is handed a
workspace folder and records every call in its `run/`. Python standard library, plus Node for chrome-devtools-mcp (pinned in
`../package.json`; run `npm ci`).

    ../start    start the server and the School Chrome, in the background
    ../stop     stop the server, which quits the School Chrome

## Directory Layout

    browser/
      ws.py        RFC 6455 cut down to one local, trusted, text-only connection
      cdp.py       which Chrome, port proof, one websocket to it
      launch.py    starts the School Chrome, or adopts one already up
      tabs.py      short tab ids; open, list, close
      mcp.py       MCP over HTTP: JSON-RPC per POST, tool dispatch
      server.py    the server process: tools, Chrome lifecycle, pid file
      service.py   ../start and ../stop: background start, locked; stop by pid
      devtools.py  MCP client for one chrome-devtools-mcp process over stdio
      worker.py    one tab's process, paired with its page; Workers registry
      steps.py     the queue: load, check, run, report
      checked.py   the queue's checked steps: pick, expect
      record.py    one queue call's numbered files in a workspace's run/
    ../start, ../stop           launchers
    ../.run/                    gitignored: server.pid, server.log, start.lock, devtools-*.log
    ../package.json             chrome-devtools-mcp, pinned; node_modules/ is gitignored
    ../tests/check_server.py    protocol, tab ids, queue, recording, service; live tabs, queue
    ../tests/check_browser.py   framing, School Chrome proof, launch; live proof, tab load

## Core Abstractions & Shared Pieces

**`cdp.require()`** is the proof that port 9223 is the School Chrome, and every connection goes
through it: `cdp.Browser()` and `launch.launch()` both call it. The fixed facts it checks against
live at the top of `cdp.py`: port 9223, folder `~/Library/Application Support/Google/Chrome-School`
(not Chrome's default folder, where Chrome refuses a debugging port), profile `Default` (named
School), and Chrome's binary path. **`cdp.Browser`** is one browser-wide websocket; a command
carries a session id to reach a tab.

**`tabs.Tabs`** maps four-character tab ids (`k3f9`) to DevTools target ids. It opens a new
`cdp.Browser` for every operation, so every tab tool re-proves the Chrome. Ids live in the server
process only.

**`mcp.Server`** takes tools as dicts (`name`, `description`, `inputSchema`, `run(arguments)`).
`run` returns a string, a list of MCP content items, or a whole result dict, which is passed through
as is. Raising `mcp.ToolError` sends the agent a readable error result; any other exception becomes
an error result naming it, with the traceback in the log. `server.tab_tools(tabs, workers)` builds
the three tab tools, `server.queue_tool` the fourth; all turn `cdp.CdpError` into `ToolError`. The queue
takes the workspace as a folder — `_workspace` refuses one outside `server.REPO`, the folder holding this
project, or one that is not there — and once its arguments pass, makes a `record.Call`, writes what was asked,
and writes what came back through `_recorded`, a raised error included.

**`worker.Worker`** is one tab's `devtools.Devtools` process and its page id there, behind a lock,
so one tab's queues run in turn and different tabs run at once. `Worker.ensure()` starts and pairs
the process on the tab's first queue, and again after it dies. **`worker.Workers`** holds
one per tab id. `tab_close`, `tab_list` (for tabs no longer open) and a queue on a tab a
fresh listing no longer shows drop it; any other failure to reach a tab leaves its process and uids
alone.

**`steps.place_screenshots`** first gives each `take_screenshot` without a `filePath` one among the
call's files. **`steps.run`** sends each chrome-devtools-mcp step with the page id added, hands
`pick` and `expect` to `checked.run`, and returns a whole MCP result: a text report, any images, and
`isError` when a step failed.

**Server lifecycle**, in `server.serve()`: ask a chrome-devtools-mcp process for its tool list (no
browser needed), bind 9230, install SIGTERM/SIGINT handlers, write `.run/server.pid`, then
`launch.launch()` (start the School Chrome, or adopt it once `require` passes), then serve. If
`launch` fails after this start launched Chrome, that Chrome is sent SIGTERM. A watcher polls
`cdp.school_chrome()` every 2s, and when Chrome quits the server stops. When the server stops, every
tab's process is stopped; unless Chrome already quit, it sends `Browser.close` and waits for Chrome
to exit, and if Chrome has not exited 15s later the server logs it and stops anyway.

## Agent Gotchas & Invariants (⚠️)

- **An answer on 9223 proves nothing by itself.** `require` stops with a `CdpError` naming what is
  wrong and what to do when:
  - the folder's `Local State` is unreadable or lists any profile but `Default`;
  - nothing listens on 9223;
  - the School Chrome runs without its port (Chrome reads the port only at startup);
  - more than one process listens, since `127.0.0.1` may reach the unchecked one;
  - the listener is not the School Chrome;
  - no DevTools answer comes back;
  - `lsof` or `ps` cannot run, hangs past 10s, or prints an error, since a blocked `lsof` reads
    like an empty port.
- **The School Chrome is whoever holds `SingletonLock`.** That symlink in the folder ends in the
  owning pid, and `ps` must show that pid's command line starting with Chrome's binary, so a lock
  left by a crash is not trusted. A command line alone never is: `ps` joins arguments with spaces,
  and argv[0] can be set to anything. `Browser()` also asks the browser that answered for its own
  pid (`SystemInfo.getProcessInfo`), because `lsof` sees only this user's processes.
- **`require` proves the browser, not the tab.** An Incognito, Guest or other profile's window is
  another browser context, and `Tabs` neither lists such a tab nor gives it an id, only counting
  it. Chrome's default context is its last-used profile, and `Local State` reaches disk seconds
  after a profile is added, so for those seconds a new profile's tab would pass. No profile is ever
  to be added to this folder.
- **A School Chrome with no window open has no profile loaded.** macOS keeps Chrome running after
  its last window closes, and Chrome then unloads the profile, so `Target.getBrowserContexts` names
  no default context. With no page open either, `Tabs` lists no tabs rather than refusing, and
  `tab_open`'s `Target.createTarget` loads the profile again. No default context beside open pages
  is still refused, since those tabs cannot be told apart.
- **Tabs open in the background.** `Tabs.open` creates the tab with `background: true`, so the
  Mac's focus never moves; without it, `Target.createTarget` brings Chrome to the front. Opening
  goes `about:blank`, attach, `Page.enable`, then navigate: Chrome can finish a load before a later
  `Page.enable` would hear it.
- **Tabs opened by hand get an id at `tab_list`.** An id dies with the server, and a tab closed
  outside the server loses its id on the next use.
- **The server refuses any request with an `Origin` header, a `Host` other than
  `127.0.0.1:9230` or `localhost:9230`, or a body that is not `application/json`.** A web page
  open in the School Chrome could otherwise POST to it and drive the browser.
- **`../stop` only signals a pid whose command line runs `-m browser.server`.** A stale pid file
  can name a reused pid. `../start` holds `.run/start.lock` while it checks and spawns, and treats
  anything answering on 9230 under another server name as a refusal.
- **One chrome-devtools-mcp process runs one tool call at a time** (one `Mutex` in its
  `McpServer`, taken by every `ToolHandler.handle`), so a single shared process would put every
  tab in one line. Each tab gets its own, about 180MB each.
- **Its page ids mean nothing to DevTools.** `Worker._pair` sets a random value on the tab
  through the server's own connection (`window[Symbol.for('resume-tools-tab')]`), looks for it
  through chrome-devtools-mcp, URL matches first, then deletes it. The probe passes an empty
  `dialogAction` and no DOM wait, so it never answers a dialog raised in another tab.
- **A queue's step is `{"tool": name, ...arguments}`, never with `pageId`.** Tools in
  `steps.LEFT_OUT` (opening, listing, choosing and closing tabs; lighthouse; heap snapshots) are
  refused before anything runs, as is an unknown tool. The queue stops at the first failed step;
  the report names the steps not run and ends with a fresh snapshot. A relative `file` is read from
  the workspace.
- **A queue's `take_screenshot` without `filePath` is saved to `run/<n>-step<k>-screenshot.png`**
  (`.jpeg` or `.webp` for those formats), and the report gives that path, not an image:
  chrome-devtools-mcp attaches an image only when no path is given, and even then saves one of 2MB
  or more to a temporary file instead.
- **A chrome-devtools-mcp tool reports success once it has acted, not once the page took it,** so
  `checked.py` adds two checked steps that read the page back; `server.QUEUE_HELP` tells agents
  when to use each.
  - **`pick`** only clicks an option that typing listed, not one already on the page (a
    `<select multiple>` with the same words). It passes once the field holds `text`; a text box
    holding only what was typed also needs the clicked option gone, since an unchosen autocomplete
    reads as typed. A field that shows a short form passes if what it shows changed. On any failure
    it clears what it typed, so the text cannot read back as an answer.
  - **`expect`** matches exactly. A combobox with an empty text box reads as the text shown around
    it, and passes when one element there holds exactly the value, so "Hispanic or Latino" does not
    pass on "White (Not Hispanic or Latino)".
  - **Both are checked for their keys and types before any step of the queue runs.**
- **Element uids come from `take_snapshot` and live in that tab's process.** They stay valid
  across queue calls until the page navigates or the element goes away. When the process died and
  was restarted, the next report opens with a note that they are gone. A step failing with
  chrome-devtools-mcp's "No page found" (it renumbered its pages after reconnecting) stops the
  process, so the next queue re-pairs.
- **chrome-devtools-mcp's file tools may only touch the folder holding this project, `/private/tmp`
  (`--workspace`) and `$TMPDIR`, which it always adds.** Usage statistics and CrUX are off, and the
  performance, network and emulation tools are not loaded.
- **No tool types a password safely.** A queue's chrome-devtools-mcp steps refuse nothing, and every
  step's arguments are recorded in the workspace's `run/`.
- **Checks.**
  - **Without Chrome:** `framing` and `protocol` need nothing. `connecting` stands in for `lsof`,
    `ps`, the lock and the port, then runs the real `lsof` and `ps` against ports it holds itself.
    `tabs_offline` stands in for Chrome; `queue_offline` for chrome-devtools-mcp and a snapshot;
    `recording_offline` for Chrome, with a workspace in a temporary folder;
    `service_offline` for the spawned server, with real `ps`.
  - **Live:** the live groups need the School Chrome up. Nothing listening on 9223 skips them;
    anything else wrong with the port is a failure. `queue_live` also needs `npm ci` done, and skips
    without it, and records into a workspace of its own in a temporary folder; its `pick` and
    `expect` checks run on a local page whose dropdowns take only trusted input.
  - **Tabs:** live checks open scratch tabs and a throwaway browser context, work only inside
    them, and close them; a tab already open is never touched.
  - **Never automated:** `../start` and `../stop` are never run against the real School Chrome,
    because stopping quits it.
