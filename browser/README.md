# browser

## Module TL;DR

The browser MCP server: it starts and owns one Chrome per profile and serves tools to agents over HTTP on `ports.MCP`
(9230 unless `browserd setup` chose another), so pages are read and driven with that profile's logins. An agent calls
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
      steps/         the queue tool's steps
        steps.py     the queue: load, check, run, report; snapshot views
        checked.py   the queue's checked steps: pick, expect, type, paste, wait; fill_refused, read_fills
        pointer.py   the queue's pointer steps: move_at, click_down, click_up
        hit.py       what a press lands on, off the accessibility tree or the page's DOM, and whether it carries the press's on
        screenshot.py  a queue's take_screenshot of the viewport: CSS pixels, saved and sent back as an image; keeps a queue's tab drawn
        dialogs.py   answers a dialog the moment it opens, for a handle_dialog step
      dashboard/
        page.py      the browserd page's server: GET /, GET /state, a POST per action
        ui/          the browserd page itself; its own README
      tools.py       the tools agents call, their descriptions, and the queue's body
      server.py      the server process (`python -m browser.server`): serves the tools and the browserd page
      cli/
        service.py   the browserd command: start, stop, restart, status, version, setup, uninstall
        installs.py  what each installer put where, for uninstall

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
- **The records folder**, `paths.RUN` (`config/paths.py` says where), holds server.pid, server.log, start.lock,
  state.db, ports.json, devtools-*.log, calls/ and downloads/<profile>/. Record folders and devtools logs are never
  removed.
- **The ports** change only through `browserd setup` (`service.setup`). browserd never edits an agent's settings, so
  after the MCP port changes an agent needs `service.CONNECT`'s line again.
- **The browserd page.** `page.Page` serves it on a thread of the server's own; `Page.snapshot`'s docstring says what
  `GET /state` answers, `Page.act` what each POST does, and `dashboard/ui/README.md` the browserd page itself.
