# Findings: clearer tool descriptions, and thinking per turn

Run 2026-09-26, to test one idea for after the benchmark: less thinking per turn through simpler, clearer queue
descriptions.

**Result:** clearer text did not cut thinking per model call. Main and every rewrite that ran came to 132 to 135 hidden
output tokens per call (median). The version measured last, v5, is level with main within noise: 27 passed against
28, 19 tool errors against 16. Its median run was 2s faster and its total cost 7% lower, but run for run it was faster
in 24 of 48 and cheaper in 26: no difference shown. Two sentences the blind reviewers called jargon, and a third cut
with them, were most likely what kept agents from calling a step's name as a tool.

Raw data:
- transcripts and scores in `~/Desktop/PROJECTS/browserd-bench/results/`:
  - `desc3/` (main as arm `base`, v3 as `desc`), `desc4/` (v4) and `desc5/` (v5), each beside its `.log`;
  - Playwright, and main before the benchmark fixes: `probe1/` (`playwright`, `browserd`); after them: `fix8/`.
- in `.run/experiments/queue-descriptions/`, which is gitignored:
  - the tool lists as served, `tools-base.md` and `tools-v0.md` to `tools-v5.md`, and each version's diff against
    main, `v3.diff` to `v5.diff`;
  - the reviewers' brief and transcripts, `brief.md` and `reviews/`;
  - the scripts, run from that folder:
    - `wtserver.py <worktree> <port> [--profile NAME]` serves a worktree on ports of its own, with its own `.run/`;
    - `dumptools.py <port> <out.md>` writes a server's tool list;
    - `runbench.py --exp desc3 --arms base,desc --k 2` is the bench's `run.py` with the two arms added;
    - `compare.py desc3 base desc` counts hidden output per call, errors and calls per tool;
    - `think_calls.py` gives where thinking falls in a run, the wait and wait_for counts, tab_opens without steps,
      and the runs a paired difference needs.

## The question

On MCP-Universe, browserd agents wrote more per model call than Playwright's: 128 hidden output tokens per call
against 74 in the `probe1` run, and 137 after the benchmark fixes (`fix8`, browserd only). The idea was that agents
deliberate over the queue's long description.

**Measure:** `claude -p` sends thinking redacted in its stream-json, so thinking is counted as hidden output: the
run's output tokens less the characters of its visible text and tool arguments at 3.5 a token, per model call. The
stream's `system/thinking_tokens` estimates come to about a fifth of that in the median run, and under half in
total, so they are used to see which calls in a run carry the thinking, not to size it.

## Method

1. **Blind review, three rounds of three.** Each reviewer, a Sonnet 5 agent with no other context, read only the tool
   list as the server gives it (`tools/list`) and three MCP-Universe-style tasks. It planned its calls, marked every
   point it had to decide something, quoted what confused it, and proposed cuts. Each round's findings made the next
   version: v0 (main) became v1, v2, then v3. v1 and v2 never ran on the benchmark.
2. **MCP-Universe.** The 24 tasks scored offline, 2 runs each, `claude -p` with Sonnet 5, 2 runs at a time.
   - Arm `base`: main at 0506c2b, served from its own worktree on 9280, profile BenchB.
   - Arm `desc`: the rewrite, served on 9270, profile BenchD.
   - v3 ran task by task beside `base` (`desc3`). v4 (`desc4`) and v5 (`desc5`) ran alone, 30 minutes to 2 hours
     after `base` finished, and are compared with `desc3`'s `base`.

## What the blind reviewers found

- **Round 1:**
  - Every task says "close the browser", and session_start said no agent ends a session, with no way to do both (all
    three).
  - `fill` was never named as the step for a text box; it read as the step for selects and dates only (all three).
  - "Never pass pageId" names a field that no schema shows (two of the three).
  - The record folder's absolute path, and the aside that Claude Code drops a reply after about 60s, read as noise.
- **Round 2:**
  - The rule that a new page makes every uid new sat mid-paragraph.
  - Whether word k of a uid run counts from 0 or 1 was unclear.
  - "Dialogs" read as covering cookie banners.
  - `wait`'s three conditions read as ones that combine.
