# Findings: browserd against Playwright MCP and chrome-devtools-mcp, and what was fixed after

Run 2026-09-25 to 2026-09-26. browserd was compared against Playwright MCP and bare chrome-devtools-mcp on three
public benchmarks (399 agent runs, $166 of model usage), its gaps were fixed, and the fixes were measured again on
MCP-Universe and on two small suites of the bench's own.

**Result:** accuracy is a tie everywhere. browserd uses fewer turns and tokens on long, many-step tasks, since the queue
batches them, and is slower on lookups and forms. After the fixes its median MCP-Universe run went from 35s to 24s
(Playwright: 18s), with half the tool errors, but a long tail of runs kept its total cost above the first run's.

Raw data is in `~/Desktop/PROJECTS/browserd-bench/`, whose README says how to run the public suites:
- transcripts and scores, one folder per experiment in `results/`:
  - `probe1/` (MCP-Universe), `ff1/` (FormFactory), `wg1/` (WebGames): the first comparison;
  - `fix8/` (MCP-Universe after the fixes), `clicks1/` (click accuracy), `hay1/` (the view cap);
  - `_invalid/`, the runs thrown out (below);
- `report.py <exp>` scores an experiment; `scratch/analyze.py` breaks down its tool use, `scratch/miscalls.py` counts
  calls of a name browserd does not serve;
- in `scratch/`: `replay*.py`, `pairrepro.py`, `attach.py` and `pp*.mjs`, the pairing bug's replays and probes (below).

The tool-description experiment that followed is [queue-descriptions.md](queue-descriptions.md).

## Setup

- **Agent:** `claude -p`, model `claude-sonnet-5`, every built-in tool off (`--tools ""`), so the browser MCP is the
  only way to the web. Same prompt, model and scoring for every arm; only the MCP server differs.
- **Arms:**
  - `browserd`: `http://127.0.0.1:9230/mcp`, profile Research, a real headed Chrome, tabs in the background. One
    system line added: "Your browser is browserd; its profile is Research."
  - `playwright`: `npx @playwright/mcp@latest --headless --isolated` (0.0.82), MCP-Universe's own config.
  - `devtools`: browserd's own pinned chrome-devtools-mcp 1.9.0, stock, `--headless --isolated`: the engine under
    browserd's queue without browserd, so browserd against devtools isolates what browserd's layer adds.
  - `next` (the reruns): the `benchmark-fixes` branch served from its worktree on 9250 by `nextserver.py`, profile
    Bench, so the main server on 9230 was never restarted.
- One run per task per arm (k=2 on MCP-Universe). The first comparison ran 4 to 6 at a time, the reruns 2.

## First comparison

| Benchmark | What it tests | browserd | Playwright | devtools |
|---|---|---|---|---|
| MCP-Universe browser_automation (24 tasks × 2) | lookups on live sites, JSON answer | **25/48** (52%) | 23/48 (48%) | not run |
| FormFactory (50 forms) | fill a real form from a document | **30/50** (60%), 94% of fields | 28/50, 93% | 28/50, 94% |
| WebGames (51 challenges) | widgets, timing, games, puzzles | **45/51** (88%) | **45/51** (88%) | 42/51 (82%) |

| Median per run | MCP-Universe (b / pw) | FormFactory (b / pw / dt) | WebGames (b / pw / dt) |
|---|---|---|---|
| Time | 35s / 18s | 41s / 23s / 27s | 39s / 37s / 36s |
| Turns | 10 / 8 | 8 / 9 / 9 | 9 / 12 / 12 |
| Input tokens | 206k / 176k | **160k** / 201k / 228k | **191k** / 227k / 298k |
| Total cost | $24.06 / $16.51 | $11.79 / $11.90 / $12.82 | $30.55 / $25.53 / $33.09 |
| Model time | 24s / 15s | 20s / 21s / 24s | 30s / 30s / 35s |
| Tool (non-model) time | 9s / 4s | 10s / 1.5s / 3s | 9s / 4s / 3s |

