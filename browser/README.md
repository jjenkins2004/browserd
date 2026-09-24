# browser

## Module TL;DR

The browser MCP server: it starts and owns Joshua's School Chrome and serves tools to Claude
sessions over HTTP on `127.0.0.1:9230`, so application forms are read and filled inside his
logged-in session. `tab_open`, `tab_list`, `tab_show` and `tab_close` manage tabs by short tab ids;
`queue` runs a list of steps on one tab through that tab's own chrome-devtools-mcp process, and
records every call in that tab's record folder, `../.run/calls/<tab>/`. Python standard library, plus Node for
chrome-devtools-mcp (pinned in `../package.json`; run `npm ci`).

    ../start    start the server and the School Chrome, in the background
    ../stop     stop the server, which quits the School Chrome

## Directory Layout

    browser/
      ws.py        RFC 6455 cut down to one local, trusted, text-only connection
      cdp.py       which Chrome, port proof, one websocket to it
      launch.py    starts the School Chrome, or adopts one already up
      tabs.py      short tab ids; open, list, show, close
      mcp.py       MCP over HTTP: JSON-RPC per POST, tool dispatch
      server.py    the server process: tools, Chrome lifecycle, pid file
      service.py   ../start and ../stop: background start, locked; stop by pid
      devtools.py  MCP client for one chrome-devtools-mcp process over stdio
      worker.py    one tab's process, paired with its page; Workers registry
      steps.py     the queue: load, check, run, report; snapshot views
      checked.py   the queue's checked steps: pick, expect, type, wait
      record.py    one queue call's numbered files in a folder
    ../start, ../stop           launchers
    ../.run/                    gitignored: server.pid, server.log, start.lock, devtools-*.log, calls/<tab>/
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
the four tab tools, `server.queue_tool` the queue; all turn `cdp.CdpError` into `ToolError`. The queue
records into `server.CALLS/<tab>/`, so it refuses a tab argument not shaped like a tab id (`tabs.is_id`)
before that reaches a path, and once its arguments pass, makes a `record.Call`, writes what was asked,
and writes what came back through `_recorded`, a raised error included.

**`worker.Worker`** is one tab's `devtools.Devtools` process and its page id there, behind a lock,
so one tab's queues run in turn and different tabs run at once. `Worker.ensure()` starts and pairs
the process on the tab's first queue, and again after it dies. **`worker.Workers`** holds
one per tab id. `tab_close`, `tab_list` (for tabs no longer open) and a queue on a tab a
fresh listing no longer shows drop it; any other failure to reach a tab leaves its process and uids
alone.

