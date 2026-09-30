# browser

## Module TL;DR

The browser MCP server: it starts and owns one Chrome per profile and serves tools to Claude Code agents
over HTTP on `127.0.0.1:9230`, so pages are read and driven with that profile's logins. An agent first calls `session_start {profile, label}`, and passes the session id it
gets to every other tool. `tab_open`, `tab_list`, `tab_show` and `tab_close` manage the session's own tabs by
short tab ids; `queue` runs a list of steps on one tab through that tab's own chrome-devtools-mcp process, and
records every call in that tab's record folder, `../.run/calls/<profile>/<session>-<label>/<tab>/`. The
browserd page, at `http://127.0.0.1:9231/`, lists the profiles kept in `../.run/state.db` as a strip of
profile tabs and shows one profile's sessions and tabs at a time; it makes new profiles, opens a profile's Chrome,
shows, closes and hands over tabs, and closes sessions.
Python standard library, plus Node for chrome-devtools-mcp (pinned in `../package.json`;
run `npm ci`).

    ../start    start the server in the background; no Chrome starts with it
    ../stop     stop the server, which quits every profile's Chrome and closes every session
    ../restart  restart the server alone, leaving every Chrome and session as it is

## Directory Layout

    browser/
      ws.py        RFC 6455 cut down to one local, trusted, text-only connection
      cdp.py       which Chrome, port proof, one websocket to it
      launch.py    starts a profile's Chrome, or adopts one already up
      chromes.py   each profile's Chrome: started on first use, focus kept, quit at stop
      tabs.py      tab ids in state.db; open, list, show, close, hand over
      focus.py     the user's focus: which app has it; gives it back from tabs pages open
      system/      what differs by OS, behind one set of names: macos.py, windows.py
      mcp.py       MCP over HTTP: JSON-RPC per POST, tool dispatch
      server.py    the server process: tools, page, pid file
      service.py   ../start, ../stop, ../restart: background start, locked; stop and restart by pid
      devtools.py  MCP client for one chrome-devtools-mcp process over stdio
      worker.py    one tab's process, paired with its page; Workers registry
      steps.py     the queue: load, check, run, report; snapshot views
      checked.py   the queue's checked steps: pick, expect, type, paste, wait; fill_refused, read_fills
      dialogs.py   answers a dialog the moment it opens, for a handle_dialog step
      downloads.py hears a tab's downloads and where each went
      screenshot.py  a queue's take_screenshot of the viewport: CSS pixels, saved and sent back as an image
      pointer.py   the queue's pointer steps: move_at, click_down, click_up
      guard.py     the click guard: a press or keys stopped when the page changed since the agent's screenshot
      record.py    one queue call's numbered files in a folder
      profiles.py  Profile (name, folder, port); what a new profile is given
      state.py     .run/state.db: the profiles, sessions and tabs
      sessions.py  session ids, labels, record folder names, when a session is paused
      page.py      the browserd page on 9231: GET /state, and a POST per button
      ui/          the page itself: one file per part, and each part's states; its own README
    ../start, ../stop, ../restart  launchers; ../browserd.cmd is the three on Windows
    ../.run/                    gitignored: server.pid, server.log, start.lock, state.db, devtools-*.log, calls/<profile>/<session>-<label>/<tab>/
    ../package.json             chrome-devtools-mcp, pinned; node_modules/ is gitignored
    ../tests/check_server.py    protocol, tab ids, sessions, focus, queue, recording, profiles, page, service; live tabs, queue
    ../tests/check_browser.py   framing, a profile's Chrome proof, launch; live proof, tab load
    ../tests/throwaway.py       the live checks' own Chrome, on a new folder and a free port

## Core Abstractions & Shared Pieces

**`system`** is everything browserd asks of the OS, one set of names in `system/__init__.py`, each given by
`system/macos.py` or `system/windows.py`; nothing else runs an OS tool or calls an OS API. What each OS does:

| | macOS | Windows |
|---|---|---|
| Chrome's binary | `/Applications/Google Chrome.app` | `BROWSERD_CHROME`, else App Paths, else Program Files |
| profile folders beside Chrome's own | `~/Library/Application Support/Google` | `%LOCALAPPDATA%\Google` |
| the folder's owner | `SingletonLock`'s pid, `ps` showing Chrome's binary | `Chrome_MessageWindow` titled with the folder; its process's image is Chrome's, no `--type` |
| a port's listeners | `lsof` | the TCP table (`GetExtendedTcpTable`), IPv4 and IPv6 |
| a command line | `ps` (words joined by spaces) | `NtQueryInformationProcess`, split by `CommandLineToArgvW` |
| starting Chrome | `open -gna`, no window | the binary, detached, its first window shown without the focus |
| the app in front, bringing one | `lsappinfo`, AppKit through `osascript` | the foreground window; `SetForegroundWindow`, shared input, `SwitchToThisWindow` |
| `../stop`, `../restart` | SIGTERM, SIGHUP | two named events per checkout and user |
| the command key (paste, `Meta+A`) | Meta (Command) | Control |