Paired per task, no difference is beyond noise (sign tests, p ≥ 0.5). browserd did better / worse on 4 / 3 tasks
against Playwright on MCP-Universe, 3 / 1 against each other arm on FormFactory, and 6 / 3 against devtools and 3 / 3
against Playwright on WebGames.

### MCP-Universe (lookups)

A tie on accuracy; browserd about 2× slower and 46% dearer:
- **Each page cost two calls.** Agents used `tab_open` as navigation (155 for 48 runs), and it returned only the tab
  id, title and URL, so every page then needed a `queue [take_snapshot]`. Playwright's `browser_navigate` returns a
  short page summary, and its agents then pulled just the lines they needed with `browser_find` (92 calls).
- **The queue does nothing here.** The median queue call had 1 step (mean 1.4); 162 of the 248 queue calls were a
  lone take_snapshot. Lookups have nothing to batch.
- **More text read:** about 30k characters of results per run against 14.5k for Playwright.
- **Overhead:** a session_start per run, and 124 tab_close calls (21% of all calls), one per tab, prompted by the
  tasks' "close the browser".
- **Errors:** 10 calls never reached browserd: a queue step's name (`take_snapshot`, `navigate_page`) called as a
  top-level tool, or `Read`, which these runs disable. The rest were real: 30s "not interactive" click timeouts,
  `wait_for` timeouts, "No snapshot found for page N", a stale uid, a made-up URL.

### FormFactory (forms)

25 real-world forms (job applications, loans, insurance claims, NDAs), 2 gold records each. The agent gets the record's
document, fills the form and submits it; scoring is on what the server received.
- **Accuracy:** every arm got 93 to 94% of fields. The misses were mostly the same fields for every arm: ambiguous gold
  values (an abstract whose gold is a one-line summary) and extraction choices, not the browser.
- **browserd read the fewest tokens,** 160k against 201k and 228k: a whole form's fills went in one queue call (mean
  4.8 steps, up to 25). Its cost came out level anyway, from more output tokens writing those queues.
- **Why it was slow:**
  - **The date field.** The accessibility tree shows a date input as `Date "Date of Birth"` over three spinbuttons
    (Month, Day, Year). Agents filled the Month spinbutton, and chrome-devtools-mcp failed it with "did not become
    interactive" after **30s** in browserd, 5s headless: **20 of 50 browserd runs**, 21 failed fills, 608s in all. A
    `fill` on the `Date` line with `1957-08-01` works in 0.7s.
  - **Each field is slower.** A `fill` took about 0.4s, since browserd reads the field first (`fill_refused`):
    about 8s of tool time per form, against 1.5 to 3s for the others. Playwright's `fill_form` does a whole form
    almost at once.
- Checked steps were rarely used: 62 `expect`, 5 `pick`, 5 `type` against 624 `fill`s. Plain HTML forms don't need
  them.

### WebGames (widgets and games)

The 51 challenges of convergence-ai/webgames' Hugging Face test split, each revealing a password when solved. The
paper's best model scored about 50%.
- **Only browserd solved:**
  - **shopping-challenge,** 20+ repetitive add-to-cart steps: `[fill, click, take_snapshot]` in one queue call per
    item, done in 23 turns, while both others ran out at 40. The queue's clearest win.
  - **robo-check (a CAPTCHA),** with `take_screenshot` and `click_at`; both others looped until out of turns.
- **browserd failed:**
  - **combination-lock (downloads).** The task downloads a note to read. The file landed in the real `~/Downloads`
    and no tool said where, so the agent hunted through `file://` listings; `tab_open chrome://downloads` could not
    be paired. Both others read the page's `URL.createObjectURL` blob in JS; Playwright MCP also saves downloads to a
    folder of its own and says where.
  - **herding (pointer movement).** The game needs the mouse moved across a canvas; browserd had `click_at` but no
    move by coordinates, and the agent spent 40 `click_at`s standing in for moves. Playwright used
    `browser_run_code_unsafe` (`page.mouse.move`).
  - **frog-crossing,** a real-time arcade game: out of turns. **block-stack, brick-buster, emoji-remember** failed in
    other arms too.
- **browserd leaned on vision:** 241 take_screenshots and 229 click_ats, 220 images kept in context, hence $30.55
  against Playwright's $25.53 despite fewer turns and tokens.