- **`browserd uninstall`**: `service.uninstall` and `installs.py` say what it removes and keeps.
- **`steps.place_screenshots`** first gives each `take_screenshot` without a `filePath` one among the
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
- **A queue's step is `{"tool": name, ...arguments}`, never with `pageId`.** `steps.check` refuses
  the whole queue before anything runs for a tool in `steps.LEFT_OUT` (opening, listing, choosing
  and closing tabs; lighthouse; heap snapshots), an unknown tool (the refusal names every tool a
  queue runs), or arguments its tool's own schema would refuse (unknown, missing, of the wrong type
  or value), so a typo at step 40 does not run steps 1 to 39 first. One string where a tool asks for
  a list of strings (`wait_for`'s `text`) is made that list, and a uid written `uid=1_13`, or a run's
  `uid=5_1..25`, as a view line shows it, is taken as `1_13`, or the run's first uid, `5_1`. The
  queue stops at the first failed step; the report names the steps not run and ends with a view of
  the page now. A relative `file` is read from the tab's record folder.
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
- **A step that begins a download says where it went.** chrome-devtools-mcp's reply never names it; an agent told
  nothing failed WebGames' combination-lock task in a benchmark, hunting for the file through `file://` listings.
  So a tab's `downloads.Watcher` (`chrome/README.md`) hears each download the tab begins. After each step, `steps.run`
  waits up to `steps.DOWNLOAD_WAIT` (5s, or the queue's time left) for a download the step began to end, and its
  report says `downloaded <name> to <path>`, that it was canceled or failed, or that it is still downloading
  (`Watcher.take` says which later step reports where it went).
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
  first to hang); a second capture brings it. Asked again every `screenshot.NUDGE` (0.5s) until one answered
  (`cdp.Browser.call`'s `nudge`; answers to the others are dropped), 48 of 48 captures answered, the longest in 2s,
  the median 0.2s. Chrome is made to draw the tab with a screencast of 16px frames nothing reads (`screenshot.DRAWN`),
  for each capture (`screenshot.drawn`) and for each queue's whole length (`screenshot.drawing` in `steps.run`):
  every tab in a minimized window is one it does not draw. A click there waited out chrome-devtools-mcp's 3s for the
  page to settle (2.9s a click; drawn 0.2s), and a scaled capture asked again while the first waited could leave the
  tab laid out at its scale, halving its viewport at 0.5 and moving every point read off a later screenshot (4 of 15
  asked again every 1ms, on the research profile 1249x1277 to 313x320). So a capture of a drawn tab is asked once (0
  of 15 shrank, a page taking 700ms a frame included); where Chrome will not draw it, only one at one image pixel per
  device pixel (a clip `scale` of 1) is asked again. Every profile's Chrome on Windows also starts with overlay
  scrollbars (`system.CHROME_FLAGS`), as a Mac's are: Windows' own take 15px of the viewport, and a background tab's
  viewport flipped between the two widths as it was laid out (1234 to 1219 CSS px), which moves a centred page's
  content between a screenshot and a press read off it.
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
  step fails saying so, and the pointer is kept where the input put it. A `click_down`'s report names what it lands
  on (`pressed the left button at 545,85 on button "Fill color"`), read by `hit.read` on the step's own connection
  just before the press goes out: `DOM.getNodeForLocation` at the point (in whole CSS px of the document, so a
  scrolled page's point is offset by its scroll; skipping a `pointer-events: none` layer, as the press does), then
  `Accessibility.getPartialAXTree` there, the tree a snapshot's view comes from, so the names match a view's. The read
  climbs from the element hit to the first control (`hit.CONTROLS`) and names it; it stops below an element that holds
  controls (`hit.HOLDERS`: a menu, a toolbar, a list, a dialog, the page), so a press in a menu between its items
  names the menu, with no words of its items'. With no control, it names the text drawn right in the element hit (a
  paragraph's, a clickable div's, an SVG text's), else its role (a canvas). Where the tree gives no words at all, the
  read asks the page's own DOM (`hit.DOM_WORDS`, by `DOM.resolveNode` and `Runtime.callFunctionOn`) for the words
  its comment lists, from the element hit and those above it. GeoGebra's tool tiles are wordless images in buttons
  under `aria-hidden`, and CNN puts its consent dialog there too, so both read as nothing named until then (seen on
  2026-10-04 through browserd: `on: "Segment"` refused on GeoGebra's tile). A frame from another site, which Chrome
  keeps in another process, is named only by where it is from. The tree's three calls take about 2 ms (measured: each
  1 ms or less on Windows). A `click_down` names what it presses in `on`, a few words as the screenshot shows them, and a
  queue with one of count 1 that has none is refused before any step runs; `hit.carries` checks them against what
  the read found, and a press there that does not carry them is not sent: the step fails, saying what is there, and
  the queue stops. Words match as `hit.carries` says, so a popup, a layer or a reload under the point stops the press when it does not carry them, and so does a layer already there that the screenshot did not
  show (one at opacity 0). `on: ""` presses what has no words (a canvas, a map, a drag's handle) and checks nothing.
  A frame from another site, or a point browserd could not read, fails any `on` but `""`. Only the first press of a
  double or triple click takes `on`. Keys are not checked. chrome-devtools-mcp's own `click_at` (behind `--experimental-vision`, which browserd does
  not pass) cannot hover or drag: WebGames' herding needs the pointer moved over a canvas, and an agent given
  `click_at` spent 40 of them standing in for moves.
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
  `checked.py` adds checked steps that read the page back (all but a `paste` without a uid); `tools.STEPS_HELP` tells agents
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
    pressed once, since `cdp.INPUT_FLAG` (`chrome/README.md`) keeps Chrome from dropping
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
- **No tool types a password safely.** Every step's arguments are recorded in the tab's record folder, and a refused
  call's in server.log too.