A process the OS will not let this user read raises `system.Unanswered` (a `CdpError` to callers), never reads as
one that exited. `system.remote_path` refuses a network share, `\\?\` or `\\.\` path before anything opens it,
which on Windows would send the user's credentials to that host.

**`cdp.require(profile)`** is the proof that a profile's port is that profile's Chrome, and every connection
goes through it: `cdp.Browser(profile)` and `launch.launch(profile)` both call it. It checks against the
profile's port and folder (never Chrome's default folder, where Chrome refuses a debugging port), the one
Chrome profile `cdp.PROFILE` (`Default`), and Chrome's binary path. A Chrome not running at all is
`cdp.NotRunning`, found before the folder's `Local State` is read, since a new profile's folder stays empty until its Chrome
first starts. **`cdp.Browser`** is one browser-wide websocket; a command carries a CDP session id (`Target.attachToTarget`'s `sessionId`) to reach a tab,
`pid` is that Chrome's, as `SystemInfo.getProcessInfo` gave it, and `profile` is whose it is; `call`'s `wait` gives
one command a timeout of its own in place of `cdp.CALL_WAIT` (a pointer step's 5s).

**`chromes.Chromes`** is where the server starts and quits each profile's Chrome; it keeps no list of running
ones, asking `cdp.owner` each time. `ensure(profile)` starts a profile's
Chrome with `launch.launch` unless it is up, one start at a time per folder, and sends SIGTERM to a Chrome
it launched but never saw answer. It then runs `focus.keep` for that Chrome on a connection of its own: a
thread that ends when the Chrome quits and starts again with it. `adopt` does the same for every profile's
Chrome already running as the server starts, `window` is the page's Open Chrome, and `quit_all` quits every
running one when the server stops, once any start under way has finished; no Chrome starts after it.

**`profiles.Profile`** is one Chrome: a name, its folder (`--user-data-dir`) and its debugging port.
**`state.State`** keeps them in `../.run/state.db`, with the sessions and tabs, one SQLite connection shared by
the server's threads; the file is gitignored, so each person's profiles stay theirs, and a name is taken
whatever its case. Not a Chrome
profile: a folder taken over must hold no Chrome profile but Chrome's `Default` one.
**`profiles.make`**, which only the page's New profile button calls, adds one: either a new folder,
`<system.CHROME_DATA>/Chrome-<name>` (`~/Library/Application Support/Google/Chrome-<name>` on a Mac,
`%LOCALAPPDATA%\Google\Chrome-<name>` on Windows), made empty, or one of `profiles.free_folders` (a
`Chrome-*` folder there that no profile uses) taken over with its logins, once `cdp.check_folder` passes.
Its port is the one that folder's Chrome already runs with, when no profile has it; otherwise the lowest from 9223
to 9299 that no profile has, the server does not hold (9230, 9231), and nothing listens on, on any address. A
name Windows keeps for a device (`CON`, `NUL`, `COM1`...) is refused on every OS, since it names a folder of
`.run/calls`.

**`page.Page`** serves the page on 9231 on a thread of the server's own. `GET /` is `page.assemble()`: `ui/page.html` with
`ui/page.css` and the scripts of `page.PARTS` put in, read again on every load. `GET /state` gives
every profile with its Chrome's pid (or `null`, not running), `error` when its Chrome's tabs could not be
listed, its open sessions (active or paused) with their
tabs, the tabs no session owns, and its last `page.CLOSED_SHOWN` (10) closed sessions, from one
`Tabs.listing` per profile, which also keeps `state.db` in step with each Chrome; and the folders a new profile
may take over. Each button POSTs: `/profiles` a new profile, `/open` a window of a profile's Chrome in front (at
a URL or blank), `/show` and `/close-tab` any tab, `/close-session` a session and every tab of it, and
`/handover` a tab no session owns to an open session of its profile; `/close-paused` (every paused session and its
tabs) is still served, though no button posts it. A tab of a session closed as it opened (a `tab_open` or popup under way) is shown with those by hand,
so it can still be closed or handed over. A `ProfileError`,
`page.Refused` or `cdp.CdpError` is answered as `{"error": ...}` for the page to show.

**`state.Session`** is one agent's task on one profile: a six-character id (`k3f9x2`), the profile's name, a
label, and its last call. `sessions.py` holds what an id and a label may be, the record folder they name
(`<id>-<the label's words, dashed>`), and `sessions.paused`: no call for `sessions.PAUSE_AFTER` (30 minutes).

