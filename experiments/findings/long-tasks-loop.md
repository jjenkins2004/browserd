# Findings: the overnight loop, browserd against Playwright MCP on the long tasks

Run the night of 2026-10-04/05 on Windows with `../long-tasks/compare.py`. A loop ran both long tasks on browserd,
changed one thing in browserd, had it reviewed and committed, and ran both again, against a Playwright MCP baseline on
the same Chrome. It follows prelim3 (`trip-compare.md`), where Playwright won the trip deck. prelim4 is one browserd
trip run made just before the loop's first change, its baseline ($3.76, 123 turns).

## Result

Means over the night's runs. browserd's runs span five commits on trip and four on capex (Iterations); Playwright's
are night-pw-trip r1–r3 and night-pw-capex r2–r4.

| task | arm (runs) | $ | minutes | API turns | correct / checks | rubric-adjusted | looks |
|---|---|---|---|---|---|---|---|
| trip | browserd (7) | 3.10 | 12.9 | 94 | 11.1/13 (86%) | 12.7/13 (98%) | 6.3 |
| trip | Playwright (3) | 3.74 | 15.2 | 110 | 12.7/13 (97%) | 13/13 (100%) | 7.0 |
| capex | browserd (6) | 0.55 | 3.7 | 36 | 33/33 | | |
| capex | Playwright (3) | 0.65 | 4.2 | 38 | 33/33 | | |

**browserd's means are cheaper and faster on both tasks: 17% cheaper and 15% faster on trip, 16% cheaper and 11%
faster on capex** (from the means before rounding). Capex quality is tied at 33/33. Trip correctness is 98% against
100% once two rubric issues are set aside; the misses left are one Denver hotel (bd1, bd5) read from a map pin's
card, which bd1's second judging passed (Quality). Trip looks are 6.3 against 7.0, traced to the agents' design
choices on too few decks; browserd's part in them is unproven.

Every run ("rubric-adjusted" applies the two fixes in Quality; polish was 5/5 in every trip run):

| trip run | commit | correct | rubric-adjusted | looks | $ | minutes | API turns |
|---|---|---|---|---|---|---|---|
| prelim5 | a3e0a47 | 13/13 | 13/13 | 7 | 3.21 | 10.1 | 86 |
| night-bd1-trip | a1476fd | 10/13 | 12/13 | 6 | 3.53 | 16.6 | 116 |
| night-bd2-trip | a1476fd | 11/13 | 13/13 | 6 | 3.95 | 13.4 | 102 |
| night-bd3-trip | c731a57 | 12/13 | 13/13 | 6 | 3.48 | 15.5 | 107 |
| night-bd4-trip | 439571c | 10/13 | 13/13 | 7 | 2.44 | 11.9 | 74 |
| night-bd5-trip | 50e461f | 10/13 | 12/13 | 6 | 2.82 | 14.2 | 98 |
| night-bd6-trip | 50e461f | 12/13 | 13/13 | 6 | 2.26 | 8.5 | 74 |
| night-pw-trip r1 | | 13/13 | 13/13 | 7 | 2.83 | 12.9 | 86 |
| night-pw-trip r2 | | 13/13 | 13/13 | 7 | 4.70 | 16.1 | 128 |
| night-pw-trip r3 | | 12/13 | 13/13 | 7 | 3.71 | 16.5 | 115 |

| capex run | commit | checks | $ | minutes | API turns |
|---|---|---|---|---|---|
| night-bd1-capex | a1476fd | 33/33 (30/33 as first graded) | 0.64 | 3.7 | 42 |
| night-bd2-capex | a1476fd | 33/33 (30/33 as first graded) | 0.46 | 3.9 | 31 |
| night-bd3-capex | c731a57 | 33/33 | 0.66 | 4.5 | 42 |
| night-bd4-capex | 439571c | 33/33 | 0.43 | 3.0 | 30 |
| night-bd5-capex | 50e461f | 33/33 | 0.55 | 4.0 | 36 |
| night-bd6-capex | 50e461f | 33/33 | 0.54 | 3.4 | 36 |
| night-pw-capex r2–r4 | | 33/33 (30/33 as first graded) | 0.64, 0.62, 0.70 | 3.6, 4.5, 4.5 | 39, 35, 40 |

"As first graded" is before 4b03bde. Playwright's capex r1 is left out: it ran at the same time as browserd-66's capex
run (Harness, "The Chrome lock").

## Quality

### Trip correctness: two rubric issues