**`steps.place_screenshots`** first gives each `take_screenshot` without a `filePath` one among the
call's files. **`steps.run`** sends each chrome-devtools-mcp step with the page id added (and
without take_snapshot's own `under` and `full`), hands each checked step to `checked.run`, passes
every reply's text, less chrome-devtools-mcp's `## Pages` list of every tab in Chrome, through
**`steps.view`**, and returns a whole MCP result: a text report, any images, and `isError` when a
step failed. A reply longer than `steps.REPLY_MOST` (40,000 characters) is cut there, the whole of it
saved as `<n>-step<k>-reply.txt` (`<n>-page-now-reply.txt` for the failed queue's).

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
  `Page.enable` would hear it. A navigation the site has not answered by the websocket's 20s
  (`cdp.Late`) leaves the tab open and loading; a URL Chrome refuses is `could not open <url>: <why>`.
  `tab_show` is the one tool meant to move focus: `Target.activateTarget` also raises the School
  Chrome over the app in front; no separate macOS call is needed.
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
- **A queue's step is `{"tool": name, ...arguments}`, never with `pageId`.** `steps.check` refuses
  the whole queue before anything runs for a tool in `steps.LEFT_OUT` (opening, listing, choosing
  and closing tabs; lighthouse; heap snapshots), an unknown tool, or arguments its tool's own schema
  would refuse (unknown, missing, of the wrong type or value), so a typo at step 40 does not run
  steps 1 to 39 first. One string where a tool asks for a list of strings (`wait_for`'s `text`) is
  made that list, and a uid written `uid=1_13`, as a view line shows it, is taken as `1_13`. The
  queue stops at the first failed step; the report names the steps not run and ends with a view of
  the page now. A relative `file` is read from the tab's record folder.
- **Claude Code cuts a tool's description at about 2,000 characters.** `server.QUEUE_HELP` stays
  under it; `server.STEPS_HELP` and the step catalog, `steps.describe`, make up the `steps` argument's
  description, which Claude Code passes whole.
- **A step that opens an alert, confirm or prompt counts as done.** chrome-devtools-mcp fails such a
  step after about 30s, with an `# Open dialog` section in its reply; `steps.run` counts it as done,
  so a `handle_dialog` step after it runs. A reply that also holds `A dialog is open (`,
  chrome-devtools-mcp's refusal of a step begun while a dialog was open, still fails, as does a
  checked step, whose read-back never ran, and a tool in `steps.UNBLOCKED`, which runs with a dialog
  open.
- **A queue's `take_screenshot` without `filePath` is saved in the tab's record folder as `<n>-step<k>-screenshot.png`**
  (`.jpeg` or `.webp` for those formats), and the report gives that path, not an image:
  chrome-devtools-mcp attaches an image only when no path is given, and even then saves one of 2MB
  or more to a temporary file instead.
- **Every snapshot a queue reports is a view**: `take_snapshot`'s, `wait_for`'s, an `includeSnapshot`
  step's and the failed queue's. A reply's snapshot runs from chrome-devtools-mcp's
  `## Latest page snapshot` line to the next of the headers it can put after one
  (`steps.AFTER_SNAPSHOT`), so a value's own `## ` line never ends it. The view keeps each line with
  words (a name that is not only spaces, or a `description`, `url`, `value` or `valuetext`) and each
  role in `steps.CONTROLS`, indented one level per kept line it sits under, and leaves out a verbose
  snapshot's `InlineTextBox` lines, which copy the text above them under shared uids. A native select, a
  combobox with options and no other control under it, becomes
  `combobox "<name>" = "<value>" <attributes> (<n> options)`; a custom multi-select, with a search box
  or remove buttons among its options, stays whole. The whole snapshot is saved in the tab's record
  folder as `<n>-step<k>-snapshot.txt`, or `<n>-page-now-snapshot.txt` for the failed queue's, and the
  view's header names it. take_snapshot's own `under: uid` keeps only that element and what sits under
  it, a native select there not collapsed, and fails its step when the snapshot lacks the uid;
  `full: true` gives the lines as chrome-devtools-mcp wrote them instead of a view. take_snapshot's
  `filePath` is refused, since it would skip the view and the record folder. A name ends at the first
  quote followed by one of the attribute names a snapshot line can carry (`steps.ATTRIBUTES`), and a
  native select's value, its last attribute, runs to the line's end, so quotes inside either are kept.
- **A chrome-devtools-mcp tool reports success once it has acted, not once the page took it,** so
  `checked.py` adds checked steps that read the page back; `server.STEPS_HELP` tells agents
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
    send as Enter and so submit the form. When the read-back fails, it says whether the field cut the
    text short (a `maxlength`) or reformatted it (a masked phone), and still fails: it passes only on
    exactly the text.
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
- **Element uids come from `take_snapshot` and live in that tab's process.** They stay valid
  across queue calls until the page navigates or the element goes away. When the process died and
  was restarted, the next report opens with a note that they are gone. A step failing with
  chrome-devtools-mcp's "No page found" (it renumbered its pages after reconnecting) stops the
  process, so the next queue re-pairs.
- **chrome-devtools-mcp's file tools may only touch `devtools.FILE_ROOTS`: `~/Desktop`, this project's
  folder (which holds the record folders) and `/private/tmp` (all `--workspace`), and `$TMPDIR`, which
  it always adds.** `steps.check` refuses a `filePath` or `filePaths` outside them
  (`devtools.may_touch`), or relative, before any step runs: the server's working folder is this
  project's, so a relative or `~` path would land inside it. Usage statistics and CrUX are off, and the performance, network and
  emulation tools are not loaded.
- **No tool types a password safely.** A queue's chrome-devtools-mcp steps refuse nothing, and every
  step's arguments are recorded in the tab's record folder.
- **Checks.**
  - **Without Chrome:** `framing` and `protocol` need nothing. `connecting` stands in for `lsof`,
    `ps`, the lock and the port, then runs the real `lsof` and `ps` against ports it holds itself.
    `tabs_offline` stands in for Chrome; `queue_offline` for chrome-devtools-mcp and a snapshot;
    `recording_offline` for Chrome, recording into a temporary folder;
    `service_offline` for the spawned server, with real `ps`.
  - **Live:** the live groups need the School Chrome up. Nothing listening on 9223 skips them;
    anything else wrong with the port is a failure. `queue_live` also needs `npm ci` done, and skips
    without it, and records into a temporary folder; its checked steps run on a local page whose
    dropdowns and one textarea take only trusted input, whose Parse resume button runs a stand-in resume parser,
    and whose confirm makes one click take about 30s.
  - **Tabs:** live checks open scratch tabs and a throwaway browser context, work only inside
    them, and close them; a tab already open is never touched. The live `tab_show` check brings the
    School Chrome to the front.
  - **Never automated:** `../start` and `../stop` are never run against the real School Chrome,
    because stopping quits it.
