# Findings: what a stopped queue's reply shows of the page now

Started 2026-10-04. A queue stops at its first failed step, or at a `click_down` whose `on` does not match what is at
the point. Today the stop reply is an MCP error (`isError: true`) that ends with a "page now" view of up to
`steps.ERROR_MOST` (9,000) characters. On the trip deck (`trip-compare.md`, prelim3) those views were 46% of browserd's
tool text and $1.49 of cache reads, and Claude Code dropped every image of an error result (36 screenshots). Checked
again on 2026-10-04: an error result still reaches the agent as text only.

Decided before this experiment: **a stop becomes a normal result** whose first line says the queue stopped, so its
screenshots reach the agent. Still open, and measured here: **what the stop reply shows of the page now.**

## Result (2026-10-04)

The batch `pagenow-1` (60 runs, 1,052 forks, $22) decided each kind of stop by the rule fixed before it ran:

| Kind of stop | Reply | Against keep, per stop | Time against keep, per stop |
|---|---|---|---|
| a step's uid is gone (uid) | note | −$0.029 [−0.040, −0.018] | about 1.5s slower: 0.74 more looks |
| a wait ran out (wait) | note | −$0.040 | the same: no reply led to a look |
| a press refused though what it named was at the point (false refusal) | note | −$0.056 | the same: no reply led to a look |
| a press refused under a popover (true refusal) | shot | −$0.053 [−0.063, −0.043] | about 0.9s faster: 0.37 fewer looks |
| a slip (a key name, a text that never showed) | none: 4 runs, under the 8 needed | | |

- **Each figure is per stop, not per run,** at the trip deck's scale: 189k tokens of context and 60 turns left.
  "Reading the numbers" below says how it is built.
- **note wins on cost, not on speed.** On uid stops its agent takes a look that keep's view would have spared it.
  Joshua weighed cost over speed (2026-10-04).
