# long-tasks

## Module TL;DR

Long, bounded browser tasks for a demo of browserd: a real agent doing an hour-scale job on real sites, in the user's own
logged-in Chrome (the `personal` profile), ending in Google files, with the tab it works in recorded as a timelapse.
Each task names its entities, its sources (pinned where they can be), its fields and the files and slides it ends in, so
a longer run means the harness struggled, not that the model chose to dig deeper. capex is written as a spec with an
answer key and graded in code; trip is written as a person would ask it, and an agent grades the deck against a rubric.
`compare.py` runs trip on browserd and on other browser MCP servers, each given the same logged-in Chrome, to compare them.

## Directory Layout

    long-tasks/
      capex/
        prompt.md   the task as the agent gets it: 4 10-Ks, then a Sheet with a chart and a 6-slide deck
        key.py      builds key.json from EDGAR's XBRL facts, each checked against the filing's text
        key.json    the 24 values in $ millions, each company's growth, slide title and lines
      trip/
        prompt.md   a team offsite in 3 short paragraphs, as a person would ask: who and why, what to find, the deck
        rubric.md   the judge's prompt: how to read the deck, the evidence files, 13 correct and 5 polish items
        key.py      builds key.json: each city's November high from a pinned Wikipedia revision
        key.json    the 3 November highs, which rubric.md states (±3°F passes); fares and hotels are live
      run.sh        one run: a clean Claude Code on claude-sonnet-5-5 with the task's prompt, record.py beside it
      record.py     the timelapse of the tab an agent works in, captured over DevTools, into frames/ and video.mp4
      grade.py      capex's checks against its key; `flights`, Google Flights' nonstops now, for trip's judge
      judge.py      grades a trip deck: claude-opus-5-5 on browserd with rubric.md and the run's evidence
      compare.py    trip on each arm (browserd, playwright, devtools, agentbrowser) k times, one at a time in
                    browserd's Chrome, each recorded and judged; gallery

## Core Abstractions & Shared Pieces

- **A task** is a folder with `prompt.md`, which holds everything the agent is told. capex's holds rules (browserd's
  profile and the session label, only the named sources, "n/a" over another source, exactly the slides listed,
  `tab_needs_input` on a login or captcha, nothing submitted or sent, stop at the end state), then the spec. trip's says
  the outcome, not the steps or the links; its one browserd sentence ("Use browserd with my ...") is what `compare.py`
  swaps for the other arms' and `judge.py` leaves out.