**`tabs.Tabs`** gives each page of a profile's Chrome a four-character tab id (`k3f9`), kept in `state.db`
as a `state.Tab` with its target id and its session, and never given out again. `list`, `open`, `target`, `show`
and `close` take the asking session, and a tab of any other session is "no tab of this session"; the page asks
as `None`, and reaches every tab. It opens a new `cdp.Browser` for every
operation, through its `connect`, so every tab tool re-proves the Chrome; `open` first calls its `start`, which
starts the profile's Chrome, and a Chrome not running lists no tabs. Each listing (`_sync`) marks closed every
tab whose page is gone, and gives each new page but the placeholder (below) a tab: the session of the page that opened it (its
`openerId`, followed through a popup's own popups and through an opener since closed), or no session's.

**`mcp.Server`** takes tools as dicts (`name`, `description`, `inputSchema`, `run(arguments)`).
`run` returns a string, a list of MCP content items, or a whole result dict, which is passed through
as is. Raising `mcp.ToolError` sends the agent a readable error result; any other exception becomes
an error result naming it, with the traceback in the log; a client dropping its connection is one
log line. Every tool call is one log line; one a tool refuses (`ToolError`), one naming no such tool, and
one whose params or arguments are not an object also name what they were given. `server.tab_tools(state, tabs,
workers, queue)` builds `session_start` and the four tab tools, `server.queue_tool` the queue;
`server.queue_steps` makes the queue's body, which the queue and `tab_open` share: `tab_open`, given `steps`, runs
them on the new tab through it (recorded as that tab's queue call), `steps.QUEUE_MOST` counted from the start of
`tab_open`, and answers with its tab id, title and URL, then the report, or those and why its steps did not run. Agents
read a new tab right after opening it (measured in benchmarks: most with a lone `take_snapshot` queue, and 8 of 11
calls of a queue step's name as a top-level tool came right after `tab_open`). Without `queue` (as most checks build
them), `tab_open` lists no `steps` argument, and ignores one given. `tab_close` takes `tabs`, a list of tab ids, and closes each
it can in one call, naming any it could not, and why, in an error result: agents told to close the browser close their tabs
as they finish, and one call per tab was 21 to 27% of their calls in two benchmark runs. All turn `cdp.CdpError`
into `ToolError`, and all but `session_start` run through `_in_session`, which refuses a missing, malformed,
unknown or closed session and moves its last call to now as the call starts and as it ends. The queue records
into `server.CALLS/<profile>/<session>-<label>/<tab>/`, so it refuses a tab argument not shaped like a tab id
(`tabs.is_id`) before that reaches a path, and a tab not the session's before anything is written. It then
makes a `record.Call` and writes what was asked as sent, so a queue refused for its other arguments or its
steps is recorded too, with a `file`'s steps added once read; once they pass, what was asked is rewritten as
the steps it runs, and what came back is written through `_recorded`, a refusal or raised error included.

**`worker.Worker`** is one tab's `devtools.Devtools` process and its page id there, behind a lock,
so one tab's queues run in turn and different tabs run at once. The process is pointed at the tab's
profile's Chrome (`--browser-url`), and `Worker.connect` is how the queue's dialog answerer and paste
reach the tab. `Worker.ensure()` starts and pairs
the process on the tab's first queue, and again after it dies, then selects the tab's page there (`select_page` without
`bringToFront`): chrome-devtools-mcp sets its own timeouts on a page only once it is selected, 5s for a click or fill
to finish, its wait for the element included, and 10s for a navigation, and one it never selected keeps Puppeteer's 30s
for both (measured: a fill on a date's Month part failed after 30.1s unselected, 5.1s selected). A navigation that
runs out its timeout still reports ok, on a page half loaded, so `steps.run` gives a `navigate_page` that names no
`timeout` `steps.NAVIGATE_TIMEOUT` (30s), what it had before. The first report on a tab older than this
server (carried over: `Workers.get`'s `made` is before `Workers.started`) also says its uids are gone, since an
earlier server's process may have given some out. It also keeps the last viewport screenshots its tab's replies gave
the agent, and where the focus was as the last one went out, for `guard.Guard`. **`worker.Workers`** holds
one per tab id. `tab_close`, the page's Close and Close session, `/close-paused`, `tab_list` (for tabs found closed) and a
queue on a tab found closed drop it;
any other failure to reach a tab leaves its process and uids alone. `Workers.pause` stops the processes of a
paused session's tabs but keeps each dead one, so its next queue starts a new one and says the uids are gone;
it asks once more, as each queue lets go of its tab, whether the session is paused still.

**`steps.place_screenshots`** first gives each `take_screenshot` without a `filePath` one among the
call's files. **`steps.run`** sends each chrome-devtools-mcp step with the page id added (and
without take_snapshot's own `under`, `full`, `find` and `after`), hands each checked step to `checked.run`, passes
every reply's text, less chrome-devtools-mcp's `## Pages` list of every tab in Chrome and its note on
which page it now selects, and with a `Page navigated to <url>.` line cut to the url's query and fragment
when the queue's navigation line before it named the same scheme, host and path and the url has a query
(`navigate_page` starts over), through **`steps.view`**, and returns a whole MCP result: a text report,
any images, and `isError` when a step failed or the queue stopped at `steps.QUEUE_MOST`. A reply
longer than `steps.REPLY_MOST` (40,000 characters) is cut after its last whole line within that (mid-line
when that line would leave less than half), the
whole of it saved as `<n>-step<k>-reply.txt`. A failed queue's view of the page now, and its failed
step's reply, are cut the same way to fit `steps.ERROR_MOST` (below), the view saved as
`<n>-page-now-reply.txt`.

**Server lifecycle**, in `server.serve()`: open `.run/state.db`, ask a chrome-devtools-mcp process for its
tool list (no browser needed), bind 9230 and the page's 9231 (exclusively: on Windows `SO_REUSEADDR` would let a
second server bind beside the first), listen for `../stop` and `../restart` (`system.listen_for_stop`), write
`.run/server.pid`, `chromes.adopt` every profile's Chrome already running, then serve the page on a thread and
the tools. Record folders and devtools logs are never removed. Another thread looks every `server.PAUSE_POLL`
(60s) for sessions paused, and `Workers.pause`s their tabs. No Chrome starts with the server, and one quitting
leaves the server up. When the server stops, every tab's process is stopped, `chromes.quit_all` sends each
running Chrome `Browser.close` and waits for it to exit (one not exited 15s later is logged, and the server
stops anyway), and every open session and tab is marked closed. On a restart alone, which `../restart` asks for
(SIGHUP on a Mac, the restart event on Windows), only the tabs' processes are stopped (a stop, SIGTERM or SIGINT
at any point still quits everything): every Chrome keeps
running and every session stays open for the server that `../restart` starts next, whose listings find the
same tabs under the same ids. A crash leaves the same.

## Agent Gotchas & Invariants (⚠️)

- **An answer on a profile's port proves nothing by itself.** `require` stops with a `CdpError` naming what is
  wrong and what to do when:
  - nothing listens on the port and no Chrome holds the folder (`cdp.NotRunning`);
  - the folder's `Local State` is unreadable or lists any Chrome profile but `Default` (a Chrome that has
    not yet shown a tab lists none, which passes);
  - the profile's Chrome runs without its port (Chrome reads the port only at startup);
  - more than one process listens, since `127.0.0.1` may reach the unchecked one;
  - the listener is not the profile's Chrome;
  - the profile's Chrome was started without `cdp.INPUT_FLAG`;
  - no DevTools answer comes back;
  - the OS cannot say who listens or what a process is (`lsof` or `ps` cannot run, hangs past 10s, or
    prints an error, since a blocked `lsof` reads like an empty port; Windows will not let this user read the
    process).
- **A profile's Chrome is whoever holds its folder as Chrome itself tells.** On a Mac that is `SingletonLock` in
  the folder: that symlink ends in the owning pid, and `ps` must show that pid's command line starting with
  Chrome's binary, so a lock left by a crash is not trusted. A command line alone never is: `ps` joins arguments
  with spaces, and argv[0] can be set to anything. On Windows it is the message-only window of class
  `Chrome_MessageWindow` titled with the folder, which a second Chrome given the folder finds and hands its launch
  to; its process must run Chrome's own image (the file, not argv[0]) and name no `--type` (a helper's). Windows
  matches the title whatever its case, but only as the folder was spelled at launch, so a profile's folder is
  always given in one spelling, long and resolved; a Chrome given another (an 8.3 name) exits 21, which `launch`
  names. Chrome makes no `SingletonLock` there, and its `lockfile` is never opened: an open at the moment a Chrome
  starts would make that Chrome fail. `Browser()` also asks the browser that answered for its own pid
  (`SystemInfo.getProcessInfo`), because `lsof` sees only this user's processes, and Windows lets a process bind a
  port on another address beside Chrome's.
- **`require` proves the browser, not the tab.** An Incognito, Guest or other Chrome profile's window is
  another browser context, and `Tabs` neither lists such a tab nor gives it an id, only counting
  it. Chrome's default context is its last-used Chrome profile, and `Local State` reaches disk seconds
  after a Chrome profile is added, so for those seconds a new Chrome profile's tab would pass. No Chrome
  profile is ever to be added to a profile's folder.
- **A Chrome with no window open has no Chrome profile loaded.** Chrome keeps running after its last window
  closes (macOS keeps every app running; on Windows the debugging port keeps it, measured), and Chrome then
  unloads its Chrome profile, so `Target.getBrowserContexts` names
  no default context. With no page open either, `Tabs` lists no tabs rather than refusing, and
  `tab_open`'s `Target.createTarget` loads it again. No default context beside open pages
  is still refused, since those tabs cannot be told apart.
- **Nothing but `tab_show` and the page's Open Chrome and Show leaves a profile's Chrome with the user's focus.**
  On Windows, `launch` starts Chrome detached with `--no-startup-window` and its first window shown without the focus,
  and a background tab, even in a new window, took no focus (measured); a page's `window.open` popup did, at once,
  and `focus.keep` gave it back before a 20ms poll saw Chrome in front, so `keep` runs there as on a Mac. `bring`
  tries `SetForegroundWindow`, then with its input joined to the app in front, then `SwitchToThisWindow`, and never
  presses a key (a stray Alt would reach the app in front). On a Mac: Chrome raises itself over
  the app in front each time it shows a window. macOS lets it at launch, even under `open -g`, and
  after that only once Chrome has been in front at least once. So `launch` starts it with `open -g`
  and `--no-startup-window`: with no window, the tabs it had open when it last quit do not come back, and the first
  `tab_open` makes one. Nothing stops Chrome raising itself for a tab a page opens (a `target=_blank`
  link, a `window.open` popup), so `focus.keep` hears its `Target.targetCreated` (a page with an
  `openerId`) 10 to 30ms after the click and, if that Chrome takes the Mac's focus within
  `focus.TAKE_WAIT` (Chrome was measured taking it 50 to 90ms after the click), gives the focus back
  to the app that had it. With that Chrome in front when the event comes, as after Joshua's own
  click, it leaves the focus there; it logs a line for each such tab.