| issue | runs it failed | what happened | fix |
|---|---|---|---|
| Denver's November high | bd1, bd2, bd3, bd4, bd5, bd6, Playwright r3 | the agent read Google's "Weather averages" card, 57°F, against the rubric's 52.9 ± 3°F. Runs that read WeatherSpark (55°F), currentresults.com (55°F) or a search snippet (53°F) passed | settle the source: name one in the prompt, or accept Google's card |
| fares | bd1 Seattle, bd2 Denver, bd4 Seattle and Denver, bd5 Denver | seen.txt holds the agent's own Google Flights text for its fare ("Cheapest from 114 US dollars"), but both harness scrapes saw the other fare level Google Flights switches between ($127 for $114; $103/$103/$163 for $96/$96/$152). Playwright r2 and r3 were saved only by their before-scrapes | accept a fare seen.txt shows on Google Flights |

The trip rubric was left alone: browserd-66 is rewriting trip on its own branch. The two misses left after both fixes
are bd1's and bd5's Denver hotel, Comfort Suites Highlands Ranch at $74–75, read from a map pin's card in a screenshot.
seen.txt holds only the pin's label, so the first judgings failed it as unverified (bd1) and "likely an ad" (bd5);
bd1's second judging, reading Google Hotels itself, passed it. bd5's saved page snapshot shows a 4-star hotel rated
4.2, not sponsored, but in a suburb outside the filtered list, whose cheapest was $77.

### Trip looks: the agents' design choices

The first nine decks were judged a second time (`rejudge/looks1`): every deck got the same looks, so the judge is
steady over 18 judgings, and what varies is the decks. bd6's deck, made later, was judged once. Photo share is the
city photo's share of the slide's area, from `deck.pdf`; slide width is the slide's mean width in the deck phase's
screenshots, as the agent got them.

| deck | looks (two judgings) | titles | city photo | slide width seen (px) | screenshots at scale ≤ 0.5 |
|---|---|---|---|---|---|
| prelim5 | 7, 7 | Streamline | 17% | 617 | 3 of 49 |
| bd4 | 7, 7 | Streamline | 18–21% | 639 | 2 of 43 |
| Playwright r1 | 7, 7 | Streamline | 16% | 892 | 0 of 41 |
| Playwright r2 | 7, 7 | Simple Light, bold navy by hand | 12% | 1,302 | 0 of 64 |
| Playwright r3 | 7, 7 | Simple Light, bold navy by hand | 19% | 1,237 | 0 of 52 |
| bd2 | 6, 6 | Streamline | 9% | 599 | 5 of 53 |
| bd1 | 6, 6 | Simple Light, bold teal by hand | 10–11% | 529 | 35 of 70 |
| bd5 | 6, 6 | Simple Light, default regular black | 12–18% | 521 | 40 of 48 |
| bd3 | 6, 6 | Simple Light, default regular black | 5.5% | 497 | 61 of 69 |
| bd6 | 6 (judged once) | Simple Light, default regular black | 18% | 530 | 39 of 48 |

- **Styled titles and a photo of at least 12% of the slide separate the 7s from the 6s.** Every 7 has both; every 6
  misses at least one (bd3 both). Two pairs isolate them: bd4 (7) and bd2 (6) are the same Streamline deck with
  photos of 18–21% against 9%, and Playwright r2 (7) against bd5 and bd6 (6) are all Simple Light, bd5's and bd6's
  photos at least as large (12–18% and 18% against 12%), r2's titles bold navy and theirs left at the default. Gray
  body text (9 of 10 decks) and a text-only recommendation slide (all 10) separate nothing.
- **Both arms make these choices.** Where a browserd agent laid the deck out as Playwright r1 did, it scored the same 7
  (prelim5, bd4). The 6s' flaws were in plain view: bd3 set its photo to 2 in wide, and bd5 saw each finished city
  slide at full scale, the judge's view.
- **What differs by arm is how large the agents saw their slides,** since browserd's help steers screenshots to scale
  0.5. Slide width seen puts all five 6s below all five 7s, but within browserd the split is 599 px against 617, and
  prelim5 and bd4 scored 7 looking mostly at 0.6–0.7. Low scale marks a frugal run, not a cause these runs show.
- **Between the arms the gap falls short of significance:** browserd has 2 sevens in 7 decks and Playwright 3 in 3. If
  looks were independent of the arm, all three Playwright decks would be among the five 7s with probability
  10/120 = 0.08.

## What was run

- **Tasks:** trip (`../long-tasks/trip/prompt.md`: flights, hotels and November highs for three cities, then a
  6-slide Slides deck built by hand) and capex (`../long-tasks/capex/prompt.md`: four 10-Ks, then a Sheet with a chart
  and a 6-slide deck).