- **A key** is `key.json`, written by the task's `key.py`, never by hand: the values a correct run ends with.
- **A run** is `run.sh <task> <name>`: Claude Code, interactive so its turns can be filmed, started in the run's own
  folder, `<data>/<name>/` (`../browserd-long-tasks` beside the repo, or `$BROWSERD_LONG_TASKS_DATA`), with
  `--setting-sources ""` (none of the user's CLAUDE.md, rules, memory, skills, hooks or plugins), `--tools ""` and
  browserd from `--mcp-config` alone, its tools allowed up front. The login is the user's own, so nothing is set up
  first. The session label `record.py` waits for is read from the prompt's "with the label" line; with none (trip), it
  follows the profile's first session started after it.
- **The working tab** is the one whose record folder (`.run/calls/<profile>/<session>-<label>/<tab>/`) changed last:
  each queue writes there. `record.py` finds the session by its label in `state.db` (or, with `--transcript`, by the id
  session_start returned in a stream-json transcript), reads the tab's DevTools target from its row in `state.db` and
  captures it on a connection of its own, so browserd never knows and the tab need not be in front. For a Chrome
  browserd does not drive, `--cdp <port> --folder <dir>` captures its visible tab instead (of several windows', the one
  whose URL changed last).
- **capex's grade** is `grade.py capex <deck URL> <sheet URL>`: the deck's `.pptx` export and the Sheet's `.xlsx`,
  fetched with the profile's cookies from a background tab of its own on docs.google.com (closed after), read with
  `zipfile` and ElementTree, then one ok/BAD line per check and a score. Text is compared through `norm`.
- **trip's judge** is `judge.py <deck URL> <transcript> <folder> [--before F]`: a clean `claude -p` on claude-opus-5-5
  with browserd (profile personal), Read and Grep, given `rubric.md` with the request and the deck's URL. Its folder
  holds the evidence: `flights-before.json` (`grade.py flights` as the run began) and `flights-after.json` (now), from
  `nonstops`, which reads each Google Flights result's aria-label on the default results and on the Cheapest tab;
  `seen.txt`, every tool result of the run. Its last message is a JSON verdict, saved as `verdict.json`; `score`
  counts the items each group passed, a missing item failing. It then closes the browserd session it opened
  (`close_sessions`, the browserd page's Close session).
- **A comparison** is `compare.py run <exp>`: per run, `<data>/<exp>/<arm>-r<n>/`, a headless `claude -p` with
  run.sh's flags, the arm's server alone, `TIMEOUT` (1 hour), and `record.py` beside it (browserd's by `--transcript`,
  the others' by `--cdp`); then the deck's PDF and one PNG per slide (`pdftoppm`),
  then the judge, in `<data>/<exp>/judging/<random id>/`, so neither its folder nor its request names the arm;
  `result.json` has the run's numbers and scores. `compare.py report <exp>` sums them and writes `gallery.html` (each
  run's slides in a row) and `gallery-blind.html` (rows shuffled and lettered, the key in `gallery-key.json`). browserd
  runs on the main server's `personal` profile, and every other arm attaches over DevTools to that same Chrome (its
  profile's port), so all arms drive one browser signed in to the same account. Each run begins with `clean_start`:
  the Chrome left holding one fresh blank tab, in a window of its own, where every server starts; after it, the run's
  browserd session (if any) is closed and `clear_tabs` closes every tab no open browserd session owns, so nothing
  passes from one run to the next. Runs go one at a time (`--jobs` 1, threads over the run list in order, each rep's
  arms reversed from the last's).

## Agent Gotchas & Invariants (⚠️)

- The sources are pinned so a key stays right: capex names each 10-K by its accession, and trip's key.py reads each
  Wikipedia page at a revision (`oldid`). Fares move by the minute, so no key can hold them. A new filing is a new task
  version: change `FILINGS` in `key.py` and the URLs in `prompt.md` together, then run `key.py` again.
- `--safe-mode` would hide the user's setup too, but it also drops `--mcp-config`'s servers: a run would have no
  browserd. A fresh `CLAUDE_CONFIG_DIR` works but needs its own login.
- A run's folder is never reused: a second run needs a new name, so no recording is overwritten.
- `record.py` and `grade.py` import browserd from this checkout and read the records `browser/config/paths.py` names: run
  them from the checkout the server on 9230 runs from, or set `BROWSERD_HOME` to that server's records.
- The grader's tab is opened over DevTools, not through browserd, so it is in no session, and its fetch must wait for
  docs.google.com's own page: the tab's first context, about:blank's, goes when the page loads.
- No export shows that slide 6's chart is linked to the Sheet, or that column H is formatted as a percent: those are
  left to the eye. That a Sheets chart reaches the `.xlsx` export as `xl/charts/` is untested: a run with a chart is
  the first proof.
- `clean_start` opens the run's one window minimized (`opens.window`), and Chrome does not draw a tab in a minimized
  window (`../../browser/steps/README.md`, the screenshot gotcha): `record.py` asks each frame again until one comes,
  but another server's own screenshots and clicks may wait on one. Un-minimize the window before a run, and keep it
  open, even behind others.
- SEC refuses a User-Agent without a contact address: `key.py` sends a placeholder one, `SEC_USER_AGENT` a real one.
- `grade.FLIGHTS_SEARCH` is Google Flights' own encoding of trip's search, copied from its address bar (dates, LAX, SEA,
  nonstop); another city swaps in for SEA. New dates in `prompt.md` need a new copy. Judge a run at once: fares move
  within minutes, and the before/after pair covers only a move during the run.
- Google Flights' "Cheapest" tab lists the same flights lower, through third parties; the prompt allows either, so
  `nonstops` reads both, clicking the tab (`CHEAPEST_TAB`) in its background tab.
- `rubric.md`'s item names are `judge.ITEMS`: change both together. The judge's request comes from `prompt.md`, so the
  two stay in step; the Nov highs in `rubric.md` come from `key.json`. The prompt names no weather source, so the
  rubric takes any value within 3°F.
- `seen.txt` is the run's tool results as they came, so a harness's own wording in them (Playwright's code lines,
  browserd's step reports) can tell the judge which arm it grades; only its folder and request are blind.
- `compare.py` takes the deck's PDF before the judge opens it: the gallery shows the deck as the run left it.
- The other arms share browserd's `personal` Chrome because Google signs out a copy of a profile's folder within
  about 10 minutes (its cookies cannot be renewed in another Chrome), and Chrome runs one process per folder. They see
  every tab of that Chrome and start on one (Playwright, chrome-devtools-mcp and agent-browser on the newest, but
  Playwright on another when several windows are open), which is why `clean_start` leaves only its fresh tab, and why
  runs never go two at once. A tab of an open browserd session (the user at work) makes a run wait, never closed.
- agent-browser's `close`, Playwright's `browser_close` and chrome-devtools-mcp leave the Chrome running: each closes
  its own tab at most (checked on a throwaway Chrome).
- Every run shares the account's Drive and Google Flights' recent searches. A run that reports a deck an earlier run
  made is not graded ("an earlier run's deck").
- Run `compare.py run` detached (`nohup`): a batch started under a Claude Code session dies with its window.
