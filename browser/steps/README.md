# browser/steps

## Module TL;DR

What a `queue` call runs: its steps, and the report they make. A step is one of chrome-devtools-mcp's tools, sent to the
tab's process (`../tabs/`), or one of browserd's own: the checked steps (`pick`, `expect`, `type`, `paste`, `wait`), the
pointer steps (`move_at`, `click_down`, `click_up`), and a `take_screenshot` of the viewport. `../README.md`'s Gotchas
bind this folder too: read it first.

## Directory Layout

    steps/
      steps.py       a queue: load, check, place_screenshots, run; view, a snapshot cut to what an agent reads
      checked.py     the checked steps; fill_refused and read_fills, which judge a fill before it runs
      pointer.py     move_at, click_down, click_up: mouse input over browserd's own connection
      hit.py         what a press lands on, and whether it carries the words the step's on names
      screenshot.py  a viewport take_screenshot in CSS pixels; keeps a queue's tab drawn
      dialogs.py     answers a dialog the moment it opens, for a handle_dialog step

## Core Abstractions & Shared Pieces

- **A queue** is `load` (the steps, or a file of them) → `check` (every step, before any runs) → `place_screenshots`
  → `run`, which hands each step to `_step` and passes its reply through `view` and `_capped`, one report section per
  step. A queue stops at the first failed step, or before a step once it has run `QUEUE_MOST`; its report then names
  the steps not run and ends with where the page now is saved, and after a refused press within `SHOT_BEFORE` a
  screenshot of it (`_after_stop` says why). `run`'s docstring says how a stopped queue is marked.
- **browserd's own steps.** `checked.py` and `pointer.py` each give `STEPS`, `KEYS` (the arguments each step takes),
  `problem` (what `check` refuses), `describe` (their lines of the `steps` argument's description) and `run`; `check`
  and `_step` look a step up in `STEPS`. A new step needs all five.
- **Two ways to the tab.** A chrome-devtools-mcp step goes through `devtools` with the tab's `page_id`. The dialog
  answerer, each pointer step (and `hit.read` on a press's connection), a viewport screenshot, paste's key press and
  `screenshot.drawing` each open a connection of their own to the tab's `target` with `connect`; both come from the
  tab's `Worker`.

## Agent Gotchas & Invariants (⚠️)

- **A step is `{"tool": name, ...arguments}`, never with `pageId`**: the tab argument chooses the page. `check` refuses
  the whole queue for any step that could only fail, so a typo at step 40 does not run steps 1 to 39 first.
- **Claude Code drops a tool's reply after about 60s** (measured: a 55s queue came back, a 65s one did not, though the
  server ran it to the end). So a queue starts no step after `QUEUE_MOST` (50s); a step's `timeout`, and a `wait`'s or
  `pick`'s wait, is cut to the time left; and a timeout over `checked.WAIT_MOST` is refused.
- **Claude Code drops an error result's images, and cuts one over about 10,000 characters out of its middle** (seen in
  a trace; a success of 17,593 came whole). So a stopped queue is not an error result; only a call refused before its
  steps run is one.
- **A dialog blocks the page.** A `handle_dialog` step right after the step that opens one answers it as it opens
  (`dialogs.Answerer`), or up to `dialogs.LATE` after, unless that step is in `OWN_DIALOGS` and answers its own;
  puppeteer marks a dialog closed by another connection handled, so chrome-devtools-mcp goes on. A chrome-devtools-mcp
  step that opens one with nothing to answer it counts as done once chrome-devtools-mcp's 5s wait on it runs out, and a
  pointer step after `pointer.DIALOG_WAIT`. A pointer step or viewport screenshot with a dialog open already fails after
  5s, saying to answer it first.
- **A viewport `take_screenshot` is browserd's own**, one image pixel per CSS pixel (fewer past `screenshot.LONGEST`
  or with `scale`), the coordinates the pointer steps take; chrome-devtools-mcp's is in device pixels, twice CSS pixels
  on a Retina Mac. Chrome does not draw a tab in a minimized window, nor reliably a background one, so `run` keeps its
  tab drawn while it runs (`screenshot.drawing`).
- **A `click_down` names what it presses, in `on`** (only the first press of a double or triple click). Just before
  the press goes out, `hit.read` reads what is at the point; a press there that does not carry those words
  (`hit.carries`), or that `hit.read` could not read, is not sent, and the queue stops. So a popup, a reload, or a layer
  the screenshot did not show (one at opacity 0) stops the press. `on: ""` presses what has no words, and checks
  nothing. Keys are never checked.
- **Every snapshot a report holds is a view** (`_view`'s docstring), or with `full: true` its lines as written; either
  is cut at `VIEW_MOST`. The whole snapshot is saved in the tab's record folder, and the header names the file.
- **A step that begins a download says where it went**, since chrome-devtools-mcp's reply never does (an agent told
  nothing failed WebGames' combination lock hunting for the file): `run` waits up to `DOWNLOAD_WAIT` after each step
  while a download the tab's `downloads.Watcher` heard is still in progress (`../chrome/`).
- **Text agents read repeats some of these numbers and lines.** `../tools.py`'s `QUEUE_HELP` and `STEPS_HELP` say
  10,000 characters (`VIEW_MOST`), 5s (`dialogs.LATE`) and 100 characters (where chrome-devtools-mcp's `fill` sets a
  value by script), and describe the report's lines; `../../tests/checks/steps.py` matches those lines word for word,
  and `queue_steps` acts on chrome-devtools-mcp's "No page found". Change them together.