- **Arms:** browserd, its 9230 server restarted onto each commit under test, on the `experiments` profile's Chrome; and
  Playwright MCP 0.0.82 attached over DevTools to that same Chrome.
- **Agent:** claude-sonnet-5-5, a clean headless Claude Code pinned at 2.1.289, with the arm's server alone.
- **Grading:** trip by claude-opus-5-5 through `../long-tasks/judge.py` (13 correct items, 5 polish, looks 1–10);
  capex in code by `grade.grade_capex` (33 checks).
- **Runs:** one per task per iteration, plus night-bd6, a second sample at 50e461f; one at a time. The Chrome was
  shared with browserd-66, another Claude Code session, which ran longer variants of the tasks (`longer-*`); from 01:26
  a lock kept the two sessions' `compare.py` runs apart (Harness). It did not cover that session's probes: one, "probe
  maps pin photo", started at 01:48 during bd1-trip.
- **Turns** are API turns (unique assistant message ids), not `compare.py report`'s `num_turns`.

## The loop's rule for a change

- **General:** it fixes a pattern seen on both tasks, or across several sites or widgets, not one site's quirk.
- **Extend before adding:** an argument on an existing step before a new step, a step before a tool. No tool or step
  was added all night; STEPS_HELP grew from 4,149 to 5,315 characters (about 290 tokens).
- **Keep the safety checks:** `on` checks on presses stay, and every other tie between fits still fails.
- **Measured** by the mechanism it targets (seconds per named step, turns in the chain it aims at) as well as by the
  totals, since one run per iteration is noisy.

Each change was built in its own worktree, reviewed by review-bugs, review-design and review-prose (nits triaged),
then committed to main.

## Iterations

Each commit's message says what it changed and the evidence that prompted it: `git show <commit>`.

### a3e0a47 (0): click, fill, hover and upload_file take a name instead of a uid (kept)

- **How general:** any menu item or dialog field an earlier step opens, on any site.
- **Review caught:** `hit.fit` ranked "Comment" equal to "Comments", so exact names tied; polling stopped at the first
  weak fit, so timing, not rank, picked a toolbar button over a menu item still opening.
- **Measured** (prelim5 against prelim4): deck turns 83→64, the run's cost to $3.21 and its turns to 86, and Google
  Hotels' turns 29→13; 51 of 54 named clicks ran.

### a1476fd (1): a name prefers an open menu or dialog; a double click's later press takes `on: ""` (kept, neutral)

- **How general:** any popup open over a page, on any site.
- **Review caught:** after a modal closes, chrome-devtools-mcp renumbers the page, so the page's own menus counted as
  popups (confirmed live); the fix then let page text shaped like `uid=1000(josh)` crash every name step.
