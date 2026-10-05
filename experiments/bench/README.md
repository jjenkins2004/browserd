# bench

## Module TL;DR

Measures browserd against other browser MCP servers on public benchmarks. Every run is `claude -p` with every
built-in tool off (`--tools ""`), so the browser MCP server is the only way to the web. The model, prompt and scoring
stay the same across arms; only the server changes. This folder holds the code; the data (the benchmarks' own repos,
every run's transcript, the scores) lives in a data folder outside the repo (`paths.DATA`, below). Every result is in
`../findings/benchmark.md`. `../../.claude/skills/benchmark/SKILL.md` walks an agent through a rerun.

The suites, each `--suite` of `run.py`:

| Suite | What it measures | Tasks | Scored by |
|---|---|---|---|
| `mcpuniverse` | research on live sites (arXiv, Hugging Face, sports stats), a JSON answer | 24, run twice (`--k 2`) | MCP-Universe's exact JSON match, and a lenient score |
| `formfactory` | actions: fill 25 real-world forms from a document, 2 records each | 50 | the submission the form server got, field by field |
| `botwall` | real live sites known for bot checks: search, name the first result | 20 | a result reported and no block (the bench's own probe) |
| `webgames` | widgets, timing, games, puzzles | 51 | the challenge's password in the answer |
| `miniwob` | synthetic form and widget drills | 38 × 2 seeds | the page's own reward |
| `clicks`, `haystack` | click accuracy on small targets; finding a fact on long pages | 9; 12 | the bench's own |
| `canvas` | pixel clicks in apps that draw (Excalidraw, Desmos, GeoGebra, diagrams.net, Maps, EtherCalc) | 6 | the app's own state, read through browserd after the run |
| `popups` | first-visit consent banners, welcome dialogs and look-alike buttons on 7 live sites (Forbes, HubSpot, Kayak, CNN, BBC, the Guardian, Sephora) | 7 | the popup seen, the press on the right button or the page's state after, and no press on its look-alike |
| `slides` | edits to a new, blank Google Slides deck per run, on the `experiments` arm's profile signed in to Google | 6 | the deck's own pptx export, read in the run's tab after the run |
| `traps` | clicks an agent never meant: a fake editor whose timed traps go up over its next target | 12 seeds | the page's own log of every trusted click and key |
| `pagenow` | what a stopped queue's reply should show of the page now: replicas of real pages where a queue step fails early (`../findings/page-now.md`) | 5 | the app's own log and the answer; export and consent by the answer alone |

The last run of the other servers' arms (2026-09-29, `final2`) was `mcpuniverse`, `formfactory` and `webgames`.
`miniwob` (drills) and `botwall` have not been run in full.

    python experiments/bench/setup.py                      fill the data folder once
    python experiments/bench/sites.py start                the local sites the suites need (WebGames, FormFactory, MiniWoB++, ...)
    python experiments/bench/nextserver.py --tree <tree>   browserd from a worktree of its own, on 9250, for the next arm
    python experiments/bench/chain.py <name> <arms> "<suite>[:<run.py args>]"...     suites one after another, detached
    python experiments/bench/run.py --exp <name> --suite <suite> --arms <arms>
                                                           one suite, in the foreground; always pass --arms, whose default is
                                                           every arm, the main server's included
    python experiments/bench/report.py <exp>               score an experiment, compare its arms
    python experiments/bench/sites.py stop

Everything runs the same on macOS, Linux and Windows; `procs.py` is the one place that differs by OS (how a process
tree is started and stopped, keeping the machine awake, where a venv keeps its Python, and Windows' `.cmd` shims).
`python` is Python 3: `python3` where `python` is not on PATH.

## Directory Layout

    bench/
      README.md       this file
      paths.py        where the code (BENCH, ROOT) and the data (DATA, RESULTS) are
      setup.py        clones each benchmark at its pinned commit, builds WebGames, fetches its tasks, Flask venv
      sites.py        start|stop the local sites, detached, logs in the data folder
      chain.py        several suites as one detached batch, each resumed once if it stops
      run.py          ARMS, the claude -p call, 2 runs at a time, cleanup, the stops
      procs.py        a process tree started and stopped the same way on every OS; Windows' .cmd shims
      browserd_call.py  browserd tool calls from the bench's own code: a suite's prepare and collect
      transcripts.py  what a run did, read off its transcript: its content blocks, and what its presses did
      canvas.py       suite canvas: pixel clicks in drawing apps, graded from the app's state
      popups.py       suite popups: first-visit consent banners and look-alike buttons on live sites
      slides.py       suite slides: edits to a new Google Slides deck per run, graded from its pptx export
      report.py       results/<exp>/scores.json, the per-arm summary, the task-by-arm table
      nextserver.py   browserd from a worktree on 9250/9251, beside the main server
      tasks.py        suite mcpuniverse: MCP-Universe's tasks, prompt and scoring, and tasks.lenient
      formfactory.py  suite formfactory: gold records, the documents, field-by-field scoring
      botwall.py      suite botwall: 20 live sites
      webgames.py     suite webgames
      miniwob.py      suite miniwob
      clicks.py       suite clicks: squares of 4 to 32 px to click in order
      haystack.py     suite haystack: one fact on a 40- or 80-section page
      traps.py        suite traps: 8 tasks in a fake editor, 6 timed traps over the next target
      ffserver.py     FormFactory's Flask app on 5055, each submission saved under its run
      mwserver.py     MiniWoB++'s pages on 4390, each reward saved under its run
      allow.py        the claudechrome arm's --permission-prompt-tool: allows every permission prompt
      clickserver.py  the clicks page on 4395
      hayserver.py    the haystack pages on 4396
      trapserver.py   the trap editor (trapapp.html) on 4397, each run's log under results/trap-logs/
      pagenow.py      suite pagenow: its apps (pagenow/) are served by pnserver.py on 4398, each run's log under
                      results/pn-logs/
      pnbatch.py      one page-now batch on the pn arms, with each scenario's k and the hashes of what it runs
      pnfork.py       forks each page-now run at its stops, 5 times per reply at its first stop and once at a later
                      one; pnhook.py (the fork's PreToolUse hook) and stubmcp.py (its browserd, answering from the
                      page at the stop) serve each fork
      assets/         sample.pdf, which setup.py copies to the data folder's assets/
      tools/          analyze.py (tool use by arm), paired.py (sign tests), miscalls.py, cheats.py, ffmap.py,
                      trapcheck.py (traps' scoring on synthetic logs; a real run's log against its layout history),
                      represses.py (an experiment's presses read again off its transcripts),
                      pnreport.py (the page-now experiment's numbers and decision)
      probes/         the pairing bug's replays and probes (../findings/benchmark.md, "The pairing bug")

    <data folder>/    paths.DATA: ../../../browserd-bench beside the repo, or $BROWSERD_BENCH_DATA
      results/<exp>/  config.json, one <arm>/<task>-r<n>.jsonl transcript and .err per run (.part until its
                      collect and cleanup are done), scores.json
      results/<exp>.log, results/chain.log      each run's line; each chain's starts, stops and ends
      results/_invalid/<exp>-<why>/             runs set aside (a broken setup, an outage), never deleted
      forks/<exp>/, sessions/<exp>/             pnfork.py's forks, and the Claude Code sessions they were cut from
      pn-tools/                                 each pn arm's initialize and tools/list, which pnfork.py capture records
                                                for the stub
      MCP-Universe/, formfactory/, webgames/, miniwob-plusplus/    the benchmarks' repos, from setup.py
      webgames-data/hf-test.jsonl, .venv/, *.log
      assets/sample.pdf                         formfactory.UPLOAD, from setup.py

## Core Abstractions & Shared Pieces

- **A suite** is a module giving `SYSTEM`, `MAX_TURNS`, `load()` ({task name: task}), `prompt(task, token)` and
  `score(task, answer, token)`, and optionally `check()`, which refuses to start while its site is down, and
  `RECORDS`, the folder where each run leaves `<token>.*` files its score reads, and `SESSIONS`, which keeps each
  run's Claude Code session, its id in `<task>-r<n>.session` beside the transcript, for `pnfork.py`. On an arm with
  a `profile`, a suite may also give `prepare(task, mcp_url, session, token)`, which sets its page up before the run in
  a browserd session of the runner's own, and `collect(task, token, transcript, mcp_url)`, which reads what the run
  left on its tabs before the runner closes its sessions, both through `browserd_call.py`. `token` (`run.token`) names
  one run, so a suite whose scoring reads what the run did on a page (formfactory, miniwob, clicks, traps, canvas,
  popups, slides) finds that run's own record. `run.SUITES` lists them.
- **An arm** is one entry of `run.ARMS`: its MCP config, a system line, and for a browserd arm (`run._browserd`) the
  page URL whose Close session the run's cleanup uses and the profile its runs use. The current arms:
  - `next`: browserd from a worktree, served by `nextserver.py` on 9250 with profile Bench, so the main server (9230)
    and its profiles are never touched. Point the worktree at the commit to measure (`git worktree add`, then
    `git -C <worktree> switch --detach <commit>`) and restart `nextserver.py`, which keeps the code it started with.
    A new worktree needs `node_modules/` (a symlink to the main checkout's will do) and its own Bench profile
    (`--profile Bench`, which takes over the Chrome-Bench folder an earlier worktree made). A server beside another
    takes ports of its own (`--port`, and `--from` for its profile's Chrome).
  - `playwright`: `npx @playwright/mcp@0.0.82 --headless --isolated`, MCP-Universe's own config, pinned so a release
    mid-batch cannot change the arm.
  - `devtools`: this checkout's chrome-devtools-mcp (`../../node_modules`), stock, `--headless --isolated`: browserd's
    engine without browserd's layer.
  - `agentbrowser`: Vercel's agent-browser (0.38.1), `agent-browser mcp`, headless. Its browser lives in a daemon
    outside the run's process tree, so each run gets a session of its own (`AGENT_BROWSER_SESSION`, the run's
    token), and `agent-browser close` ends it after the run.
  - `claudechrome`: Claude in Chrome, Claude Code's own `--chrome` tools. Its browser is a headed Chrome of the
    bench's own, `<data>/chrome-claude/`, with the Claude extension signed in, started by hand with
    `--remote-debugging-port=9295`; after each run the runner closes its tabs over that port. Claude Code asks before
    each action on a site no `ClaudeInChromeDomain` rule names, whatever the permission mode, so `allow.py`, its
    `--permission-prompt-tool`, allows every ask; claude.ai, where that Chrome is signed in, is denied. One extension
    serves one run, so the arm is `solo`: its runs take turns, whatever `--jobs` says.
  - `experiments`: the main server (9230) on the profile signed in to Google, for `slides`.
  - `trap-on`, `trap-none`, `trap-guard`: the text-check experiment's arms (`../findings/text-check.md`), worktrees of
    their own served by `nextserver.py` on 9250, 9260 and 9270.
  - `browserd` (the main server on 9230, profile research), `cap` and `nocap` are earlier experiments' arms.
  - `pn-a`, `pn-b`, `pn-c`: the page-now experiment's arms, the worktrees `browserd-pn-<letter>` served by
    `nextserver.py` on 9310, 9320 and 9330. Which stop reply each holds is in the data folder's `arms.json`, never in
    what the agent sees.
- **A run** is `run.run_one`: `claude -p` (PATH's, or `$BROWSERD_BENCH_CLAUDE`) with the arm's servers only
  (`--strict-mcp-config`), the model (`--model`, default `claude-sonnet-5`), the suite's max turns, and its transcript
  streamed to `results/<exp>/<arm>/<task>-r<n>.part`, each line stamped `_t` (when it arrived), renamed `.jsonl` once
  its collect and cleanup are done. Arms interleave task by task, so each sees a live site at about the same time. A
  run whose `.jsonl` ends in a result is done, so running an experiment again runs only what is missing, a run cut off
  before its cleanup included.
- **Scoring** is `report.py`, through each suite's `score`. For MCP-Universe it gives the benchmark's own strict score
  and `tasks.lenient`, which forgives formatting alone:
  - the JSON is taken from any text around it, unless the text holds several JSON values that differ;
  - a task whose format names keys its evaluator does not use (`Name_of_the_paper_1` where it wants the paper's
    title) is scored on its values alone, exactly, so two IDs swapped between papers still pass;
  - otherwise a value may go on after the expected one past a space (`"Onboarding — Set you up…"` for
    `"Onboarding"`), and case, spacing and a period ending a line are ignored; a value that starts with the format's
    own placeholder (`"Y or N"`) fails;
  - it leaves out `huggingface_task_0002`: its GAIA.py sits in a gated dataset that needs a Hugging Face login, which
    no arm has.

  FormFactory's own evaluator scores a model's text, not a browser's submission, so `formfactory.py` scores the
  submission: a gold key names the field whose label it is (exactly, then by the field's name, then by prefix;
  `ALIASES` for one that matches none); values match after lower-casing and spacing, dates and numbers by value,
  phones by digits, a select or radio by its value or text, a gold "None" by an empty field; a textarea or list passes
  at 80% of the gold's words of 4 or more letters; `EQUIVALENT` maps 6 gold values no option says in the same words;
  file fields, gold keys naming no field, repeatable groups and "Paper Category" are not scored. A run passes when
  every scored field is right; `report.py` also gives the share of fields right. `tools/paired.py` gives sign tests
  between arms.

## Agent Gotchas & Invariants (⚠️)

- **One batch at a time, 2 runs at a time.** A batch of 6 at a time that cleaned nothing up ran a Mac out of memory,
  and several agents opening heavy pages at once crashed a profile's Chrome out of memory on Windows (2026-10-04), so
  run `--jobs 2` or fewer, and one batch at a time; `run.py` checks neither memory nor other batches itself.
- **Never restart the main server (9230) for a run.** Measure a commit with the `next` arm: a worktree of its own
  served by `nextserver.py --tree`, which uses that worktree's own `.run/` (state, profiles, records). Never serve the
  checkout 9230 runs from: the two servers would share its `.run/`. A bench profile takes a Chrome port from
  `nextserver.BENCH_PORT` (9240) up, since the main server gives its own profiles the first free ports from 9223, and
  a profile it made later once took the Bench Chrome's port. A bench server beside another gives its profile a range
  of its own: `--profile <name> --from <port>`.
- **Start long batches detached** (`chain.py` does it itself): a batch started as a background task of a Claude Code
  session dies with that session's window, as one did on 2026-09-28.
- **Runs start in the data folder** (`cwd=paths.DATA`): `claude -p --setting-sources project` loads the settings of
  the folder it runs in, so a run started in the repo would load the repo's.
- **The stops.** A batch stops at the first run that `claude -p` ended in an error before its first tool call (logged
  out, a used-up plan) or whose transcript says a browserd profile's Chrome stopped answering; such a run does not
  count as done and runs again. It stops too at a run whose sessions could not be closed, which does count as done. It
  also stops after `run.DEAD_AFTER` (2) runs in a row of one arm that reached no browser (its server did not connect,
  or every call needing a Chrome failed): those count as done, so move their `.jsonl` and `.err` to
  `results/_invalid/<exp>-<why>/<arm>/` to rerun them. A rerun keeps the run's token (`run.token`), so before it
  starts the runner moves what an earlier attempt left in the suite's `RECORDS` to the folder of the same name in
  `results/_invalid/`. A wifi outage looks the same: set aside the runs of every arm in the outage's
  window, not just the flagged ones, and resume. A run the plan's session limit cuts off part way counts as done, and only the
  next run stops the batch: set aside each run whose transcript says "session limit", as well as the flagged ones.
- **Cleanup is the runner's.** After each run it stops the run's process tree (`procs.stop_tree`), closes the
  agent-browser session, and closes each browserd session the run started on the browserd page (Close session), only
  those. A run killed by hand leaves its sessions open: close them with
  `run.close_sessions(browserd_call.SESSION.findall(<transcript>), <page>)`, and an agent-browser run's with
  `AGENT_BROWSER_SESSION=<its token> agent-browser close`. Close only a run's own sessions: every other session on the page is the user's.
- **A run times out after `run.TIMEOUT` (900s)** and has no result, so it runs again when the experiment is resumed.
- **An experiment's name is its identity:** running a name again resumes it, keeping its done runs from whatever
  commit made them, so a new measurement needs a new name.
- **Never delete a run or an experiment:** set it aside in `results/_invalid/`.
- **On MCP-Universe every arm gets one line of the bench's own,** "Find every answer with the browser, not from
  memory", after MCP-Universe's instruction; without it an arm can pass from memory. `report.py`'s `noBrowse`
  counts runs with no tool call.
- **Deviations from MCP-Universe:** only its 24 tasks scored by `playwright.is_dict_equal` run (7 Google Maps tasks need
  an API key, 4 booking tasks re-scrape booking.com when scored); the `date` server is left out; the agent is Claude
  Code, not its ReAct agent, with 30 turns, not 20.
- **WebGames** passwords sit in its site's bundle, so its system line forbids reading the source, scripts or network
  responses; `tools/cheats.py` checks transcripts for it. **MiniWoB++** episodes start with the page (no START cover)
  and last 30 minutes, not 10 to 30 seconds; only the first counts. **botwall** answers are not checked against the
  sites.