- **For browserd:**
  - At a stop, browserd knows whether a uid was gone, a wait ran out or a press was refused. It does not know
    whether a refusal was true, though its refusal said it could not read the point (consent's frame) in 12 of the
    13 false refusals, and in no true one.
  - So the policy it can follow is shot (note's line plus a half-scale screenshot) for every refused press, and note
    for every other stop.
  - On a refused press, shot saves $0.066 against note when the refusal is true, and costs $0.013 more when it is
    false. So it pays when more than about 1 refusal in 6 is true.
- **True refusals were decided after a fix to the oracle,** made once the batch was in ("The oracle's popover bug"
  below); before the fix the rule kept keep's reply there.
  - shot's saving comes from its agents not looking: 0 looks per stop, against keep's 0.37 and note's 1.00. The
    oracle does not touch looks.
  - The fix only made shot eligible.

## Plan, revision 3 (after review round 2), fixed before the pilot

### Question and replies

At a stop, which reply gets the agent back to work cheapest and fastest, without its next action going wrong more
often? Replies compared, per kind of stop:
- **keep:** today's view of the page now, cut so the report stays under 9,000 characters (never under 2,000);
- **short:** the same view cut to 2,000 characters;
- **note:** one line naming the file the page now is saved in; take_snapshot or take_screenshot shows it;
- **shot** (exploratory, forks only): note's line plus a screenshot of the page now at scale 0.5 (about 500 tokens).

**mixed** (exploratory) is computed afterwards from the keep and note forks, not forked itself: keep's reply when the
failed step named a uid, note's otherwise.

### Why forks, not whole runs

Review round 1 found that whole-run cost cannot separate the replies:
- keep's extra reply is about 2k tokens per stop, roughly $0.01 to $0.03 a run;
- the SD of cost after a stop is about $0.08 a run;
- separating the replies that way needs 90 to 200 runs per arm.

So every first stop of a real run is replayed once per reply, and what the agent does next is measured.

### Part 1: end-to-end runs

- **Arms:** three worktrees of `main` (e28fadd), `browserd-pn-a`, `-b` and `-c`. They are served by
  `nextserver.py` on 9310, 9320 and 9330, with profiles PnA, PnB and PnC on Chrome ports 9244, 9245 and 9246.
  - Each holds keep, short or note, assigned at random. The assignment is in the data folder's `arms.json`, never
    where the agent can see it.
  - Each has its own `npm ci`.
- **Shared by all three arms, uncommitted, the patches kept in the data folder:**
  - the stop-replies change: a stop is a normal result, its first line is `--- stopped …; not run: …`, and
    `steps.STOPPED` marks it for `tools.py` and the server log;
  - the same queue description, "…names the steps not run, and ends with the page now". It is checked identical
    across arms once the worktree folder name is replaced.
  - for the forks only, two viewport screenshots saved at each stop (scales 1 and 0.5, with their report lines), and
    when they were taken. Each is skipped once the queue has run 45s, limited to 2s, and never sent.
- **Runs:**
  - 5 scenarios x 3 arms x k = 2: 30 runs, 2 at a time, run alone, arms interleaved.
  - `claude-sonnet-5-5` on the pinned Claude Code 2.1.289 (`browserd-pn-data/bin/claude.exe`), with
    `DISABLE_AUTOUPDATER=1`.
  - `--max-turns 60` and `--tools ""`, as the bench and the trip ran.
  - `--include-partial-messages`, since usage is read from `message_delta`.
  - A fresh session id per launch, kept in a sidecar file, and each stream-json line stamped `_t`.
  - A run that times out (900s) or hits the cap is a failure and is never rerun.
- **Reported by arm** (descriptive; n is too small to decide on):
  - passes, stops per run and the first stop's kind;
  - whole-run cost, time and turns, and the same up to the first stop, as a check that the arms match until then;
  - the real next action after the first stop, which is what the fork oracle is validated against.

### Scenarios

Local replicas served by `pnserver.py` on 4398:
- The suite's `prepare` starts a new attempt (`/reset`). The server writes the attempt number into the page, which
  uses it in its storage key, and stamps it on every log line.
- The scorer reads the latest attempt only.
- The app files' sha256 are recorded in `config.json`.

| Scenario | Copies | Kind of stop | How the stop happens |
|---|---|---|---|
| `helpdesk` | Zendesk or Jira inline edits | uid | Every saved change re-renders all 36 rows 300 ms later, so a queue's second change on an old uid fails (probed: 5.1s). The first save also adds 2 tickets. The view is 19.4k characters. |
| `checkout` | Shopify | uid | Country/region rebuilds the address fields. A postal-code change replaces the shipping rates with "Calculating rates…" and new radios 800 ms later. A shipping choice rebuilds the payment block. Phone is required. The view is 3.8k characters. |
| `export` | Looker or Stripe exports | wait | The report takes 120s, past any one wait (45s at most). The summary comes from the server only once it is ready. The view is about 10k characters. |
| `deck` | Google Slides | press refused (true) | Slide text is SVG, which `hit.py` reads, as real Slides' is. There is a filmstrip, menus and a toolbar. On the first `move_at` that comes at least 1s after the first edit (the start of the next queue), a "Try the new themes" popover opens over the toolbar, as a non-inert `role=dialog` at the end of body. The next press that names a toolbar control is refused. |
| `consent` | BBC and the Guardian | press refused (false) | An article page of about 9k characters. Its consent dialog is a frame from another site (127.0.0.1 inside a page on localhost), which `hit.py` reads as `Iframe`, so `on: "Accept all"` is refused (BBC: 4 of 4). |

The pixel apps (`deck`, `consent`) log their layout, as `trapapp.html` does: the rect of every named control and
overlay at each change. That lets the oracle judge a press at a point.

### Part 2: forks at the stops (primary)

- **Which stops:**
  - the first stop of every Part 1 run, with 3 samples per reply;
  - at most one later stop per run, only of a kind not seen yet in that run, with 1 sample per reply;
  - a stop whose assistant message held more than one tool call is skipped.
- **The fork:**
  - The run's session file is cut after the last record of the stopped call's message, and a `hook_deferred_tool`
    marker is added. Every fork gets its own copy under a fresh id.
  - Claude Code is resumed with no prompt (`--resume --fork-session`, stdin from /dev/null).
  - It runs the deferred call against a stub MCP server, which serves the source arm's own `initialize` and
    `tools/list` as captured after the servers restart.
  - The stub answers that call with the reply under test: the original report's head and images, with the page-now
    part rebuilt from the saved snapshot by keep's own `steps.view` and `_capped`.
- **What the stub and hook do after that:**
  - **A look-only call** (only take_snapshot and take_screenshot steps, at most 3) is answered from the page as it
    was at the stop: the saved snapshot through `steps.view` with the step's options, or the saved screenshot at the
    nearer scale. A full-page or element screenshot is not served; it ends the fork as its first action, which the
    oracle judges ok, since a screenshot changes nothing.
  - **Any other call** is deferred by the PreToolUse hook, and that call is the fork's first action.
  - **Fail closed:** the stub answers nothing else. A fork must end in a deferred call or a final answer, or it is
    void. The hook's command uses forward slashes.
- **Running the forks:**
  - `pnfork.py` runs them in their own pool (2 at a time), as soon as each Part 1 run has been cleaned up.
  - Each fork has `--max-turns 6` and a 300s timeout. The replies run in random order per stop.
  - Failures go to `forks-failed.jsonl`, which the report counts.
- **Gate:** a fork's first request must read at least the source run's cached prefix from the cache. A miss voids
  the fork and is reported.
- **What each fork measures:**
  - looks before the first action, and the tokens they brought back;
  - the reply's tokens: the first request's total input minus the source turn's;
  - the first action, judged by the oracle;
  - the time of each turn.
- **Calibration:** forks given the source arm's own reply are compared with the run's real next action. Agreement
  is reported.

### The oracle: would the first action work?

- **Every scenario:** the first step names a uid the page-now snapshot does not hold.
- **helpdesk:** more than one change in a queue (the second goes stale), or a change to a ticket the task does not
  name.
- **checkout:** a step on a field or radio that an earlier step of the same queue rebuilt (country, postal code,
  shipping).
- **export:** a wait that ends before the report is ready.
- **deck and consent:** a press at a point the layout at the stop puts under an overlay, or under no element that
  carries the press's `on`. For consent, a named press on the frame is refused, and `on: ""` there is pressed.
- **Validation:** the oracle is checked against Part 1's real next actions before it is used. It needs 90% agreement,
  or that scenario's "would fail" is reported, not decided on.

### Cost model, per stop, against keep

`Δ = Δtokens·(w + T·r) + Δlooks·(C·r + o) + p_fail·(C·r + o + F·(w + T·r)) / (1 − p_fail)`, against keep's own terms.
- Δtokens: the reply's tokens plus the looks' tokens.
- F: the stop reply that a failed action would itself get, under that reply.
- w, r, o: claude-sonnet-5-5's rates as `modelUsage` prices the pilot's runs (Sonnet only; Haiku's title calls are
  left out): $4/M for a 1-hour write, $0.20/M for a read, $10/M for output, as trip-compare's fit had them.
- Time: (Δlooks + p_fail) x the measured turn latency, reported only.

### Decision, fixed before the pilot

- **Per kind of stop** (uid, wait, press refused true, press refused false):
  - **Decision point:** C = 189k (the trip deck's mean context) and T = 60. Also reported at the stop's own C, and at
    T = 15 and 120.
  - **Interval:** 90%, from resampling runs (not stops) within each scenario.
  - **Eligibility:** a reply can win a kind only if the upper bound of its p_fail minus keep's is under 10 points.
  - **Winner:** the eligible reply with the lowest mean Δ, if it beats every other eligible reply by at least $0.01
    and 10% per stop, with intervals that exclude zero. Otherwise keep's reply stays for that kind.
  - **Too few runs:** a kind with fewer than 8 contributing runs gets no decision.
- **Overall:**
  - If one reply wins every decided kind, that is the answer.
  - Otherwise the per-kind policy is the answer: browserd knows the kind when it stops.
  - Expected savings are shown across stop mixes (uid share 10%, 30% and 50%), not one weighted winner, since the
    recorded transcripts are skewed to refusals.
- **shot and mixed** are reported against the winner. Either becomes a recommendation only if it beats the winner by
  more than 10% at the decision point.

### Pilot, then review round 3

- `--exp pagenow-pilot`: each scenario once on each arm (15 runs), with their forks. It must show:
  - each scenario stops at its designed trigger in at least 2 of 3 runs;
  - images reach the agent in a stop's result;
  - per-message prices add up to Sonnet's `modelUsage` within 1%;
  - forks gate and end correctly on Sonnet;
  - the oracle's agreement.
- Pilot runs are not results. Review round 3 reads this plan, the code and the pilot's evidence before the batch.
- **Budget:**
  - pilot: about $9 of runs and $9 of forks;
  - batch: about $18 of runs and $30 of forks, for about 45 stops x 4 replies x 3 samples, later stops x 1;
  - about $66 in all.

### Changes after review round 3 and the pilot, fixed before the batch

- **Runs:** k per scenario and arm is helpdesk 3, checkout 3, export 4, consent 4 and deck 6, so each kind reaches 8
  runs. That is 60 runs. The pilot cost $0.14 a run and $0.015 a fork, a quarter of the budget above.
- **Forks:** 5 samples per reply at a run's first stop.
- **The oracle:**
  - **When it judges:** at the stop's own time, plus the source run's latency to its next turn, plus the fork's time
    before its action. It no longer uses the fork's own clock.
  - **Pointer:** it starts from the stopped call's last `move_at`.
  - **Uids:** it checks `fill_form`'s element uids.
  - **helpdesk:** it counts only select changes.
  - **checkout:** a field filled with the value it already holds triggers nothing.
  - **export:** a re-wait is the correct move; an answer without the figure fails.
  - **deck:** a uid click is a misclick only when its target's centre lies under the popover.
  - **consent:** the frame reports its buttons' rects once laid out.
  - **New classes:** actions it cannot judge are "unjudged", and a look the fork could not serve (past the hook's 3,
    or a screenshot at a stop that saved none) is "look-cap". Both are left out of the failing share.
- **Agreement with real next actions** is counted over the full classes. Real misclicks are read from the app logs.
- **Kinds of stop:**
  - A refused press is a false refusal when browserd's refusal says it could not read the point (consent's frame).
    Otherwise it is a true refusal when the layout had an overlay or frame at the point, and a false refusal when it
    had neither.
  - A key-name error, or a wait for text that never showed, is a slip.
- **The deck** draws each line as one SVG `text` element, as Slides does, so a press naming several words of a line
  passes.
- **The cost model** pools the failing share per kind and reply over stops and uses it linearly:
  `p·(C·r + o + F·(w + T·r))`, with F that reply's own measured size.
- **Decision:**
  - **Eligibility:** a reply is eligible when its stop-paired failing share minus keep's is under 15 points at the
    interval's top.
  - **Winner:** the cheapest eligible reply that saves at least max($0.01, 10% of keep's modelled cost per stop) with
    its interval below zero. Among replies within that margin of the cheapest, the order note, short, shot decides.
  - **Intervals:** from resampling runs within each scenario.
- **Harness:**
  - The stub voids a fork it cannot render.
  - Screenshot looks are served only when the stop saved screenshots. Those screenshots are taken only before 35s
    into a queue.
  - The watcher logs failures to `forks-failed.jsonl` and tries each run up to 3 times.
  - Replies are rebuilt with the frozen keep arm's checkout, `browserd-pn-c`. Its diff hash, and the hashes of the
    harness and the apps, are in `harness.json`.

### Threats

- **The arms before the stop:** they share a description, neutral names and a random letter. Measures up to the
  first stop are reported by arm.
- **A fork stops at the first action:** a reply that shows part of the page can let the agent act on what it sees
  and look later. The oracle flags actions that leave the stopped queue's targets undone, and Part 1's recovery
  window (to the next stop or 5 turns) is reported by arm.
- **Replica fidelity:**
  - deck's sizes are checked against a real Slides snapshot from slides-pilot1;
  - consent copies BBC's measured mechanism;
  - each scenario's page-now size is reported.
- **No Read tool:** note's path is useless to these agents. With Read, an agent might read the whole saved snapshot,
  which is no cheaper than a take_snapshot. The write-up says so.

## How review hardened the plan

Joshua asked for the plan to be attacked before it ran: at most three rounds, each with three adversarial reviewers
(statistics and measures, scenario realism, harness and mechanics) who read the plan and the code. Round 3 also read
the pilot's data. Each round's main findings, and what changed:

### Revision 1, before round 1

- **Design:** whole runs of four scenarios (helpdesk, checkout, export, and a kanban `board` with a modal over its
  cards) on three arms, k = 6.
- **Arms:** named for their reply; each arm's queue description said what its stop reply held.
- **Measures:** cost after the first stop was primary. A projection of turns after the stop to the trip deck's scale
  was the fallback.
- **Guard:** an arm 3 passes in 24 behind was dropped.

### Round 1: whole runs cannot answer it

- **No power:**
  - Cost after a stop has an SD of about $0.08 a run (the trap1 transcripts), against a likely effect of $0.03 or less.
  - Separating the arms would take 90 to 200 runs each.
  - Changed: forks at real stops became the primary measure (Part 2); whole runs stayed as a check.
- **The projection was incomplete:**
  - It left out the 1-hour cache write ($4/M, as much as 20 turns of reading) and what a look brings back.
  - Changed: the cost model above.
- **Output tokens in stream-json's assistant events are placeholders** (145 against 798 in one run).
  - Changed: `--include-partial-messages`, with usage read from `message_delta` and checked against `modelUsage`.
- **The arms differed before any stop:** their descriptions named their reply, and arm names (PnNote) reached the
  model.
  - Changed: one description for all, neutral profile names, and arms assigned at random letters.
- **The scenarios decided the sign:**
  - board rarely stopped: drags go by uid, and `on: ""` is never refused.
  - checkout's trigger could be foreseen, and helpdesk raced the queue.
  - export could be answered from its table.
  - Changed:
    - board was dropped;
    - helpdesk re-renders after every save;
    - checkout rebuilds its rates on a postal-code change and its payment block on a shipping choice;
    - export takes 120s and serves its summary only once it is ready.
- **The harness:**
  - A rerun started from the old page state.
  - The main checkout's `node_modules` was empty.
  - `tools.py`'s "No page found" check and `run.py`'s `reached_browser` keyed on `isError`.
  - Stops were found by `startswith`.
  - The Claude Code version could drift mid-batch.
  - Changed: `/reset` per attempt, `npm ci` per worktree, `steps.STOPPED`, and a pinned Claude Code with
    `DISABLE_AUTOUPDATER`.

### Round 2: forks need an oracle, and a decision per kind

- **The recorded stop mix was 86 to 95% press refusals,** from suites built to provoke them, so a weighted winner
  would have been the deck's winner.
  - Changed: decide per kind of stop, and show savings across uid shares of 10, 30 and 50%.
- **"Acts on uids the page still has" is not "would work":**
  - helpdesk's second change goes stale anyway;
  - a press works or not by what is at its point.
  - Changed: a per-scenario oracle, validated against Part 1's real next actions at 90%.
- **The deck was not Slides:**
  - its slide text was canvas, where real Slides' is SVG, which `hit.py` reads;
  - its `showModal` made the page inert and hid it from the snapshot;
  - the agent's own Escape closed its popover.
  - Changed:
    - SVG text and a non-inert `role=dialog`;
    - the popover opens over the toolbar at the first `move_at` 1s after the first edit;
    - the consent scenario was added: a frame from another site on a 9k-character article (BBC: refused 4 of 4).
- **mixed only duplicated keep's and note's samples.**
  - Changed: it is computed from them, and its forks went to shot.
- **The forks:**
  - the forks of one stop shared a source file;
  - the stub failed open;
  - forks ran inside the batch with no limit;
  - the mechanism had been tried on Haiku only.
  - Changed:
    - a private cut copy per fork;
    - a stub and hook that fail closed;
    - a pool of their own after cleanup;
    - a Sonnet cache gate.
- **The decision point** paired the short tasks' context with T = 60 and had no margin.
  - Changed: C = 189k, T = 60, and a win needs at least $0.01 and 10%.

### Round 3, with the pilot's data: the oracle's clock

- **The oracle judged each fork by the fork's own clock,** minutes after its run, so every export wait and deck press
  looked fine.
  - Changed: the stop's time, plus the source run's latency, plus the fork's time before its action.
- **The cost model was not the plan's formula:**
  - it had no F;
  - p/(1−p) per stop turned one stop into −$0.36.
  - Changed: p pooled per kind and reply and used linearly, and F per reply.
- **k = 2 could decide only uid stops.**
  - Changed: k per scenario, so each kind reaches 8 runs (60 runs), and 5 samples per first stop.
- **The tie rule handed the win to keep** when note and short both beat it.
  - Changed: the cheapest eligible reply wins, and note, short, shot break ties.
- **The oracle's validation flattered it:**
  - agreement only compared "stopped again or not";
  - actions it could not judge counted as ok;
  - its deck rule punished keep's correct dismissal by uid;
  - per-word SVG text made false refusals that Slides does not.
  - Changed:
    - agreement over the full classes, with real misclicks read from the app logs;
    - the classes unjudged and look-cap;
    - one `<text>` per line;
    - refusals classed true or false from the layout.
- **The harness:**
  - a stub crash did not void its fork;
  - one error killed the watcher;
  - replies were rebuilt from a live checkout.
  - Changed:
    - `stubfail.txt` voids the fork;
    - failures go to `forks-failed.jsonl` and are retried;
    - replies are rebuilt from the frozen pn-c checkout, with hashes in `harness.json`.

## The pilot (pagenow-pilot)

- **Size:** 15 runs, each scenario once on each arm, and 166 forks. They cost $2.07 and $2.41.
- **Stops:**
  - Every scenario stopped at its designed trigger in at least 2 of 3 runs; deck was 2 of 3.
  - checkout's country trigger never fired: agents set Country alone, then looked. Its postal-code trigger stopped
    3 of 3 runs, and its shipping trigger 2 of 3.
- **Images:**
  - No stopped queue in the pilot or the batch had taken a screenshot before its failed step, so the runs never
    showed a stop's images reaching the agent.
  - The forks did: every shot fork checked (12 deck forks of the batch) got its screenshot in the stop's result.
- **Prices:** per-message prices matched `modelUsage` to the cent.
- **Forks:**
  - every fork's first request read at least its source turn's cached prefix;
  - all 166 ended in a deferred call.
- **Not results:** pilot runs fed round 3 only.

## The batch (pagenow-1)

### Part 1: runs

60 runs, all passed. By arm (descriptive, as planned):

| Arm | Reply | Passed | Cost | Turns | Seconds | Before the first stop |
|---|---|---|---|---|---|---|
| pn-a | note | 20/20 | $0.123 | 11.2 | 62 | $0.059, 5.4 turns (18 runs stopped) |
| pn-b | short | 20/20 | $0.136 | 11.7 | 59 | $0.052, 4.8 turns (19 runs stopped) |
| pn-c | keep | 20/20 | $0.147 | 11.8 | 68 | $0.051, 4.9 turns (17 runs stopped) |

- **Cost:** note's arm cost 16% less than keep's over whole runs.
- **Before the first stop:** note's arm spent a little more than keep's, so the saving does not come from the runs'
  starts.
- **Deck:** it stopped in 4, 5 and 3 runs of 6 on pn-a, pn-b and pn-c.

### The oracle against the real next actions

The oracle was checked against the real next action after each run's first stop, over its full classes:
- checkout 9 of 9, consent 12 of 12, export 12 of 12, helpdesk 9 of 9;
- deck 6 of 8, under the 90% the plan requires.

### The oracle's popover bug, fixed after the batch

- **Both deck disagreements** (pn-a deck-r1 and pn-b deck-r5) were the same queue: press "Not now" on the popover,
  then press the toolbar control again.
  - The real queues worked, but the oracle said they failed. It judged every press against the layout at the stop,
    with the popover still open.
  - A press on either popover button closes it (`../bench/pagenow/deck.html`).
- **The fix:** once one of the popover's buttons is pressed, by point or by uid, the oracle drops the popover for the
  queue's later steps. Deck then agrees 7 of 7, with 5 unjudged.
- **What it changed:**
  - Every true-refusal fork the oracle had called "fails" was this queue.
  - Before the fix, the rule kept keep's reply for true refusals: note, short and shot failed 23, 36 and 12 points
    more often than keep.
  - After the fix, no reply fails a judged action, and shot wins.
  - This decision was made after seeing the data, so it carries less weight than the others.
- **Unjudged actions:** 66 of deck's 81 are queues with a `press_key`; most of the rest hold a double-click. The
  oracle models neither.
- **A sensitivity check** judged keyboard steps as harmless. No decision changed, and deck agreed 10 of 11.

### Part 2: forks

1,052 forks, none void, none failed. None missed the cache, and each reply's samples at a stop sent the same first
request; that check replaced the plan's cache gate, which would have voided a fork run after its cache expired. Per
kind of stop and reply:
- **reply:** the reply's own tokens;
- **looks:** look-only calls per fork before its first action;
- **failing:** judged first actions that would fail. Unjudged actions are left out.

| Kind (runs) | Reply | Reply tokens | Looks | Failing | Against keep, per stop [90%] |
|---|---|---|---|---|---|
| uid (19) | keep | 3,676 | 0.24 | 35 of 90 | |
| | short | 1,396 | 0.82 | 28 of 90 | −$0.012 [−0.027, +0.004] |
| | **note** | 354 | 0.98 | 30 of 90 | **−$0.029 [−0.040, −0.018]** |
| | shot | 1,169 | 0.95 | 44 of 91 | −$0.008 [−0.018, +0.002]; fails 9 points more (top 20): not eligible |
| wait (12) | keep | 2,653 | 0 | 0 of 48 | |
| | short | 1,235 | 0 | 0 of 48 | −$0.023 |
| | **note** | 184 | 0 | 0 of 48 | **−$0.040** |
| false refusal (13) | keep | 3,811 | 0 | 0 of 65 | |
| | short | 1,264 | 0 | 0 of 64 | −$0.041 |
| | **note** | 285 | 0 | 0 of 65 | **−$0.056** |
| | shot | 1,097 | 0 | 0 of 65 | −$0.043 |
| true refusal (10) | keep | 3,044 | 0.37 | 0 of 34 | |
| | short | 1,331 | 1.00 | 0 of 17 | +$0.031 [+0.021, +0.040] |
| | note | 317 | 1.00 | 0 of 21 | +$0.013 [+0.002, +0.023] |
| | **shot** | 1,134 | 0.00 | 0 of 31 | **−$0.053 [−0.063, −0.043]** |
| slip (4) | keep | 2,818 | 0 | 0 of 15 | |
| | short | 1,386 | 0 | 0 of 15 | −$0.024 |
| | note | 338 | 0.94 | 0 of 15 | +$0.002 |
| | shot | 1,156 | 0 | 0 of 15 | −$0.027 |

- **Where no interval is given,** it is within $0.001: no fork failed, and looks hardly varied from stop to stop.
- **Wait stops have no shot:** they came after 35s into a queue, when no screenshot is taken.
- **Stops of the kind "other"** came from one run, so they are not decided.
- **On uid stops, shot's agents failed more often** (44 of 91, against keep's 35 of 90) though they looked
  as often as note's.
- **mixed** (keep's reply when the failed step named a uid, note's otherwise) never beats note: on uid stops it is
  keep's reply, which note beats, and on the other kinds it is note's.

The chosen reply against keep at other operating points (the plan's decision is at T = 60):

| Kind | Reply | At the stop's own context | T = 15 | T = 60 | T = 120 |
|---|---|---|---|---|---|
| uid | note | −$0.052 | +$0.003 | −$0.029 | −$0.071 |
| wait | note | −$0.040 | −$0.017 | −$0.040 | −$0.069 |
| false refusal | note | −$0.056 | −$0.025 | −$0.056 | −$0.099 |
| true refusal | shot | −$0.041 | −$0.031 | −$0.053 | −$0.082 |

note on uid stops is even with keep in a short task (T = 15). It pulls ahead as the task gets longer, since keep's
view is carried on every later turn.

### Time

- **What was measured:** each fork's turns. A fork's time to its first action differs between replies by its looks,
  each one a turn of 2.0 to 2.5s, plus a turn for each action that fails.
- **uid:**
  - note's agents took 0.74 more looks per stop than keep's: about 1.5s slower, less a little for failing 6 points
    less often;
  - short's took 0.58 more: about 1.3s slower.
- **true refusal:**
  - shot's agents took 0.37 fewer looks than keep's: about 0.9s faster;
  - note's took 0.63 more: about 1.4s slower.
- **wait, false refusal:** no reply led to a look, so no difference was measured. Reading a longer reply was not timed
  apart; the difference is a few thousand tokens of prefill.
- **Whole runs (descriptive):** note 62s, short 59s, keep 68s.

### Spend

- **pilot:** $2.07 of runs and $2.41 of forks;
- **batch:** $8.13 of runs and $13.89 of forks;
- **in all:** about $26.50, against $66 budgeted.

## Reading the numbers

- **Each "against keep" figure is per stop:** how much less the rest of the task costs when one stopped queue gets
  that reply instead of keep's.
- **It is modelled at the trip deck's scale,** not at these short tasks': 189k tokens of context and 60 turns left. It
  is built from what the forks measured:
  - **the reply's size:** a token in the reply is written to the cache once ($4/M), then read on every later turn
    ($0.20/M x 60);
  - **looks:** each is one more turn that reads the whole context (189k x $0.20/M, about 4¢) and writes what it
    brought back, which is then carried like the reply;
  - **failing actions:** each costs a turn plus another stop reply of that reply's own size.
- **Worked example, note on a uid stop** (rounded):
  - Its reply is 3,322 tokens smaller than keep's: −5.3¢.
  - Its agents look 0.74 more times: +2.9¢ for the turns, and +1.8¢ for the 1,117 tokens they bring back.
  - It fails a little less often, and its failures are cheaper, since their stop reply is note's too: −2.3¢.
  - In all: −2.9¢ a stop.
- **Per run,** the saving is the per-stop figure times the stops.
  - These runs had 0 to 3 stops each; 54 of 60 stopped.
  - In a long task with 5 stops it is roughly 15 to 25¢.
  - The whole-run means in Part 1 (note $0.123 against keep $0.147) are measured, not modelled, but too noisy to
    decide on.

## What this means for browserd

- **Already decided:** a stop is a normal result (branch `stop-replies` in `browserd-pn-keep`). It is not on main yet.
- **The page now, as decided:**
  - note for a gone uid, a wait that ran out, and a slip;
  - shot for a refused press.
  - Slips were not decided; note is even with keep there (+$0.002).
- **A simpler option:** note for every stop. It gives up about $0.066 a true refusal (note is +$0.013 against keep
  there) and saves $0.013 a false one.
- **shot's screenshot** must be skipped late in a queue, as the experiment's were after 35s: a capture the page does
  not answer can take 25s, and Claude Code drops a reply after about 60s.
- **Not measured:**
  - **The Read tool:** these agents had none, so note's saved path was useless to them. With Read, an agent may read
    the whole saved snapshot, which costs about what a take_snapshot does.
  - **The stop mix** of real tasks.
  - **Other models:** Sonnet 5.5 only.
  - **Real sites:** these are replicas, sized from real pages.
- **Planned but not reported:** savings across stop mixes, the recovery window after a stop, deck's size against a
  real Slides snapshot, and each scenario's page-now size. The keyboard sensitivity check ran from a scratch script
  that sets the oracle's `NEUTRAL` to include `press_key` and `type_text`; it is not in the committed code.

## Data and reproduction

- **Code:**
  - `../bench/pagenow.py`, the suite, and `../bench/pagenow/`, its apps;
  - `../bench/pnserver.py`, which serves them;
  - `../bench/pnbatch.py`, the batch;
  - `../bench/pnfork.py` with `pnhook.py` and `stubmcp.py`, the forks;
  - `../bench/tools/pnreport.py`, the report;
  - `../bench/run.py`'s arms pn-a, pn-b and pn-c.
- **Arms:**
  - the worktrees `browserd-pn-a`, `-b` and `-c`, at e28fadd with the patches in the data folder's `patches/`
    (`stop-replies.diff`, `arm-*.diff`);
  - `arms.json` says a = note, b = short, c = keep.
- **Data:** `browserd-pn-data` beside the repo, given as `BROWSERD_BENCH_DATA`:
  - `results/pagenow-pilot/` and `results/pagenow-1/`: transcripts, and `harness.json`, the hashes at batch time. The
    code was tidied after the batch in review, with the report's numbers unchanged, so those hashes are of the code
    as it ran, not as committed.
  - `forks/pagenow-1/<arm>/<run>/stop<k>/<reply>-s<n>/`: each fork.
  - `sessions/pagenow-1/`: the runs' Claude Code sessions.
- **The report:** `BROWSERD_BENCH_DATA=<data> python3 experiments/bench/tools/pnreport.py pagenow-1 [--json out.json]`.
- **A rerun:**
  1. `BROWSERD_BENCH_CLAUDE` set to the pinned Claude Code 2.1.289;
  2. `pnserver.py`, and `nextserver.py` for each arm on 9310, 9320 and 9330;
  3. `pnfork.py capture`;
  4. `pnfork.py watch <exp>` beside `pnbatch.py <exp>`.
