# long-tasks

## Module TL;DR

Long, bounded browser tasks for a demo of browserd: a real agent doing an hour-scale job on real sites, in the user's own
logged-in Chrome (the `personal` profile), ending in Google files, with the tab it works in recorded as a timelapse.
Each task names its entities, its sources (pinned where they can be), its fields and the files and slides it ends in, so
a longer run means the harness struggled, not that the model chose to dig deeper. Each prompt stays 1,200 to 1,450
characters; the work, 15-35 minutes on browserd in calibration runs, comes from many entities and editor features, not
from the prompt's length. capex is written as a spec with an answer key and graded in code; parks, a My Maps map, is asked as a
person would and graded in code; trip is asked as a person would, and an agent grades the deck and Sheet against a
rubric. `compare.py` runs any task on browserd and on other browser MCP servers, each given the same logged-in Chrome,
to compare them.

## Directory Layout

    long-tasks/
      capex/
        prompt.md   10 companies' 10-Ks, found on EDGAR; a Sheet with formulas, two pivot tables and their charts; a
                    13-slide deck of tables and the charts, linked
        key.py      builds key.json from EDGAR's XBRL facts, each checked against the filing's text
        key.json    3 fiscal years of 3 values a company in $ millions, free cash flow, capex share, slide title, table
      parks/
        prompt.md   a My Maps map: 28 national parks from a pinned Wikipedia revision, a layer and pin color a state,
                    each pin titled, described and given a photo; a driving route through Utah's gateway towns
        key.py      builds key.json from that revision
        key.json    each park's layer, date, acres, visitors and point; the route's towns
      trip/
        prompt.md   a team offsite in 3 short paragraphs, as a person would ask: 8 cities' flights, hotel, weather and
                    a restaurant near the hotel; a Sheet with its chart; a 13-slide deck ending in the pick's itinerary
        rubric.md   the judge's prompt: how to read the deck and Sheet, the evidence files, the items; its
                    {request}-style placeholders are filled by judge.py
        key.py      builds key.json: each city's November high from a pinned Wikipedia revision
        key.json    the 8 cities and their November highs (±2°F passes); fares, hotels and restaurants are live
      run.sh        one run: a clean Claude Code on claude-sonnet-5-5 with the task's prompt, record.py beside it
      record.py     the timelapse of the tab an agent works in, captured over DevTools, into frames/ and video.mp4
      grade.py      capex's and parks' checks against their keys (GRADERS); `flights`, Google Flights' nonstops now,
                    for trip's judge
      judge.py      grades a trip deck and Sheet: claude-opus-5-5 on browserd with rubric.md and the run's evidence
      compare.py    a task on each arm (browserd, playwright, devtools, agentbrowser) k times, one at a time in
                    browserd's Chrome, each recorded and graded; gallery

## Core Abstractions & Shared Pieces

- **A task** is a folder with `prompt.md`, which holds everything the agent is told. capex's is a spec: the companies,
  where to find their filings and what not to use, each value's line, the Sheet's headers and tabs, the slides. trip's
  and parks' say the outcome, not the steps (parks links its one source and says how to add each pin). Each has one
  browserd sentence ("Use browserd with my personal profile..."), which `judge.py` leaves out of trip's request.
  `compare.py`'s `BROWSERD_SENTENCE` finds it, once in `prompt.md` or `compare.py run` stops; the other arms get
  `NEUTRAL` for it, and browserd's own keeps it, naming `judge.PROFILE` for personal.
- **A key** is `key.json`, written by the task's `key.py`, never by hand: the values a correct run ends with.
- **A run** is `run.sh <task> <name>`: Claude Code, interactive so its turns can be filmed, started in the run's own
  folder, `<data>/<name>/` (`../browserd-long-tasks` beside the repo, or `$BROWSERD_LONG_TASKS_DATA`), with
  `--setting-sources ""` (none of the user's CLAUDE.md, rules, memory, skills, hooks or plugins), `--tools ""` and
  browserd from `--mcp-config` alone, its tools allowed up front. The login is the user's own, so nothing is set up
  first. The session label `record.py` waits for is read from the prompt's "with the label" line; with none (trip,
  parks), it follows the profile's first session started after it.
- **The working tab** is the one whose record folder (`.run/calls/<profile>/<session>-<label>/<tab>/`) changed last:
  each queue writes there. `record.py` finds the session by its label in `state.db` (or, with `--transcript`, by the id
  session_start returned in a stream-json transcript), reads the tab's DevTools target from its row in `state.db` and
  captures it on a connection of its own, so browserd never knows and the tab need not be in front. For a Chrome
  browserd does not drive, `--cdp <port> --folder <dir>` captures its visible tab instead (of several windows', the one
  whose URL changed last).