- Two agents called `tab_show` on their own, and it failed: "macOS did not bring that Chrome to the front".
- A click on a moving target also waited 30s before failing, the same wait as the date fields.
- **No cheating found:** no run read the site's source for a password. Every arm read the page's own DOM (a chart's
  SVG).

## What was fixed

Every gap the benchmark found, and what came of it. Every commit named is on main.

| Gap | Evidence | Fix |
|---|---|---|
| A failed click or fill took 30s in browserd, 5s headless | FormFactory dates, WebGames' moving target | ba23cf7: the tab's page is selected in its chrome-devtools-mcp, which sets its own 5s and 10s timeouts only on a selected page |
| A date filled through its Month spinbutton | 20/50 FormFactory runs, 608s lost | 430523f: a date or time field is one view line, its parts left out; `fill` refuses a part or a value Chrome would leave empty |
| Each page cost two calls | 155 tab_opens, then a take_snapshot each | 60e39f5: `tab_open` takes `steps` and runs them on the new tab in the same call |
| Downloads | combination-lock lost | bb72f44: a step that begins a download says where the file went |
| Screenshots in device pixels, read through a file | 4 WebGames agents called `Read`, which these runs disable, on a screenshot's saved file; a Retina image's points are 2× CSS pixels | a6d4d6e: a viewport `take_screenshot` is browserd's own, in CSS pixels, returned as an image |
| No pointer movement without an element | herding lost | ff8c6a9: `move_at`, `click_down`, `click_up` |
| Images cost tokens on every later request | 220 images kept in context on WebGames | 814c57b: `take_screenshot`'s `scale` (0.5 is a quarter of the tokens); its click accuracy measured below |
| tab_close one call per tab | 21% of calls, 27% after the fixes | 4efe755: `tab_close` takes a list |
| Long views re-read on every turn | 145 of 537 views over 10,000 characters in `probe1` and `fix8` | b5019bd: a view over 10,000 characters is cut, its note naming how to read on; measured below |
| Queue step names called as tools | 9 on MCP-Universe, 2 on WebGames; 7 of the 11 came right after a `tab_open` | 60e39f5, as `tab_open` now takes the steps: 1 bad call after the fixes. The queue's description, and the lines that keep steps from being called as tools, are in `queue-descriptions.md` |
| A tab chrome-devtools-mcp never lists | 3 of 12 runs of the view-cap test (below) | 69ef04c: a placeholder tab keeps a window open |
| Each successful fill is slower | 0.4s per fill | not fixed: a whole form's tool time is seconds against a run's model time; if it matters, batch the read-first check across a queue's fills |
| Tabs pile up across sessions | the Research Chrome reached about 130 processes and hung | the runner closes each run's session (a harness change, not browserd's); browserd itself still keeps a paused session's tabs open: closing or capping them is not done |

## MCP-Universe after the fixes (`fix8`)

The 24 tasks × 2 again, arm `next`, against the first run's browserd and Playwright. This was run after the fixes
above up to 814c57b; `tab_close`'s list, the view cap and the placeholder came after it and are not in it.

| | browserd before | browserd after | Playwright |
|---|---|---|---|
| Passed | 25/48 | 26/48 | 23/48 |
| Median time | 35s | **24s** | 18s |
| Mean time; 90th percentile | 50s; 137s | 48s; 134s | 36s; 86s |
| Median turns | 10 | 8 | 8 |
| Tool errors | 22 | **10** | 22 |
| Step names called as tools | 9 | **1** | |
| Tool calls | 586 | 532 | 459 |
| Input tokens, all runs | 17.0M | 18.7M | 15.3M |
| Total cost | $24.06 | $26.81 | $16.51 |

- **The typical run got faster and cleaner,** from `tab_open`'s steps (171 tab_opens, and 163 queue calls against 248
  before) and the 5s timeouts.
- **The total got dearer,** from a long tail: a few runs looped on lone `take_snapshot` or `find` queues, re-reading
  long views, and every later request carries each one again. The view cap is aimed at that tail.