- **Every profile's Chrome starts with `--allow-pre-commit-input` (`cdp.INPUT_FLAG`).** Once a page
  loads, Chrome holds its input until it first draws (paint holding), dropping every key press and
  click while `Input.dispatchKeyEvent` and `Input.dispatchMouseEvent`, and so chrome-devtools-mcp, report success. The hold ends
  only when Chrome draws the page, and it never draws a background tab by itself, so without the flag
  such a tab drops input indefinitely (measured: 40 of 40 first key presses on fresh background tabs dropped). `launch`
  `cdp.require` refuses a profile's Chrome whose command line lacks the flag, naming its pid to quit, so no tool,
  listing or window uses one, a Chrome kept through `../restart` from before the flag included.
- **Tabs open in the background.** `Tabs.open` creates the tab with `background: true`, so the
  Mac's focus never moves; without it, `Target.createTarget` brings Chrome to the front. Opening
  goes `about:blank`, attach, `Page.enable`, then navigate: Chrome can finish a load before a later
  `Page.enable` would hear it. A navigation the site has not answered by the websocket's 20s
  (`cdp.Late`) leaves the tab open and loading; a URL Chrome refuses is `could not open <url>: <why>`.
  For `tab_show`, `Target.activateTarget` picks the tab, and `focus.bring` brings the tab's Chrome
  to the front by pid, which `activateTarget` alone does not do for a Chrome never yet in front; when
  macOS refuses, `tab_show` fails.
- **`tab_open` never opens its tab in a window of its own.** A tab opened in a new window (as `Target.createTarget`
  opens one in a Chrome with no window open) holds a Google search page Chrome's omnibox prerenders there
  (`warmup.html`), and when Puppeteer attaches that hidden page before the tab's own, Puppeteer finishes connecting
  without the tab's page, so a new chrome-devtools-mcp lists no page for the tab and its queues cannot pair it
  (measured in two runs: 6 and 8 of 25 tabs opened in new windows, each failing for over 60s with a new process per
  queue). So `Tabs.open`, in a Chrome with no page of its Chrome profile open,
  first opens `tabs.PLACEHOLDER`, a `data:` page that no listing includes and so gets no tab id; the new window's
  prerender goes to the placeholder, and the tab goes into that window beside it (measured: 0 of 25 failed, each opened
  in a Chrome with no window open). A lock per profile, in `Tabs._placing`, covers looking for a page and opening the
  placeholder, so two opens at once make one placeholder. The placeholder stays open until its Chrome quits or it is
  closed by hand, and the next `tab_open` with no window open opens another.
- **No agent ends a session.** An agent would end one while its task still needed it, so only Joshua
  closes one: on the page (Close session) or with `../stop`. `Tabs.close_session` marks the
  session closed before it closes its tabs, so any call of the session's made meanwhile is refused. A session is paused after 30 minutes without a call, which stops its tabs' chrome-devtools-mcp processes;
  any call resumes it. A closed session's id is refused, pointing at `session_start`. Every agent, a subagent
  included, starts its own session; `SESSION_HELP` says so, and that only an agent carrying on the same task
  in the same tabs is given another's id. Sessions of one profile share its logins and cookies: one signing
  out of a site signs every one out.
- **A tab opened by hand is no session's** until the page hands it over. It gets a tab id at the next listing, but
  no session's `tab_list` shows it before then. A tab
  closed outside the server is marked closed at the next listing or use, and its id stays refused.
- **The MCP port, 9230, refuses any request with an `Origin` header, a `Host` other than
  `127.0.0.1:9230` or `localhost:9230`, or a body that is not `application/json`.** A web page
  open in any Chrome could otherwise POST to it and drive the browser.
- **The page has a port of its own, 9231,** so the MCP port keeps refusing every request with an `Origin`. Every
  page request must carry a `Host` of `127.0.0.1:9231` or `localhost:9231`, and no `Origin` but the page's own;
  a POST must carry that one. `GET /state` and every POST must also carry `X-Browserd-Token`, a random value
  written into the page when it is served and new each start. A web page open in any Chrome
  cannot read the token, since the page sends no CORS headers, and cannot frame the page to steer a click
  (`frame-ancestors 'none'`).
- **`../stop` and `../restart` only signal a pid whose command line runs `-m browser.server`.** A stale pid file
  can name a reused pid. `../start` holds `.run/start.lock` while it checks and spawns, and treats
  anything answering on 9230 under another server name as a refusal.
- **One chrome-devtools-mcp process runs one tool call at a time** (one `Mutex` in its
  `McpServer`, taken by every `ToolHandler.handle`), so a single shared process would put every
  tab in one line. Each tab gets its own, about 180MB each.
- **Its page ids mean nothing to DevTools.** `Worker._pair` sets a random value on the tab
  through the server's own connection (`window[Symbol.for('resume-tools-tab')]`), looks for it
  through chrome-devtools-mcp, URL matches first, then deletes it. The probe passes an empty
  `dialogAction` and no DOM wait, so it never answers a dialog raised in another tab. When no listed page holds the
  marker, the queue fails, saying to open the tab's url again with `tab_open` before closing it with `tab_close`.
- **A queue's step is `{"tool": name, ...arguments}`, never with `pageId`.** `steps.check` refuses
  the whole queue before anything runs for a tool in `steps.LEFT_OUT` (opening, listing, choosing
  and closing tabs; lighthouse; heap snapshots), an unknown tool (the refusal names every tool a
  queue runs), or arguments its tool's own schema would refuse (unknown, missing, of the wrong type
  or value), so a typo at step 40 does not run steps 1 to 39 first. One string where a tool asks for
  a list of strings (`wait_for`'s `text`) is made that list, and a uid written `uid=1_13`, or a run's
  `uid=5_1..25`, as a view line shows it, is taken as `1_13`, or the run's first uid, `5_1`. The
  queue stops at the first failed step; the report names the steps not run and ends with a view of
  the page now. A relative `file` is read from the tab's record folder.
- **Claude Code cuts a tool's description at about 2,000 characters.** `server.QUEUE_HELP` stays
  under it; `server.STEPS_HELP` and the step catalog, `steps.describe`, make up the `steps` argument's
  description, which Claude Code passes whole. `QUEUE_HELP` must still say that the catalog's tools run only as
  steps: without its "Never pass pageId", "every tool a step may name" and "never tools to call by themselves"
  lines, agents call a queue step's name, like `navigate_page`, as a top-level tool (measured in
  `../findings/queue-descriptions.md`).