- **Round 3:** wording only, such as which step "that step" means in the dialog sentence, wait's "(default 30000, most
  45000)" and navigate_page's "asks to stay".

By round 3 every reviewer settled the close-the-browser, `fill` and new-page questions straight from the text.

## MCP-Universe results

48 runs per arm. Paired comparisons match each run with main's run of the same task and repeat.

| | main | v3 | v4 | v5 |
|---|---|---|---|---|
| passed | 28 | 27 | 28 | 27 |
| hidden output per call, median | 133 | 135 | 133 | 132 |
| hidden output per call, mean | 168 | 162 | 157 | 173 |
| paired: less hidden output per call than main | | 22 | 25 | 23 |
| seconds, median | 30 | 29 | 26 | 28 |
| seconds, mean | 52 | 49 | 38 | 44 |
| paired: faster than main | | 27 | 32 | 24 |
| cost | $25.49 | $26.89 | $21.93 | $23.70 |
| paired: cheaper than main | | 21 | 30 | 26 |
| tool errors | 16 | 27 | 15 | 19 |
| step names called as tools | 1 | 7 | 9 | 1 |
| tab_open / tab_close calls | 117 / 90 | 145 / 136 | 133 / 115 | 139 / 110 |
| wait / wait_for steps | 47 / 5 | 28 / 30 | 12 / 0 | 20 / 14 |

**Where the thinking goes.** A run's first model call, where the agent has just read the tool list, carries a mean of
21 to 36 estimated thinking tokens in every arm, Playwright's (`probe1`) included. The later calls, which choose a
`find` regex, a uid or a link on the page in front of them, carry the rest: from the fourth call on, a mean of 96 to
137 estimated thinking tokens per call on browserd's arms and 49 on Playwright's. Thinking follows reading the page,
not reading the tool text.

## What the rewrites broke, and why

Each change below was first measured in v3, though some came in v1 or v2.

- **The tab_open line**, "to go to another page in a tab you have, use a queue's navigate_page instead": agents in 5
  runs then called `navigate_page` as a tool. It went in v4, yet v4 made 7 such calls: the missing sentences below
  explain both better.
- **"wait_for for text to appear"**: `wait_for` rose from 5 steps on main to 30, several of them 30s timeouts. It went
  in v4.
- **The rewording of `wait`** ("wait for exactly one of"): 2 waits refused for their arguments. v4 went back to main's
  wording.
- **The cut of main's guidance on `wait`**, "uid and value for a field it fills (passing at once if the field holds it
  already), or gone with its status text". Every version since lacked it: wait steps fell from 47 on main to 20 on v5,
  and wait_for rose to 14, 6 of them failed; v4 lacked it too and made no wait_for step. Not traced further; the
  committed text restores it (below).