- **Measured** (bd1, bd2): one press changed, correctly (the menu's "Text box t"); the `on: ""` part was never used in
  bd1, and in bd2 it let 11 second presses of double clicks run.

### b1eacb3 (2): Tab in type_text, common key names, a combobox gives way to its text box (kept; help fixed in 439571c)

- **How general:** tab-separated typing was seen on trip (prelim5, then 50 single Tab presses) and on
  longer-capex-bd1 (110), not on the night's capex runs, which pasted; key names (Down, Esc, Del, Return, Ctrl, Cmd)
  on any site; the combobox rule is Slides' "Font size", a combobox wrapping a same-named text box.
- **Review caught:** the nesting rule made a native select ("Month" holding option "Month") act on its placeholder
  option and reset it; it was narrowed to a combobox holding a textbox or searchbox.
- **Measured** (bd3, with c731a57): 5 presses of Down and Shift+Down ran, a key name bd1 had refused; tab typing
  landed 2 of 6 times on trip (Tab adds to a filled cell, a line break stays inside a Slides cell: about 7 turns of
  rework); on capex the new help steered the agent from paste to typing, and its rows ran along row 2 (about 3 turns,
  30 s); the combobox rule never fired.

### c731a57 (3): a name that cannot resolve fails fast and says why, and may fit a text box's value (kept)

- **How general:** both tasks. Name misses waited 5 s each (47 s in bd1-trip); a disabled control (capex's Import,
  trip's INSERT IMAGE) waited out chrome-devtools-mcp's 5 s and now fails, saying so, once the page is still; capex's
  agent never saw a Sheets alertdialog (about 4 turns); the editors' titles, "Untitled spreadsheet" and "Untitled
  presentation", are the value of textbox "Rename".
- **Review caught:** the stop rule asked whether any usable control fit, not the best: a disabled "Save" beside an
  enabled "Save as copy" failed at 0.0 s, and a disabled "Import" beside "Help on Import" still waited 5.2 s. A
  stillness window of one 0.4 s poll would fail a server-opened dialog or a debounced button, so it became
  `NAME_STILL`, 1 s.
- **Measured** (bd3): misses failed in 1.2–1.4 s (7 s saved on trip, 11 s on capex); the agent used the popup note's
  uid the next turn 3 of 3 times on capex; Import refused in 1.3 s, and the linked chart took 6 turns against 11; 6
  value fits, all right; no wrong press.

### 439571c (4): type_text's help on tabs and a sheet's rows; a partial fit taken once the page is still (kept)

- **How general:** the help covers any table or sheet; the partial fits are names read off a screenshot's toolbar
  labels ("Layout" for Apply layout, "Theme", "Background"), which waited the full 5 s, 33 s on bd3-trip.
- **Review caught:** no bug; prose made the help's "tab" unambiguous (elsewhere it means a browser tab). Accepted risk:
  a better popup fit opening 1–5 s after the page went still loses to the page's partial fit.
- **Measured** (bd4): partial fits took 1.5–2.4 s against 5.3–5.7 s, with no wrong press; capex went back to paste
  (the first missed: no click on the cell, 2 turns); tab typing landed on new tables and failed once on filled cells
  (1 turn against about 7). The run's low totals are mostly the agent's route.

### 50e461f (5): views cut urls over 300 characters; `on` may name what a text box shows (kept)

- **How general:** the cut is site-agnostic but pays mainly on Google travel and search pages: about 1% of a trip run,
  up to 5% on a hotel-heavy one. The `on` rule had one case: bd1-capex's `on: "Untitled spreadsheet"`, refused, about 2
  of the 3 turns that title cost.
- **Review caught:** `on` would have passed on any word of an editor's whole text (`on: "Bold"` on a paragraph inside
  textbox "Body"); the fix to that then let a whitespace-only `on` pass anywhere, cross-site frames included.
- **Measured** (bd5, bd6): in bd5 the cut never fired, as no view held a url over 300 characters. In bd6 it cut 109 of
  the 133 urls in trip's views, and the deck began at 63k of context, the night's second lowest; the characters it
  saved were not measured. On earlier transcripts the cut would have removed 33.6k characters from prelim5's views and
  25.8k from bd2's, the two runs whose decks began above 90k of context. No worded press in either run passed by a
  text box's value.

The loop stopped changing browserd after 50e461f. The waste left was about 5 of 74 turns on trip and 6 of 30 on capex
(bd4), spread over different agent mistakes, and the best candidate left would save about a turn a run, well under the
spread between runs of one commit.

## Rejected ideas

| idea | why not |
|---|---|
| A stopped queue still takes the screenshot or snapshot it ends with | 44 stops skipped one, and 15 were followed by a turn that only looked (over the 9 browserd runs to bd4: 2.2 a run on trip, 1.0 on capex). But pagenow-1 (`page-now.md`) found a note cheaper than a look for uid and wait stops, and the look would also add an image after the other 29 stops: about $0.01 a stop net |
| take_screenshot after a press waits until the page is still | it would have saved 2 capex turns ($0.03) on pickers shot while blank, but adds tool time to every screenshot of every task |
| `find` shows the lines after each match | capex's prompt allows scripts, and its agents read statements with evaluate_script: no find-then-reread happened. It pays only when scripts are banned (up to 14 turns in longer-capex-bd1) |
| A refused press says where its words are now | about 0 turns saveable over 72 refusals (these runs, the longer tasks, the bench, pagenow): the words were unreadable (canvas text, frames) or covered at the same point, and the refusal's screenshot handled the few that moved |
| Refuse a made-up step only before a queue's last step, and report a last one as not run | 13 queues in 10 of the 13 browserd runs ended in a made-up step, 12 of them named `*_placeholder*`. It is the agent's invention, worth about $0.03 a run |
| STEPS_HELP: take screenshots at no scale "to judge how something you made looks" | unproven (Quality). One full-scale screenshot per finished slide costs about $0.04 a trip run, but full scale for every screenshot while building costs $0.3–1.0, as much as browserd's lead |

## Caveats and next steps

- **One run per task per iteration.** Runs of one commit differ widely (bd1 and bd2 at a1476fd: $3.53 and $3.95, and
  capex sheets of 19 and 10 turns), and Playwright's trip runs ranged from $2.83 to $4.70, about ±$1. Each verdict rests
  on its mechanism, not its totals, and the browserd means mix five commits on trip and four on capex.
- **Looks compared 7 browserd decks with 3, one of them judged once.**
- **Next:**
  1. The trip rubric: accept a fare seen.txt shows on Google Flights, and settle the weather source, in browserd-66's
     rewrite of trip.
  2. More runs at HEAD, 50e461f (night-bd6 was the first), so the means rest on one commit.
  3. If looks matter, A/B the screenshot clause in Rejected ideas over 3–5 trip runs, or judge 5 or more decks per arm.
  4. browserd's viewport is square, 1249×1243, so a Slides slide fills 29% of a screenshot, against 60% in Playwright
     r2's 1700×1000 window: a landscape viewport would give about twice the slide pixels per image token. It changes
     every site's layout, so measure it first.

## Harness

- **capex in compare.py (164c9f0):** capex runs on every arm, its prompt made neutral for the others by `SWAPS`
  (`../long-tasks/README.md`). Review caught that a failing check's line holding a character outside cp1252 would lose
  a run its score on Windows; stdout now escapes such characters.
- **The grader's shared formulas (4b03bde):** "growth is a formula" failed for GOOGL, AMZN and META in every run of both
  arms, as an `.xlsx` may write a column of same-shaped formulas as one shared formula whose text only its first cell
  holds; that Sheets' export does so is read from the grades (`../long-tasks/README.md`, Agent Gotchas). Design review
  cut the first fix, which rebuilt each cell's formula text in about 35 lines, to recording whether a cell has an
  `<f>`. The runs graded before it lost exactly those three checks, so they count as 33/33.
- **The Chrome lock.** After browserd-66's capex run and Playwright's capex r1 ran at once at 01:26, the two sessions
  agreed on a lock directory, `.chrome-lock` in the data folder. The launcher took it, waited until no `compare.py run`
  had been seen for 3 minutes, restarted browserd and ran the batch. From night-bd3 on, it also left 90 s after each
  release (`.night-released`) so browserd-66 could take its turn.
- **The restart that silently failed.** The first launcher restarted the server with `cmd //c "browserd.cmd restart"`
  from Git Bash and did not check it. For night-bd2 nothing restarted, so bd2 ran a1476fd like bd1 rather than
  b1eacb3, which came out only after the run. The launcher then restarted through PowerShell, stopped if that failed,
  and logged the commit, and each later run's commit was checked against server.log's restart time (bd4's restart at
  04:35 came before 50e461f's commit at 04:44, so bd4 ran 439571c).
- **On Windows:** runs need `PYTHONUTF8=1`, since `compare.py`, `grade.py` and `judge.py` read `prompt.md`, `key.json`
  and `rubric.md` in the locale's encoding. `pdftoppm` is not installed, so every run's note says "no pictures" and the
  galleries have no slides; `deck.pdf` is kept and was measured directly.

## Data and reproduction

- **Runs:** `C:/Users/2004j/Desktop/browserd-long-tasks/` (the data folder beside the repo), one folder per experiment
  holding `<arm>-r<n>/` (transcript, `result.json`, `deck.pdf`, the recording) and, for trip, `judging/`, with
  `<exp>.log` beside it. The night's experiments are prelim4, prelim5, night-pw-trip, night-pw-capex,
  night-bd1-trip to night-bd5-trip, night-bd1-capex to night-bd5-capex, and `rejudge/looks1/` (the second judgings,
  with `rejudge-looks1.log`).
- **The journal,** `night/journal.md` in that folder, is the durable record: each iteration's tables, the chains turn
  by turn, every candidate and the looks analysis. The launchers (`batch.sh`, `batch2.sh`, `rejudge.sh` with
  `rejudge.py`) and the analysis scripts the journal names (`pw_metrics.py`, `capex_metrics.py`, `lat.py` and the
  rest) are copied, as they ran, to `night/scripts/`; paths inside them still point at the loop session's scratchpad.
- **Commands,** from the checkout the 9230 server runs, after restarting it onto the commit under test, with
  `PYTHONUTF8=1 BROWSERD_LONG_TASKS_PROFILE=experiments` and
  `BROWSERD_LONG_TASKS_CLAUDE=C:/Users/2004j/Desktop/browserd-pn-data/bin/claude.exe` (Claude Code 2.1.289), never
  two at once:

      python experiments/long-tasks/compare.py run night-bd5-trip --arms browserd --k 1
      python experiments/long-tasks/compare.py run night-bd5-capex --task capex --arms browserd --k 1
      python experiments/long-tasks/compare.py run night-pw-trip --arms playwright --k 3
      python experiments/long-tasks/compare.py run night-pw-capex --task capex --arms playwright --k 4
      python experiments/long-tasks/compare.py report <exp>