- **Claude Code reads the tool list when a Claude Code session connects, and again only when told it changed.**
  So `initialize` declares `tools.listChanged` and gives an `Mcp-Session-Id`, and a request under an `Mcp-Session-Id`
  this process did not give (one from before a restart) is answered as an event stream:
  `notifications/tools/list_changed`, then the answer, once per id; any other request is plain JSON. Not
  the 404 the spec gives an unknown id: Claude Code answers that by initializing again, without listing
  the tools.
  Claude Code (2.1.181, seen with `claude -p`) then lists the tools again, and the model has the new
  list from the Claude Code session's next turn; a turn already running, a subagent's included, keeps the old one.
  A Claude Code session that connected to a browserd not yet declaring `listChanged` never hears it, and needs
  `/mcp` to reconnect; the queue's refusal of an argument it does not take says so.
- **Claude Code cuts an error result over about 10,000 characters out of its middle** (seen in a
  trace; a success of 17,593 came whole). So a failed queue's view of the page now is cut to keep the report
  under `steps.ERROR_MOST` (9,000), but never below `steps.PAGE_NOW_LEAST` (2,000), the whole of it
  saved as `<n>-page-now-reply.txt`.
- **Claude Code drops a tool's reply after about 60s** (measured: a 55s queue came back, a 65s one did
  not, though the server ran it to the end). So a queue starts no step once it has run
  `steps.QUEUE_MOST` (50s), naming the steps not run; `checked.run` cuts a `wait`'s timeout or a
  `pick`'s wait to the time left, and `steps.run` a chrome-devtools-mcp tool's `timeout`; and a
  `timeout` over `checked.WAIT_MOST` (45,000 ms), a `wait`'s or a chrome-devtools-mcp tool's, or a
  `pick` `wait` over 45s, is refused.