- **tab_close was 144 of 532 calls (27%),** one per tab, which `tab_close`'s list now makes one call.
- **The gap left to Playwright is the model, not the server.** browserd's non-model time is 4s in the median run,
  level with Playwright's, and 10s of a 48s mean run; the rest is model time, from more turns (a mean of 11.9 against
  10.5) and more hidden output per model call (137 tokens against 74). browserd's visible tool arguments ran to 671
  characters per run (median) against Playwright's 240. Clearer descriptions did not change the hidden output
  ([queue-descriptions.md](queue-descriptions.md)).

## Click accuracy at scale 0.5 (`clicks1`)

`clicks.py` on `clickserver.py`: a canvas of 12 numbered squares, 3 each of 4, 8, 16 and 32 px, to click in order
from screenshots, 3 seeds, each run three ways: every screenshot at full size, every one at scale 0.5, and the agent's
own choice.

| Squares hit | 4 px | 8 px | 16 px | 32 px |
|---|---|---|---|---|
| Full size | 6/9 | 7/9 | 9/9 | 9/9 |
| Scale 0.5 | 6/9 | 9/9 | 9/9 | 9/9 |
| Agent's choice | 8/9 | 7/9 | 9/9 | 9/9 |

- **At 16 px and up, scale 0.5 cost nothing:** every square was hit in every arm.
- Under 16 px the misses show at every scale, full size included; 9 runs are too few to rank them.
- Given the choice, agents took full-size screenshots themselves.
- The step help says so: scale 0.5 to see what is where, no scale to read small text or aim at anything under about
  16 CSS pixels.

## The view cap (`hay1`)

`haystack.py` on `hayserver.py`: a staff handbook of 40 or 80 sections (a 40-section page's view is about 57,000
characters), one fact hidden in a middle section: a locker code in a sentence (`code`), the one staff row whose badge
expired in 2019 (`row`), or the one section whose safety officer is also its deputy (`twin`); 2 seeds each, 12 tasks.
Arms: `cap`, the view cap (b5019bd, on 9250), and `nocap`, views cut only at a reply's 40,000 characters (4efe755, on
9260, profile Bench2).

| | cap | nocap |
|---|---|---|
| Found, all 12 | 12/12 | 12/12 |
| 9 tasks neither arm hit the pairing bug on: mean time | **11.4s** | 22.0s |
| Median time | **12s** | 17s |
| Input tokens | **749k** | 1,202k |
| Cost | **$1.48** | $2.59 |
| All 12: median time; cost | 12.5s; $2.51 | 17.1s; $3.46 |

- **The cap did not hide the fact,** and cut input tokens 38%: without it, agents fell back to `full: true`, dumps of
  about 40,000 characters that stayed in context for every later turn.
- **Caveats:** the `nocap` arm also lacked `after`, which came with the cap; every needle could be found by a `find`
  regex; one run per task.
- 3 of the 12 `cap` runs (row-40-s1, twin-80-s1, twin-80-s2) hit the pairing bug below, whose failed calls inflate
  that arm's all-12 numbers.

## The pairing bug: a tab chrome-devtools-mcp never lists

In those 3 runs, `tab_open` opened the tab and loaded the page, `tab_list` listed it and `tab_show` could show it, but
every call that ran steps on it failed: "no page chrome-devtools-mcp lists holds its marker", 16 in all (13 queues,
and the steps of the 3 first tab_opens). Each queue
started a new chrome-devtools-mcp, and each failed the same way. The agents recovered by closing the tab and opening a
new one; in row-40-s1 the second tab failed too.

**Cause.** The runner closes each run's session, so the next run's `tab_open` finds the Chrome with no window open and
makes a new one. In a new window, Chrome's omnibox prerenders a Google search page (`warmup.html`) into the window's
tab, as a hidden child page. Puppeteer, connecting, waits for each tab's first child to attach; when the hidden page
attaches first, Puppeteer finishes connecting before the tab's own page is attached, so chrome-devtools-mcp's first
`list_pages` lists no page for the tab. Which child attaches first held steady for a given tab, so each new process
failed the same way. A fresh throwaway Chrome never prerenders, so the live checks never saw it.