- **capex's grade** is `grade.py capex <deck URL> <sheet URL>`: the deck's `.pptx` export and the Sheet's `.xlsx`,
  fetched with the profile's cookies from a background tab of its own on the file's host (closed after), read with
  `zipfile` and ElementTree, then one ok/BAD line per check and a score. Text is compared through `norm`. **parks'** is
  `grade.py parks <map URL>`: the map's KML (`/maps/d/kml?mid=…&forcekml=1`, fetched the same way from
  www.google.com), read by `read_map`. My Maps' "Image URL" tab refuses outside URLs, and the dialog's Google Images
  search finds one. A missing layer, pin, row or slide fails every check on it, so a task's runs all have as many
  checks.
- **trip's judge** is `judge.py <deck URL> <sheet URL> <transcript> <folder> [--before F]`: a clean `claude -p`
  (`$BROWSERD_LONG_TASKS_CLAUDE`, else `claude` on PATH) on claude-opus-5-5 with browserd (profile `judge.PROFILE`:
  `$BROWSERD_LONG_TASKS_PROFILE`, else personal), Read and Grep, given `rubric.md` with the request, the deck's and
  Sheet's URLs, and the cities, normals and items from `key.json`.
  Its folder holds the evidence: `flights-before.json` (`grade.py flights` as the run began) and `flights-after.json`
  (now), from `nonstops`, which reads each Google Flights result's aria-label on the default results and on the
  Cheapest tab; `seen.txt`, every tool result of the run. Its last message is a JSON verdict, saved as `verdict.json`;
  `score` counts the items each group passed, a missing item failing. It then closes the browserd session it opened
  (`close_sessions`, the browserd page's Close session).
- **A comparison** is `compare.py run <exp> [--task trip|capex|parks]` (trip by default): per run,
  `<data>/<exp>/<arm>-r<n>/`, a headless `claude -p` (the judge's Claude Code) with run.sh's flags, the arm's server
  alone, `TIMEOUT` (2 hours), and `record.py` beside it (browserd's by `--transcript`, the others' by `--cdp`); then the
  files the task's final message must link (`FILES`, found by `LINKS`), the deck's PDF and one PNG per slide
  (`pdftoppm`) for a task with a deck, then the grade. trip's is the judge, given the deck and Sheet, in
  `<data>/<exp>/judging/<random id>/`, so neither its folder nor its request names the arm; capex's and parks' are
  `grade.GRADERS` on `judge.PROFILE`, and no judge. `result.json` has the task, the files' URLs (`deck`, `sheet`,
  `map`), the run's numbers and scores: trip's `correct`, `polish` and `looks`; capex's and parks' `score` (checks
  passed, checks made) and `checks` (each check's ok/BAD line); `passed` when every correct item or check passed. A
  `result.json` of `{"skipped": why}`, written by hand, keeps a run from starting. `compare.py report <exp>` sums the
  runs in a table per task, leaving out skipped ones, and writes `gallery.html` (each
  run's slides in a row) and `gallery-blind.html` (rows shuffled and lettered, the key in `gallery-key.json`). browserd
  runs on the main server's profile `judge.PROFILE` names, and every other arm attaches over DevTools to that same
  Chrome (its profile's port), so all arms drive one browser signed in to the same account. Each run begins with
  `clean_start`: the Chrome left holding one fresh blank tab, in a window of its own, where every server starts; after
  it, the run's browserd session (if any) is closed and `clear_tabs` closes every tab no open browserd session owns, so
  nothing passes from one run to the next. Runs go one at a time (`--jobs` 1, threads over the run list in order, each
  rep's arms reversed from the last's).

## Agent Gotchas & Invariants (⚠️)

- The sources are pinned so a key stays right: capex's key names each 10-K by its accession, and the prompt the latest
  10-K filed before Oct 1, 2026, which picks the same ones (AAPL's next comes about Oct 30); trip's and parks' key.py
  read each Wikipedia page at a revision (`oldid`). Fares, hotels and restaurants move, so no key can hold them. A new
  filing is a new task version: change `FILINGS` in `key.py` and the date in `prompt.md` together, then run `key.py`
  again.
- `--safe-mode` would hide the user's setup too, but it also drops `--mcp-config`'s servers: a run would have no
  browserd. A fresh `CLAUDE_CONFIG_DIR` works but needs its own login.
- A run's folder is never reused: a second run needs a new name, so no recording is overwritten.
- `record.py` and `grade.py` import browserd from this checkout and read the records `browser/config/paths.py` names: run
  them from the checkout the server on 9230 runs from, or set `BROWSERD_HOME` to that server's records.
- The grader's tab is opened over DevTools, not through browserd, so it is in no session, and its fetch must wait for
  the file host's own page (docs.google.com, or www.google.com for a map): the tab's first context, about:blank's,
  goes when the page loads.
- No export shows that capex's last slides' charts are linked to the Sheet: that is left to the eye. A Sheets chart
  reaches the `.xlsx` export as `xl/charts/`, a pivot table as `xl/pivotTables/`.
- An `.xlsx` may write same-shaped formulas as one shared formula, its text in the first cell alone
  (`<f t="shared" ref="H2:H5" si="0">G2/F2-1</f>`) and an empty `<f t="shared" si="0"/>` in the rest, so
  `read_sheet` records only whether a cell has an `<f>`. That Sheets' export does so is read from the runs' grades
  (H2 a formula, H3 to H5 none, in every run): no export has been opened.
- capex's key holds what the prompt asks for where filings differ: NVDA's capex line includes intangibles
  (`PaymentsToAcquireProductiveAssets`), TSLA's is net of sales; ORCL's and CRWV's free cash flow is negative. TSLA's
  EDGAR list shows a 10-K/A (Part III only) above its 10-K. A company whose 10-K holds its statements only in an exhibit
  (IBM's annual report) cannot be one: the prompt has the values read in the 10-K itself.
- My Maps finds no driving route to a park's own place ("Couldn't find a route"), so parks' route goes through towns.
  Grade a run after it ends, not during it: the map's KML lags its last saves (`grade_parks`).
- `clean_start` opens the run's one window minimized (`opens.window`), and Chrome does not draw a tab in a minimized
  window (`../../browser/steps/README.md`, the screenshot gotcha): `record.py` asks each frame again until one comes,
  but another server's own screenshots and clicks may wait on one. Un-minimize the window before a run, and keep it
  open, even behind others.
- SEC refuses a User-Agent without a contact address: `key.py` sends a placeholder one, `SEC_USER_AGENT` a real one.
- `grade.FLIGHTS_SEARCH` is Google Flights' own encoding of trip's search, copied from its address bar (dates, LAX, SEA,
  nonstop); another city swaps in for SEA. New dates in `prompt.md` need a new copy. Judge a run at once: fares move
  within minutes, and the before/after pair misses a fare that came and went during the run, so the rubric also takes
  a Google Flights list seen.txt shows the agent reading (an hour-long run saw two such fares).
- Google Flights' "Cheapest" tab lists the same flights lower, through third parties; the prompt allows either, so
  `nonstops` reads both, clicking the tab (`CHEAPEST_TAB`) in its background tab.
- `judge.ITEMS` builds rubric.md's `<city>_` items from `key.json`'s cities; the judge's request comes from
  `prompt.md`, so the two stay in step. A new city is a line in `trip/key.py`, a name in the prompt's list and one
  more in its slide count, then `key.py` again.
  The prompt names Wikipedia for the weather, whose climate tables also hold a "Mean maximum" row 10-20°F higher, so the
  rubric takes only the mean daily maximum, within 2°F.
- `seen.txt` is the run's tool results as they came, so a harness's own wording in them (Playwright's code lines,
  browserd's step reports) can tell the judge which arm it grades; only its folder and request are blind.
- `compare.py` takes the deck's PDF before the judge opens it: the gallery shows the deck as the run left it.
- The other arms share browserd's `judge.PROFILE` Chrome because Google signs out a copy of a profile's folder within
  about 10 minutes (its cookies cannot be renewed in another Chrome), and Chrome runs one process per folder. They see
  every tab of that Chrome and start on one (Playwright, chrome-devtools-mcp and agent-browser on the newest, but
  Playwright on another when several windows are open), which is why `clean_start` leaves only its fresh tab, and why
  runs never go two at once. A tab of an open browserd session (the user at work) makes a run wait, never closed.
- agent-browser's `close`, Playwright's `browser_close` and chrome-devtools-mcp leave the Chrome running: each closes
  its own tab at most (checked on a throwaway Chrome).
- Every run shares the account's Drive and Google Flights' recent searches. A run that reports a file an earlier run
  made is not graded ("an earlier run's deck", "... sheet" or "... map"), but a copy of one has a new URL: capex and
  parks name their files, and Sheets, My Maps and Slides' Insert > Chart list earlier runs' files of that name. Move
  earlier runs' files to the Trash before a batch.
- An experiment holds one task: a run's folder is `<arm>-r<n>` whatever the task, and one with a `result.json` is done,
  so `--task capex` on an experiment with trip's runs skips those arms' reps. Give each task its own `<exp>`.
- Run `compare.py run` detached (`nohup`): a batch started under a Claude Code session dies with its window.