- **v4 still called step names as tools, 9 times:** navigate_page (7, five of them with session and tab) or
  take_snapshot bare (2), mostly right after a `tab_open` or a lone `take_snapshot` queue. The likely cause was three
  sentences cut from the queue's own description, the only text there saying the catalog's function signatures are
  steps, not tools:
  - "Never pass pageId: the tab chooses the page" (46aba6a);
  - "The steps argument's description lists every tool a step may name" (2b62921, which moved the catalog into the
    steps argument, since Claude Code cuts a tool's description at about 2,000 characters);
  - "a chrome-devtools-mcp tool reports success once it has acted" (134de34).

  Round 1's reviewers read two of them, pageId and "a chrome-devtools-mcp tool", as undefined jargon; none mentioned
  the third. A reader of the text alone cannot see what they prevent. v5 restored the first two, named
  chrome-devtools-mcp's tools again in a new sentence ("chrome-devtools-mcp's tools and browserd's own, which run only
  as steps, here or in tab_open's steps, and are never tools to call by themselves"), and left the success rule in the
  steps argument, as v3 had it: 1 such call in 48 runs, as on main. v5 also added a sentence saying so outright, so
  the run does not show which change did it. The earlier drop, from 9 such calls in `probe1` to
  1 in `fix8`, came with the seven benchmark fixes, most likely tab_open taking steps (60e39f5).
- **The record folder's path** was cut in v3 and restored in v5: the queue's `file` argument is read relative to that
  folder, and agents with file tools read their records there.

## What the committed text keeps, against main

The committed text is v5 plus accuracy fixes from pre-commit review, made after the runs and not measured.

- **session_start:** "Only the user closes a session, on the browserd page; no tool closes one or the browser. If your
  task says to close the browser, tab_close each tab of your session (tab_list lists them); otherwise leave them open."
  It keeps main's line on how a session closes (the user on the browserd page, or browserd stopping), and drops that
  a pause stops the processes driving a session's tabs. Agents made more tab_close calls than on main:
  136 on v3 and 110 on v5, against 90.
- **queue:**
  - chrome-devtools-mcp's tools and browserd's own "run only as steps, here or in tab_open's steps, and are never
    tools to call by themselves", in place of the success-rule sentence, which moved to the steps argument;
  - the record folder line drops the `001-queue.json` and `001-queue.txt` names;
  - a view's table cells come row by row (a row with no name has no line of its own);
  - the 60s aside is cut, and a stopped queue "names the steps not run".
- **the steps argument:**
  - the new-page rule comes first;
  - `fill` is named for a text box, and `press_key` Enter after a one-line text box's fill submits that box;
  - the rest report success once they act, so `expect` follows one whose result matters, while pick, type, wait and
    a paste given a uid read the page back themselves;
  - dialogs are alert, confirm and prompt only, never a modal or banner the page draws itself, and the
    unanswered-dialog sentence reads cause first;
  - `expect` reads a spinbutton's real value;
  - a uid run covers a table's one-word cells too, "for uids 5_1 to 5_25", and its words "keep their uids in order"
    in place of "word k has uid 5_(1+k)";
  - `navigate_page` leaves a page "even when the page asks to confirm leaving".
- **the step catalog:** `type`'s line loses its copy of the 100-character rule, and pick's example reads "like just
  the city".

Fixed after the runs, against v5:
- claims that were wrong: that every paste reads the page back, that a table "reads row by row", that `press_key` Enter
  after any fill submits it (now a one-line text box's fill), and "tab_close each tab you opened", which left out popups
  and tabs the user handed over;
- main's wording back for the date formats (v5's made a month read as a DateTime), for which steps open a dialog (a
  click, a key press) and when handle_dialog answers a late one, for wait's guidance, for how a session closes and
  that a call with its id resumes it, and for `full: true`; main's fact, in new words, for evaluate_script taking no
  handle_dialog step;
- wording: dialogs are "never a modal or banner the page draws itself" (v5: "a page's own popups"), navigate_page's "the
  page asks to confirm leaving" (v5: "a leave-site prompt"), and a uid run is drawn "as a canvas app like Slides draws
  its words or a table its one-word cells" (v5: "Slides or a table row draws them").

## Caveats

- 48 runs per arm, 2 per task. A run's time and cost vary up to fourfold between a task's two runs: the paired log
  ratio against main has a spread of 0.5 to 0.64. Showing a 7% difference at 80% power needs about 400 to 660 paired
  runs, 17 to 28 per task in each arm, roughly $400 to $680.
- v4 and v5 ran after `base` finished, not beside it, on live sites that can change.
- Both arms used fresh Chromes, BenchB and BenchD, not Research with its history.
- Hidden output counts tool arguments at 3.5 characters a token: an estimate.
- The bench's `run.py` stops a batch on any transcript holding "Not logged in". It stopped `desc4`'s first batch on an
  agent's "Not logged into Hugging Face". From then on the runs used a wrapper, `runbench.py`, that matches claude
  -p's own line, "Not logged in · Please run /login"; `run.py` still has the loose match.
- One batch stopped on a single `/json/version` timeout from BenchB's Chrome, which answered again at once. Its run
  ran again.

## Next

- Cut what a page costs to read, not what the tool text says. The 10,000-character view cut on branch
  `benchmark-fixes` (b5019bd) measured 38% fewer input tokens on 40- and 80-section pages. `tab_open` could also
  return a short view without being given a step: on main, 30 of 117 tab_opens carried none.
- Blind reviewers find what reads badly, but before a sentence is cut, check in git why it was added: text that
  reads as jargon can be what prevents a mistake.