**Measured** by replaying the runs' browserd calls against the Bench Chrome with no agents (`scratch/replay*.py`):

| | Tabs that failed |
|---|---|
| Before, each tab_open in a new window | 6/25 and 8/25, each failing for over 60s |
| A tab opened while another held the window | 0/25 |
| Pairing listing the pages again, up to 5 times (86f5f51) | 0/25 |
| A placeholder tab (69ef04c), kept open between runs | 0/25 |
| A placeholder tab, every page closed between runs | 0/25; the prerender went to the placeholder |

- The prerender was present in every new-window tab (25 of 25), so it is needed but not enough: whether it attaches
  first is a race.
- **Kept:** the placeholder (69ef04c) replaced the relisting (86f5f51). A `tab_open` in a Chrome with no page of its
  Chrome profile open first opens a `data:` placeholder tab that no listing includes, and the tab goes into its
  window. A tab that still cannot be paired fails its queue saying to open its url again with `tab_open` before
  closing it with `tab_close`.
- **Not covered:** a tab in a window browserd did not open (the page's Open Chrome, or a tab opened by hand and handed
  over), where the race is not measured.

## Not finished

- **botwall:** 20 live sites known for bot checks, where browserd's real Chrome should do best. The bench's own probe,
  not a public benchmark.
- **MiniWoB++:** 38 form and widget tasks with autocompletes, date pickers and book-flight.
- **devtools arm on MCP-Universe.**
- **FormFactory and WebGames after the fixes;** the WebGames rerun was cancelled.

botwall and MiniWoB++ are built and smoke-tested (`botwall.py`, `miniwob.py` with `mwserver.py`). The first batch
stopped when `claude -p` began failing with "Not logged in" (the keychain seemed to have locked) and the Research
Chrome hung; those runs are in `results/_invalid/`.

## Incidents

- **The Mac ran out of memory** in the first batch:
  - no run cleaned up: every browserd run left its tabs open, each with a chrome-devtools-mcp process (about 180MB)
    until its session paused 30 minutes later;
  - 6 agents ran at once, each Playwright or devtools run with its own headless Chrome, beside another session's
    6-agent experiment on the `test` profile;
  - nothing watched memory, and nothing stopped when the Research Chrome hung.
  - Since then `run.py` runs 2 at a time, starts a run only with 25% of memory free, refuses to run beside another
    batch, kills each run's process group, and closes each run's browserd session on the browserd page.
- **Files left in `~/Downloads`** by browserd runs: "grampa's old note.txt" (×3) and "credentials.txt" (×2), WebGames'
  own files.
- **Port 4173 was taken** by another local site, so WebGames moved to 4380; the first WebGames smoke run hit that site
  and was thrown out.
- **`run.py` stops a batch on an agent's own words:** its check for a logged-out `claude -p` matches "Not logged in"
  anywhere, so an agent writing "Not logged into Hugging Face" stops the batch. `claude -p`'s own line is "Not logged
  in · Please run /login". Not fixed.

## Caveats

- **Few runs.** One run per task on FormFactory, WebGames and the bench's own suites, so small differences are noise.
- **Not the same browser setup.** browserd drives a real, headed Chrome with its tabs in the background; Playwright and
  devtools run headless. Part of the timing difference comes from that.
- **MCP-Universe:**
  - only its 24 tasks scored by exact JSON match: 7 Google Maps tasks need an API key, and 4 Booking tasks re-scrape
    booking.com when scored;
  - its own scoring, so exact keys are required;
  - the agent is Claude Code rather than MCP-Universe's ReAct agent, with 30 turns instead of 20.
- **FormFactory:**
  - its own evaluator scores a model's text, not a browser's submission, so the harness scores the submission;
  - short fields must match exactly (after case and spacing); textareas and list values pass at 80% word coverage,
    since their gold is often a summary;
  - 8 gold values were mapped to the option a person would pick, and "Paper Category" is not scored.
- **WebGames:** served locally from the repo, whose passwords match the Hugging Face split. The prompt is WebGames'
  own; one system line forbids reading the site's source for the password.