- **A dialog a `handle_dialog` step waits on is answered the moment it opens.** chrome-devtools-mcp
  blocks about 5s on a step whose dialog it was not told to answer. So when a `handle_dialog` step
  follows a step, `steps.run` first starts a `dialogs.Answerer` on a connection of its own to the tab
  (`Page.enable`, then `Page.javascriptDialogOpening`), which answers the dialog as that step asks,
  and the `handle_dialog` step waits up to `dialogs.LATE` (5s) after the step before for one that
  opens late. puppeteer marks a dialog closed by another connection handled, so chrome-devtools-mcp
  goes on. An answerer that heard no dialog, or could not reach the tab, leaves the `handle_dialog`
  step to chrome-devtools-mcp. None starts for a step in `steps.OWN_DIALOGS` (`evaluate_script`'s
  `dialogAction`, `navigate_page`'s `handleBeforeUnload`, and `handle_dialog`), which answer their own.
  When a queue stops before its `handle_dialog` step runs, the report still says what the answerer
  answered.
- **A step that begins a download says where it went.** Chrome saves a download where that Chrome's own
  settings say (`~/Downloads` unless changed), and chrome-devtools-mcp's reply never names it; an agent told nothing
  failed WebGames' combination-lock task in a benchmark, hunting for the file through `file://` listings. So a tab's
  `Worker` runs a `downloads.Watcher` from `ensure` until `stop` or `pause`, on a connection of its own to the tab:
  `Page.enable` for `Page.downloadWillBegin`, which only the tab's own downloads send; `Target.setDiscoverTargets`,
  to follow the popups it opens, whose downloads send only `Browser.downloadWillBegin`, naming the popup; and
  `Browser.setDownloadBehavior` with `behavior: default` and `eventsEnabled`, which keeps Chrome's own behaviour and
  only asks for `Browser.downloadProgress`. After each step, `steps.run` waits up to `steps.DOWNLOAD_WAIT` (5s, or
  the queue's time left) for a download the step began to end, and its report says `downloaded <name> to <path>`,
  that it was canceled or failed, or that it is still downloading; the first step on the tab after it ends then
  says where it went, in this queue or a later one, unless the tab's `Watcher` was stopped or started again
  meanwhile. A download heard only after a step's reply came back lands in the next step's report, the next
  queue's first when that step was the last.
- **A step that opens a dialog nothing waits on counts as done.** chrome-devtools-mcp fails it after
  about 5s, with an `# Open dialog` section in its reply; `steps.run` counts it as done, so a
  `handle_dialog` step in the next queue answers it. A reply that also holds `A dialog is open (`,
  chrome-devtools-mcp's refusal of a step begun while a dialog was open, still fails, as does a
  checked step, whose read-back never ran, and a tool in `steps.UNBLOCKED`, which runs with a dialog
  open.
- **A queue's `take_screenshot` of the viewport is browserd's own, one image pixel per CSS pixel, and comes back as an
  image.** chrome-devtools-mcp's is in device pixels, twice CSS pixels on a Retina Mac, while the page's own
  coordinates are CSS pixels, so every point read off its image would be twice its place. `screenshot.viewport` asks
  `Page.getLayoutMetrics` for the viewport, clips `Page.captureScreenshot` to it at its scroll offset (a clip sits in
  the document) at a scale of one over the device pixel ratio, saves it in the tab's record folder as
  `<n>-step<k>-screenshot.jpeg` (`.png` or `.webp` for those formats), and returns a line giving its size and path,
  then the image, so no Read is needed to see it. It is a JPEG at quality 80 unless the step asks otherwise: a quarter
  of a PNG's bytes (measured on Google Maps: 156KB against 616KB), and every later request of a conversation carries
  the image again. A viewport over `screenshot.LONGEST` (2,000) on a side, past which Claude Code shrinks an image it
  reads (seen: 2,400 to 2,000), is shrunk to it, and a step's own `scale` (above 0, up to 1; refused with `uid` or
  `fullPage`) shrinks it by that factor more, for fewer image tokens; the line gives the factor to multiply a point
  by. A `take_screenshot` of an element (`uid`) or the whole page (`fullPage`) is still chrome-devtools-mcp's, in
  device pixels, saved as `<n>-step<k>-screenshot.png` (or its format's) and reported by path, not as an image:
  chrome-devtools-mcp attaches an image only when no path is given. A page gets `screenshot.ANSWER_WAIT` (5s) to
  answer `Page.getLayoutMetrics`, which it never does while a dialog is open, before the step fails saying to answer
  the dialog first. On Windows, Chrome draws a background tab about once a second, and a capture of one waits for a
  frame that, measured, sometimes never came (one in two clipped captures hung for 60s, and every capture after the
  first to hang); a second capture brings it. So a capture is asked again every `screenshot.NUDGE` (0.5s) until one
  answers (`cdp.Browser.call`'s `nudge`; answers to the others are dropped): 48 of 48 then answered, the longest in
  2s, the median 0.2s. A step that waits on a frame (a screenshot, the guard's capture, chrome-devtools-mcp's wait
  after a click) may so take up to about 2s more on Windows than on a Mac. Every profile's Chrome on Windows also
  starts with overlay scrollbars (`system.CHROME_FLAGS`), as a Mac's are: Windows' own take 15px of the viewport, and
  a background tab's viewport flipped between the two widths as it was laid out (1234 to 1219 CSS px), which the guard
  reads as the viewport changing size.
- **The pointer steps are a hand's three moves: `move_at x,y`, `click_down` and `click_up`.** A click is the three
  in turn, a drag puts a second `move_at` between the press and the let-go, and a hover is a `move_at` alone, so no
  step repeats another's work. `pointer.run` sends each as one `Input.dispatchMouseEvent` on a connection of its own
  to the tab, at a viewport screenshot's CSS pixels, and keeps where the pointer is and which buttons it holds for
  each tab (`pointer._pointers`, by target id), since `click_down` and `click_up` act where the pointer is; browserd
  forgets it when it restarts, so a `click_down` or `click_up` on a tab no `move_at` has placed the pointer on since
  browserd started is refused. A `move_at` with a button down carries it, as a drag. A pointer step whose input
  opens an alert, confirm or prompt (most often a `click_up`, since a click fires on the let-go) is not answered
  until the dialog is (measured: `Input.dispatchMouseEvent` waited out the 20s `cdp.CALL_WAIT`), so each pointer
  step waits `pointer.DIALOG_WAIT` (5s) for its input to be answered, then hears the dialog on the `Page.enable` it
  asked for and counts as done, saying so, as a chrome-devtools-mcp step does; a `handle_dialog` step right after
  answers it as it opens. A dialog open already holds the `Page.enable` too, so the step fails after those 5s, sending
  nothing and saying to answer it first; input a busy page has not taken after them lands once it is free, so the
  step fails saying so, and the pointer is kept where the input put it. chrome-devtools-mcp's own `click_at` (behind `--experimental-vision`, which browserd does
  not pass) cannot hover or drag: WebGames' herding needs the pointer moved over a canvas, and an agent given
  `click_at` spent 40 of them standing in for moves.
- **The click guard stops a press, or keys, that the page changed under since the agent's last screenshot.** A point
  read off a screenshot can meet a popup that opened while the agent thought, or one its own last click opened. Each
  queue gets a `guard.Guard` over its tab's Worker; its reference is the last viewport screenshot a reply gave before
  the queue arrived, each taken through `Guard.capture`, which first bumps a counter that `guard.PAGE_JS`, a
  MutationObserver kept in the page, stamps changes with. Measured in `../findings/real-sites.md` (19 sites, 126 agent
  runs), its setting is:
  - **pixels (S1):** over 10% of the 24 CSS px square around the point, or 75% of its central 8 px, changed by more
    than 64 in a channel, against a fresh capture of the same format and scale;
  - **the page (S2):** an element added, shown or hidden around the point, or text changed within 3 levels of it;
  - **layout (S6):** a layout shift from or onto the point;
  - **the page itself:** a new document, a scroll or a resize, or a check that could not tell, which stops too.

  The check runs before a `move_at` that a `click_down` follows, since the hover the move causes is the agent's own,
  or before a `click_down` whose pointer an earlier queue placed; a `click_down` of count 2 or 3 goes on its first.
  S2, S6 and the document are read again just before the input goes out (a few ms, against the check's ~100). A press
  with no reference is stopped too. On a tab a screenshot came back from, `press_key` and `type_text` are stopped when
  the focus is not where the last reply, key or step left it, or not in what the queue's last press hit, or when
  something entered the top layer since the screenshot; a tab driven by snapshots alone is never read. A stopped step
  fails, saying why, with a screenshot of the page now, saved as `<n>-step<k>-stopped.<format>` and the next
  reference. It forgets its references when browserd restarts, so the first press after a restart is stopped.
- **Every snapshot a queue reports is a view**: `take_snapshot`'s, `wait_for`'s, an `includeSnapshot`
  step's and the failed queue's. A reply's snapshot runs from chrome-devtools-mcp's
  `## Latest page snapshot` line to the next of the headers it can put after one
  (`steps.AFTER_SNAPSHOT`), so a value's own `## ` line never ends it. The view keeps each line with
  words (a name that is not only spaces, or a `description`, `url`, `value` or `valuetext`) and each
  role in `steps.CONTROLS`, indented one level per kept line it sits under, and leaves out a verbose
  snapshot's `InlineTextBox` lines, which copy the text above them under shared uids. A native select, a
  combobox with options and no other control under it, becomes
  `combobox "<name>" = "<value>" <attributes> (<n> options)`; a custom multi-select, with a search box
  or remove buttons among its options, stays whole. A date, datetime-local, month, week or time input
  (`steps.DATE_ROLES`: `Date`, `DateTime`, `InputTime`) is its own line alone, its value on it once it has one, unless
  the view is under its uid: its parts (spinbuttons, like a date's Month, Day and Year) and picker button are left out,
  since `fill` cannot take a part (measured: 20 of 50 FormFactory benchmark runs filled a Month spinbutton, and every
  such fill failed). A run of `steps.WORD_RUN_LEAST` (3) or more
  `StaticText` siblings, each one word and nothing else, whose uids count up by one, as a canvas app
  like Slides draws its words, becomes one line, `uid=5_1..25 StaticText "<the words, joined by
  spaces>"`, and word k keeps its uid, `5_(1+k)`. A gap in the numbering, a line of more than one
  word, or any attribute ends a run; two one-word neighbours are more often two labels. The whole snapshot is saved in the tab's record
  folder as `<n>-step<k>-snapshot.txt`, or `<n>-page-now-snapshot.txt` for the failed queue's, and the
  view's header names it. take_snapshot's own `under: uid` keeps only that element and what sits under
  it, a native select there not collapsed, and fails its step when the snapshot lacks the uid;
  `full: true` gives the lines as chrome-devtools-mcp wrote them instead of a view; `find: <regex>`
  keeps only the lines it matches, ignoring case, and its header counts them; between two of them, a line such as
  `(2 lines left out by find)` says it left lines out there, since two lines shown one after the other read as
  neighbours: on an MCP-Universe benchmark task a `find: "ROLE"` showed `<ROLE>`, the block's first line and
  `</ROLE>`, its second line left out, and the agent answered with the first alone; `after: uid` keeps the lines after
  that element in the saved snapshot's order, one the view leaves out or folds into a run of words included, and
  fails its step when the snapshot has no such element. It goes by place, not by number: chrome-devtools-mcp keeps
  an element's uid from the snapshot that first saw it, so a snapshot taken after the page changed mixes `1_x` and
  `2_x` uids. A view, or `full`'s lines, over `steps.VIEW_MOST` (10,000 characters, the first line, a page's
  RootWebArea with its url, not counted) is cut at a line, and a note after it gives how many lines are left, the
  take_snapshot call that reads on (`after` the last uid shown, with the step's own `under`, `find` or `full`), and
  the first `steps.HEADINGS_MOST` (40) headings below the cut, whose uids read from there as `after` (`under` a
  heading gives the heading alone, its section sitting beside it). A view barely over is left whole, when the note
  would be as long as what it cuts. 145 of 537 views were over 10,000 characters in two MCP-Universe benchmark runs,
  and every later request of a conversation carries each one again. On a 40- or 80-section page with one fact
  hidden in its middle, agents found it in 9 of 9 task pairs both with the cut and with views cut only at a reply's
  `steps.REPLY_MOST` (40,000), in a mean 11.4s against 22.0s (median 12s against 17s) and with 38% fewer input
  tokens (750k against 1,203k). A failed queue's view of the page now is cut to fit `steps.ERROR_MOST`, which is
  under `steps.VIEW_MOST`, so it loses the note. take_snapshot's
  `filePath` is refused, since it would skip the view and the record folder. A name ends at the first
  quote followed by one of the attribute names a snapshot line can carry (`steps.ATTRIBUTES`), and a
  native select's value, its last attribute, runs to the line's end, so quotes inside either are kept.
- **A chrome-devtools-mcp tool reports success once it has acted, not once the page took it,** so
  `checked.py` adds checked steps that read the page back (all but a `paste` without a uid); `server.STEPS_HELP` tells agents
  when to use each.
  - **`pick`** only clicks an option that typing listed, not one already on the page (a
    `<select multiple>` with the same words). It passes once the field holds `text`; a text box
    holding only what was typed also needs the clicked option gone, since an unchosen autocomplete
    reads as typed. A field that shows a short form passes if what it shows changed. On any failure
    it clears what it typed, so the text cannot read back as an answer. It refuses a native select
    before typing, since typed letters jump a select's choice to whatever option they start.
  - **`expect`** matches exactly. A combobox with an empty text box reads as the text shown around
    it, and passes when one element there holds exactly the value, so "Hispanic or Latino" does not
    pass on "White (Not Hispanic or Latino)".
  - **`type`** exists because `type_text` takes no uid, typing into whatever has focus, and `fill`
    sets a value of 100 characters or more by script, which React ignores. It focuses the text box at
    or inside the uid (or the uid's own element, when it is contenteditable) and selects its text by
    script, types with `type_text`, then reads back as `expect` does, a contenteditable element's text
    with each run of whitespace made one space. It types nothing into a box that is disabled,
    read-only or did not take focus, an input that is not text-like (a checkbox, a submit, a date), a
    combobox input (that takes `pick`), or a one-line box given a line break, which `type_text` would
    send as Enter and so submit the form. It passes on exactly the text, or on the text with only its
    spacing and punctuation changed (a masked phone), saying what the field shows; a field that cut the
    text short (a `maxlength`) fails, saying so.
  - **`paste`** exists because editors change text as it is typed (Slides curls quotes and capitalizes a
    new line; a code editor closes brackets and indents lines), and a real paste is taken as it is. It
    presses the command key and V (Meta+V on a Mac, Control+V on Windows, `checked.PASTE_KEY`) over the server's own
    connection to the tab with Chrome's `paste` command, since on a Mac a key press alone, as `press_key` sends it,
    pastes nothing; the command carries the paste on Windows too, and the listeners count presses of that OS's key.
    A `press_key` shortcut written with Meta is held with Control on Windows (`steps._command_key`), where Meta is
    the Windows key. Chrome reads the clipboard into
    that paste, and nothing writes the clipboard: first `checked.HAND_JS` puts listeners in every
    same-origin frame, and they hand the paste event the text as its `clipboardData`, insert the text
    themselves when no page script cancelled or stopped the paste, cancel and stop Chrome's own insert of
    the real clipboard, which follows a paste an editor stopped without cancelling (Slides does), and
    block any paste after the first; the step takes the text back after, removing them all, or fails
    saying to reload the tab. A page script that reads the paste, or Chrome's insert, before them can
    still read the real clipboard. The focus in a frame from another site, where they cannot go, is
    refused, and so is the focus moving where they are not before the key is pressed. The key is
    pressed once, since `cdp.INPUT_FLAG` (above) keeps Chrome from dropping
    it: a press the page did not take fails the step, and one it took whose paste a page script had first
    fails it saying the field may hold the Mac's clipboard. Three other ways were measured to fail: `Input.insertText` gets closed brackets and
    curled quotes as typing does, a synthetic `paste` event puts nothing in a box with no paste handler,
    and dragging and dropping the text puts nothing in CodeMirror or Quill. With a uid, it focuses and
    selects as `type` does, refusing what `type` refuses but a line break in a one-line box (a paste
    presses no Enter), and reads back as `type` reads; without, it pastes where the focus is, followed
    into same-origin frames (where Google Docs and Slides take typing) and shadow roots, refusing unless
    that is a text box or contenteditable element, and nothing reads it back.
  - **`wait`** takes one condition: `gone` (the text, once seen, is in no snapshot line's page text,
    the title included; uids, roles and urls are left out), `uid` and `value` (the field holds it, read
    as `expect` reads), or `still` (ms without a change). `timeout` is ms, like `wait_for`'s; `pick`'s
    `wait` stays seconds, and a `timeout` or `still` under 100 is refused, as seconds written where ms
    belong. `gone` fails when its text did not show in the wait's first `checked.APPEAR_WAIT` (3s, or
    its timeout if shorter), since text split across
    elements (a word in bold) never matches and would pass at once; the 3s let a page start its work
    a moment after the step before. `gone`
    and `still` read snapshots, taken one after another, not a script, so they see every frame and
    every field's value, which a MutationObserver misses when a script sets `.value`; a page whose
    text changes more often than every `still` ms (a clock, a carousel) never counts as still, and a
    change a snapshot does not carry (a CSS spinner) is no change.
  - **Each is checked for its keys and types before any step of the queue runs.**
  - **`fill` and `fill_form` read each element first** (`checked.fill_refused`), and fail without
    filling any element that is disabled, read-only, a checkbox, radio or switch given anything but
    "true" or "false", or a dropdown select given text none of its options' labels is exactly (a
    `<select multiple>` takes an option's value, so is not checked). chrome-devtools-mcp's own `fill`
    empties a read-only box and reports success, fails a disabled box after 5s, and fails the other two
    at once, each with a message that names none of them. `fill` and `fill_form` also fail a part of a date or time
    input (an element in the input's own shadow root, which chrome-devtools-mcp's `fill` fails after 5s), naming the
    role of the input's line (`Date`, `DateTime` or `InputTime`) to fill instead, and a date or time input given a
    value Chrome would not take, which chrome-devtools-mcp's `fill` leaves empty and reports as a success.
    `checked.FILL_JS` sets the value on a copy of the input, Chrome's own parser, so a form but the type's own
    (`checked.DATE_VALUES`: 1957-08-01 for a date) and a day that does not exist (1957-02-29) both fail; an empty
    value, which clears the input, passes.
  - **A run of `fill` steps in a row is read in one call** (`checked.read_fills`) as its first step runs. The first
    step, and each after it that `checked.FILL_JS` reads as a `box` (a text-like input, a textarea or a
    contenteditable, with no combobox or listbox role) up to the first that is not one, is judged by that read instead
    of its own (`steps._Fills`); a select, toggle, date or other input is what most often locks or unlocks the fields
    after it, so after one each fill reads its own element. A read that refuses is taken again at the step's own turn,
    after the fills before it, so a box one of them enabled is filled. No `GAP` is slept between two fills, since
    chrome-devtools-mcp's `fill` waits for the page to settle. On a FormFactory form a `fill` step took about 0.4s
    (its read 0.1s, chrome-devtools-mcp's `fill` 0.2s), and `GAP` 0.1s followed it; four text boxes in a row took
    0.9s, against 1.8s with a read and a `GAP` per fill, and 50 FormFactory agent runs a mean 20.9s a form against
    21.8s (31 of 50 forms faster), their fields as right (94%). Not covered: a box the page makes read-only or
    disabled after its run's read, by a handler an earlier fill of the run set off (a box's keys and `input` as it is
    typed; the `input` and `change` fired at once for a value of 100 characters or more, which `fill` sets by script;
    a typed box's `change`, fired as the next fill takes the focus) or by work of its own (a reply or timer landing),
    is filled on the read from before: chrome-devtools-mcp's `fill` empties a read-only one and reports success, and
    fails a disabled one after 5s.
- **Element uids come from `take_snapshot` and live in that tab's process.** They stay valid
  across queue calls until the page navigates or the element goes away. When the process died and
  was restarted, the next report opens with a note that they are gone. A step failing with
  chrome-devtools-mcp's "No page found" (it renumbered its pages after reconnecting) stops the
  process, so the next queue re-pairs.
- **chrome-devtools-mcp's file tools may only touch `devtools.FILE_ROOTS`: the Desktop (`~/Desktop`, or on
  Windows the Desktop known folder, wherever OneDrive moved it), this project's folder (which holds the record
  folders) and, on a Mac, `/private/tmp` (all `--workspace`), and the temporary folder, which it always adds.**
  `steps.check` refuses a `filePath` or `filePaths` outside them (`devtools.may_touch`, which compares them
  case-folded and refuses a network path before resolving it), or relative, before any step runs: the server's
  working folder is this project's, so a relative or `~` path would land inside it. A `file` of steps on a network
  path is never read either; one is read as UTF-8, a byte order mark (Windows PowerShell's) skipped. Usage statistics and CrUX are off, and the performance, network and
  emulation tools are not loaded.
- **No tool types a password safely.** A queue's chrome-devtools-mcp steps refuse nothing, and every
  step's arguments are recorded in the tab's record folder, and a refused call's in `../.run/server.log` too.
- **Checks.**
  - **Without Chrome:** `framing` and `protocol` need nothing. `connecting` stands in for the OS
    (`system.listeners`, `command`, `switches`, `chrome_owner`) and the port, then asks the real OS about ports it
    holds itself; `owning_mac` checks the lock, `ps`, `lsof` and `open`, and `owning_windows` a real
    `Chrome_MessageWindow` in a process of its own, a pid it cannot read, and how Chrome is started.
    `tabs_offline` and `session_tools_offline` stand in for Chrome and `osascript`, with `state.db` in a
    temporary folder; `focus_offline` for `lsappinfo`, `osascript` and Chrome's events; `queue_offline` for chrome-devtools-mcp and a snapshot;
    `pairing_offline` for a tab's chrome-devtools-mcp and the connection that marks the tab;
    `dialogs_offline` for the answerer's connection; `downloads_offline` for the watcher's connection; `paste_offline` for chrome-devtools-mcp and
    the connection that hands the page its text and presses the paste key; `screenshot_offline` for the connection
    a viewport screenshot is taken over; `pointer_offline` for the connection mouse input is sent over; `limits_offline` for a slow tool;
    `recording_offline` for Chrome, recording into a temporary folder; `profiles_offline` for the Google folder
    and a folder's running Chrome, and `page_offline` for Chrome, `focus.bring` and each tab's Worker, with `.run/state.db` and the Google
    folder each in a temporary folder;
    `service_offline` for the spawned server, with real `ps`, and for the server `../stop` and `../restart`
    signal, a process that runs `-m browser.server` in its command line.
  - **Live:** the live groups start a Chrome of their own, `throwaway.chrome()`, on a new folder under
    `$TMPDIR` and a free port, and quit it after, so no live check touches a profile's Chrome; with no
    Chrome installed they skip. `queue_live` also needs `npm ci` done, and skips
    without it, and records into a temporary folder; its checked steps run on a local page whose
    dropdowns and one textarea take only trusted input, whose Parse resume button runs a stand-in resume parser,
    whose Quoted editor curls quotes as they are typed, whose Stopper editor puts a paste in itself and stops
    it without cancelling Chrome's own insert, as Slides does, and whose Warn me confirm, clicked with no
    handle_dialog step after it, makes one click take about 5s. Its paste checks read the Mac's clipboard's
    change count, never its contents, and check nothing wrote it. Its pixel checks find a red square drawn on a
    canvas down a scrolled page in a viewport screenshot's own pixels, and click it there with `move_at`,
    `click_down` and `click_up`; its pointer checks drag across a pad that records trusted mouse events, click a
    button whose alert holds the let-go, and then try a `move_at` and a screenshot with that alert still open. Its
    guard checks press with no screenshot, on an unchanged spot, on a button that turns blue on hover, under a modal
    raised after the screenshot, and twice for a double click, and type with the focus kept and moved.
  - **Tabs:** live checks open scratch tabs and a throwaway browser context, work only inside
    them, and close them (`queue_live`'s download checks also save two files in the real `~/Downloads`, the
    throwaway Chrome keeping Chrome's own download folder, and remove them); a tab already open is never touched. No live check moves the Mac's focus:
    `tab_show` and `focus.keep` are checked offline only, and `focus.front` and `focus.bring` never run in a check.
  - **Never automated:** `../start`, `../stop` and `../restart` are never run, since each acts on the real
    browserd: stopping quits every profile's Chrome, and restarting replaces the one running.
