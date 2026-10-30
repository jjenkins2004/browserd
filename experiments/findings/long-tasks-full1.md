# Findings: browserd against Playwright MCP, chrome-devtools-mcp and Claude in Chrome on the long tasks (full1)

Run 2026-10-05 on Windows with `../long-tasks/compare.py`: three long tasks on four arms, three runs each, 36 scored
runs of 10 to 67 minutes, $324.41 for the runs and $29.64 for trip's judge. It follows the overnight loop
([long-tasks-loop.md](long-tasks-loop.md)) on longer tasks, whose sizes came from the calibration runs at the end of
this file. The same four arms on the public benchmarks are in [own-profiles.md](own-profiles.md).

## Setup

- **Tasks,** `../long-tasks/<task>/prompt.md` at 93d0a51 (browserd's prompts in the runs differ from these only in the
  profile's name, and the other arms' swap browserd's sentence for a neutral one):
  - capex: ten companies' 10-Ks found on EDGAR, a Sheet with formulas, two pivot tables and their charts, and a 13-slide
    deck with the charts linked. Graded in code, 86 checks.
  - parks: a My Maps map of 28 national parks from a pinned Wikipedia revision, a layer and pin color per state, each
    pin titled, described and given a photo, and a driving route. Graded in code from the map's KML, 107 checks.
  - trip: a team offsite across six cities, with flights, hotel, GSA M&IE, weather and 7+ underrated dinner spots per
    city, one picked and backed by sources, written into one Google Doc. Graded by claude-opus-5-5 through
    `../long-tasks/judge.py`: 50 correct items, 6 polish items, looks 1 to 10.
- **Arms:**
  - browserd: the main server on 9230, started 05:03:47 on 50e461f, the last commit to change code in `browser/` before
    the batch; it batches steps in one `queue` call;
  - Playwright MCP 0.0.82 over `--cdp-endpoint`;
  - chrome-devtools-mcp 1.9.0 over `--browserUrl`;
  - Claude in Chrome (`claude --chrome`, with `../bench/allow.py` answering its site prompts), which batches with
    `browser_batch`.
- **Agent:** `claude-sonnet-5-5` on Claude Code 2.1.289, as all 36 transcripts' init events say; no user settings, the
  arm's tools alone.
- **Browser:** one task per profile, on the user's own Google account: lt-capex (9226), lt-parks (9227), lt-trip (9228).
  The three tasks ran at once from 13:01; within a task, runs went one at a time, with each repeat's arms in the reverse
  order of the last.
- **Harness changes during the batch:** 5a24238 (earlier runs' titled files go to the Trash before each run) came with
  parks' rerun at 15:30, e3f1005 (a parks map found in Drive if not linked; no scored run needed it) was committed at
  15:40 during it, and e895155 (the judge answers through `--json-schema`) came with trip's restart at 16:14. capex
  ended at 15:45 on the process that began at 13:01, so its earlier "Tech capex" files stayed in Drive; every capex run
  still made its own.

Minutes are `wall` in `result.json`. API turns are unique assistant message ids in the transcript, as in
`long-tasks-loop.md`; `result.json`'s `turns` is Claude Code's `num_turns`, here always the tool calls plus one. Tool
errors count failed tool results and browserd's stopped queues, so they include calls to tools an agent made up
(`screenshot_placeholder`) and say little about which arm's work went wrong.

## Results

| over the 9 runs an arm | browserd | Playwright | devtools | Claude in Chrome |
|---|---|---|---|---|
| passed | 8/9 | 7/9 | 9/9 | 8/9 |
| total time (min) | 192.6 | 315.4 | 285.3 | 278.1 |
| time against browserd | 1.00× | 1.64× | 1.48× | 1.44× |
| total cost ($) | 43.81 | 66.03 | 138.48 | 76.09 |
| cost against browserd | 1.00× | 1.51× | 3.16× | 1.74× |
| tool calls | 1107 | 2089 | 8320 | 3256 |
| API turns | 1115 | 1346 | 2256 | 1483 |

**Every arm finished every run, with none timed out. browserd was fastest on all three tasks' means and cheapest on
capex and trip, with parks about tied with Playwright ($4.43 and $4.50). It had no lead on correctness:** capex and
parks passed in all 24 runs, so they separate the arms only on time and cost, and on trip, the one task that separates
them, devtools alone passed all three runs.

### capex (86 checks)

| mean of 3 runs | passed | min | $ | API turns | tool calls | tool errors | cache-read | output |
|---|---|---|---|---|---|---|---|---|
| browserd | 3/3 | 10.3 | 2.52 | 95 | 94 | 3.3 | 7.67M | 38.3k |
| Playwright | 3/3 | 12.6 | 3.46 | 131 | 216 | 12.7 | 11.42M | 38.4k |
| devtools | 3/3 | 12.7 | 8.19 | 158 | 626 | 10.0 | 28.90M | 83.4k |
| Claude in Chrome | 3/3 | 18.9 | 3.81 | 104 | 275 | 5.0 | 11.21M | 69.7k |

| run | checks | min | $ | API turns | tool calls | tool errors | cache-read | output |
|---|---|---|---|---|---|---|---|---|
| browserd r1 | 86/86 | 9.8 | 2.35 | 99 | 98 | 3 | 7.12M | 35.3k |
| browserd r2 | 86/86 | 10.8 | 3.23 | 109 | 108 | 5 | 10.51M | 42.3k |
| browserd r3 | 86/86 | 10.3 | 1.98 | 78 | 77 | 2 | 5.38M | 37.4k |
| Playwright r1 | 86/86 | 12.0 | 3.70 | 123 | 271 | 18 | 11.90M | 43.8k |
| Playwright r2 | 86/86 | 12.1 | 2.90 | 103 | 181 | 7 | 8.86M | 35.0k |
| Playwright r3 | 86/86 | 13.6 | 3.79 | 168 | 197 | 13 | 13.50M | 36.5k |
| devtools r1 | 86/86 | 14.5 | 8.87 | 161 | 528 | 14 | 31.90M | 87.9k |
| devtools r2 | 86/86 | 11.9 | 7.69 | 167 | 688 | 9 | 26.79M | 82.8k |
| devtools r3 | 86/86 | 11.8 | 8.00 | 145 | 663 | 7 | 28.02M | 79.5k |
| Claude in Chrome r1 | 86/86 | 16.8 | 3.78 | 104 | 249 | 6 | 11.25M | 67.0k |
| Claude in Chrome r2 | 86/86 | 17.6 | 2.61 | 81 | 176 | 5 | 6.84M | 58.4k |
| Claude in Chrome r3 | 86/86 | 22.2 | 5.06 | 128 | 400 | 4 | 15.55M | 83.6k |

### parks (107 checks)

| mean of 3 runs | passed | min | $ | API turns | tool calls | tool errors | cache-read | output |
|---|---|---|---|---|---|---|---|---|
| browserd | 3/3 | 21.2 | 4.43 | 129 | 128 | 10.7 | 14.77M | 62.5k |
| Playwright | 3/3 | 27.5 | 4.50 | 105 | 177 | 15.7 | 13.64M | 44.2k |
| devtools | 3/3 | 32.3 | 15.57 | 234 | 761 | 10.0 | 59.48M | 126.5k |
| Claude in Chrome | 3/3 | 31.4 | 8.12 | 178 | 337 | 4.3 | 29.24M | 105.3k |

| run | checks | min | $ | API turns | tool calls | tool errors | cache-read | output |
|---|---|---|---|---|---|---|---|---|
| browserd r1 | 107/107 | 18.9 | 4.00 | 116 | 115 | 11 | 12.49M | 64.5k |
| browserd r2 | 107/107 | 20.7 | 3.82 | 110 | 110 | 9 | 11.96M | 57.9k |
| browserd r3 | 107/107 | 24.0 | 5.47 | 161 | 160 | 12 | 19.85M | 65.2k |
| Playwright r1 | 107/107 | 32.0 | 6.29 | 127 | 214 | 19 | 19.08M | 46.2k |
| Playwright r2 | 107/107 | 23.9 | 3.17 | 95 | 137 | 13 | 9.48M | 42.1k |
| Playwright r3 | 107/107 | 26.5 | 4.03 | 94 | 180 | 15 | 12.36M | 44.2k |
| devtools r1 | 107/107 | 22.5 | 8.33 | 146 | 643 | 9 | 27.79M | 114.4k |
| devtools r2 | 107/107 | 29.8 | 9.50 | 186 | 519 | 6 | 34.78M | 102.7k |
| devtools r3 | 107/107 | 44.6 | 28.87 | 370 | 1121 | 15 | 115.86M | 162.3k |
| Claude in Chrome r1 | 107/107 | 33.9 | 10.26 | 201 | 412 | 8 | 38.00M | 125.5k |
| Claude in Chrome r2 | 107/107 | 29.5 | 6.78 | 167 | 264 | 4 | 23.80M | 94.2k |
| Claude in Chrome r3 | 107/107 | 30.7 | 7.33 | 167 | 335 | 1 | 25.90M | 96.0k |

### trip (50 correct items, 6 polish items, looks 1 to 10)

`passed` means every correct item was right, so one wrong item fails a run.

| mean of 3 runs | passed | correct | polish | looks | min | $ | API turns | tool calls | tool errors | cache-read | output | judge $, total |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| browserd | 2/3 | 148/150 | 17/18 | 7.3 | 32.7 | 7.65 | 147 | 146 | 7.3 | 25.70M | 111.2k | 6.47 |
| Playwright | 1/3 | 136/150 | 18/18 | 7.3 | 65.1 | 14.05 | 212 | 303 | 16.3 | 47.66M | 145.9k | 7.06 |
| devtools | 3/3 | 150/150 | 18/18 | 7.7 | 50.1 | 22.41 | 360 | 1386 | 14.3 | 84.36M | 243.4k | 7.97 |
| Claude in Chrome | 2/3 | 149/150 | 18/18 | 7.3 | 42.5 | 13.43 | 212 | 473 | 6.0 | 49.46M | 164.0k | 8.13 |

| run | passed | correct | polish | looks | min | $ | API turns | tool calls | tool errors | cache-read | output | judge $ |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| browserd r1 | no | 48/50 | 5/6 | 6 | 38.4 | 8.73 | 159 | 158 | 9 | 30.08M | 124.5k | 2.30 |
| browserd r2 | yes | 50/50 | 6/6 | 8 | 27.8 | 6.40 | 142 | 141 | 6 | 20.67M | 103.5k | 1.89 |
| browserd r3 | yes | 50/50 | 6/6 | 8 | 32.1 | 7.83 | 141 | 140 | 7 | 26.35M | 105.5k | 2.28 |
| Playwright r1 | no | 46/50 | 6/6 | 7 | 61.7 | 11.02 | 191 | 264 | 18 | 39.69M | 124.8k | 2.43 |
| Playwright r2 | no | 40/50 | 6/6 | 7 | 67.2 | 15.98 | 229 | 334 | 14 | 54.64M | 138.7k | 2.59 |
| Playwright r3 | yes | 50/50 | 6/6 | 8 | 66.4 | 15.15 | 216 | 311 | 17 | 48.65M | 174.2k | 2.04 |
| devtools r1 | yes | 50/50 | 6/6 | 7 | 44.2 | 25.46 | 373 | 1490 | 12 | 100.02M | 262.8k | 3.99 |
| devtools r2 | yes | 50/50 | 6/6 | 8 | 60.6 | 20.11 | 350 | 1285 | 16 | 69.78M | 218.8k | 1.95 |
| devtools r3 | yes | 50/50 | 6/6 | 8 | 45.4 | 21.66 | 358 | 1383 | 15 | 83.27M | 248.5k | 2.03 |
| Claude in Chrome r1 | yes | 50/50 | 6/6 | 7 | 35.1 | 10.86 | 182 | 467 | 7 | 38.03M | 152.2k | 2.70 |
| Claude in Chrome r2 | yes | 50/50 | 6/6 | 8 | 46.4 | 13.15 | 209 | 392 | 3 | 47.80M | 164.3k | 2.66 |
| Claude in Chrome r3 | no | 49/50 | 6/6 | 7 | 46.0 | 16.27 | 244 | 561 | 8 | 62.57M | 175.6k | 2.77 |

The runs that lost items, from `judging/<id>/verdict.json`:

| run | lost | the judge's reason |
|---|---|---|
| browserd r1 | denver_weather | the Doc gives 55.9°F against the key's 52.9°F |
| | austin_backing | a Visit Austin listing page, not a food guide, and no TikTok |
| | polish: layout | seven blank pages between sections |
| Playwright r1 | boston_spots | most rows marked unverified, one with about 4.5k Yelp reviews against the 500 limit |
| | austin_backing, nashville_backing | the linked Reddit comments do not recommend the pick |
| | vetting | the guide and "another article" are from one outlet |
| Playwright r2 | nashville and boston: spots, dinner, backing (6 items) | no dinner sections, marked not completed |
| | boston_flights | a $377 fare listed where $375 ones were cheaper |
| | chicago_spots | 6 spots, not 7 |
| | chicago_backing, austin_backing | wrong branch and a directory listing; a comment that does not name the pick |
| Claude in Chrome r3 | vetting | the TikTok link has "Food" appended to its id, so it opens "Page not available" |

### How far the runs overlap

On time, browserd's slowest run beat every other arm's fastest on capex, and Playwright's and devtools' on trip. Claude
in Chrome's fastest trip (35.1 min) beat browserd's slowest (38.4), and on parks browserd's slowest (24.0) came after
Playwright's fastest (23.9) and devtools' (22.5). On cost, every devtools run cost more than every browserd run on every
task, as did Claude in Chrome's on parks and trip and Playwright's on trip. The ranges overlap on capex against
Playwright ($2.90 to $3.79 against browserd's $1.98 to $3.23) and Claude in Chrome ($2.61 to $5.06), and on parks
against Playwright ($3.17 to $6.29 against $3.82 to $5.47).

## What explains the differences

| task | arm | API turns | calls an API turn | cache-read an API turn | images | model (min) | outside the model (min) |
|---|---|---|---|---|---|---|---|
| capex | browserd | 95 | 1.0 | 80k | 49 | 4.8 | 5.5 |
| capex | Playwright | 131 | 1.6 | 87k | 43 | 6.2 | 6.3 |
| capex | devtools | 158 | 4.0 | 183k | 71 | 10.8 | 1.9 |
| capex | Claude in Chrome | 104 | 2.6 | 107k | 77 | 8.1 | 10.8 |
| parks | browserd | 129 | 1.0 | 114k | 88 | 8.2 | 13.0 |
| parks | Playwright | 105 | 1.7 | 129k | 58 | 6.3 | 21.1 |
| parks | devtools | 234 | 3.3 | 254k | 76 | 20.2 | 12.1 |
| parks | Claude in Chrome | 178 | 1.9 | 164k | 182 | 12.5 | 18.9 |
| trip | browserd | 147 | 1.0 | 174k | 81 | 13.0 | 19.8 |
| trip | Playwright | 212 | 1.4 | 225k | 89 | 20.3 | 44.8 |
| trip | devtools | 360 | 3.8 | 234k | 93 | 37.1 | 13.0 |
| trip | Claude in Chrome | 212 | 2.2 | 234k | 163 | 18.0 | 24.5 |

"Model" is `duration_api_ms` from the transcript's result event, and "outside the model" is `wall` less that: tool calls
and the harness. The calls' own intervals in the transcripts give the same figure for browserd and within 6 minutes for
the others.

- **Cost follows cache reads, which are API turns times context.** At `page-now.md`'s rates ($0.20/M for a read, $4/M
  for a 1-hour write, $10/M for output) reads, writes and output add up to each run's cost, within 0.1%, and reads are
  59% to 76% of an arm's mean. browserd had the fewest API turns on capex and trip and the least context per turn on all
  three tasks. On parks Playwright took fewer turns, 105 to browserd's 129, at more context a turn, 129k to 114k, so the
  two come out about even.
- **devtools has no batching.** It writes each action as its own tool call, 3.3 to 4.0 calls an API turn: 959 key
  presses and typed strings a trip run. So it writes the most output (243k tokens a trip run to browserd's 111k), which
  stays in context, and it spends most of its time in the model (37.1 of 50.1 minutes on trip). Its parks mean is pulled
  up by r3, $28.87 and 370 API turns against $8.33 and $9.50.
- **browserd puts its steps in queues:** 6.2, 10.4 and 8.4 steps a queue on capex, parks and trip, one call an API turn.
- **Playwright batches by writing code:** 25, 67 and 132 `browser_run_code_unsafe` calls a run. Most of the time it
  loses is outside the model (44.8 minutes on trip to browserd's 19.8). Adding up the waits written into its code and
  its `browser_wait_for` calls gives about 18 minutes a trip run and 31 in r2, which waited for Yelp's block to clear,
  and failed locators took 2.7 minutes a trip run and 4.3 a parks run. The rest of that time was not split further.
- **Claude in Chrome batches with `browser_batch`** (25.5, 12.1 and 19.2 actions a batch, on 15, 71 and 65 batches a
  run), and takes the most images on capex, parks and trip (77, 182 and 163 a run). Its time gap to browserd is largest
  on capex (1.83 times) and smallest on trip (1.30). It refused reddit.com in all three trip runs ("This site is not
  allowed due to safety restrictions"), and two of its agents quoted Reddit from Google's results instead; the judge
  passed those runs at 50/50.
- **Yelp's bot check showed up in Playwright's runs only.** All three Playwright agents reported Yelp blocks: r1 a 403
  on Boston that never cleared, r2 a 403 "after my rapid page loads" and then a "not a robot" check for the rest of the
  run, which cost it six items, and r3 a 403 on typed URLs, which it got past through the site's search form, passing
  50/50. No other arm's agent reported one. `clean_start` deletes Yelp's cookies before every run, so no run inherited a
  check. These runs do not show why: bursts of page loads from code, the DevTools attachment and chance all fit, and it
  was not tested. It also hit a browserd calibration's judge (below), so browserd's Chrome is not exempt.

## Runs set aside

`full1-parks-set-aside/`, 9 runs, $7.08 in all, not in any table above:

| run | why | min | $ | API turns |
|---|---|---|---|---|
| browserd r2 | an earlier run's map | 0.9 | 0.20 | 10 |
| browserd r3 | an earlier run's map | 0.8 | 0.25 | 9 |
| Claude in Chrome r1 | an earlier run's map | 1.7 | 0.20 | 16 |
| Claude in Chrome r2 | no map URL in the final message | 2.4 | 0.22 | 18 |
| Claude in Chrome r3 | no map URL in the final message | 30.7 | 5.17 | 144 |
| devtools r1 | an earlier run's map | 0.9 | 0.24 | 14 |
| devtools r2 | an earlier run's map | 0.6 | 0.20 | 11 |
| Playwright r1 | an earlier run's map | 0.8 | 0.25 | 13 |
| Playwright r2 | an earlier run's map | 1.2 | 0.34 | 13 |

- The seven reported a map an earlier run had made, found in My Maps, so `compare.py` graded none of them. Fixed in
  5a24238, then parks ran again: browserd r2 and r3, Playwright r1 and r2, devtools r1 and r2. browserd r1, Playwright
  r3 and devtools r3 kept their results from before the Trash step: browserd r1 started clean, and the other two saw
  earlier maps in My Maps but did not open them.
- Claude in Chrome shows the model `mid=REDACTED`, so r2 reused the earlier map and r3 built a whole map, which scored
  107/107 by hand, and could not read its link. All three Claude in Chrome runs went again from 18:18 after the Trash
  step, and got the link by printing the map's id character by character.

Also:
- `full1-trip-set-aside/claudechrome-r2-1614/` holds only a prompt: the run was stopped at the 16:14 restart before its
  agent began. The four r1 runs done by then were kept.
- Two trip runs were judged again, not run again: devtools r1, whose judge returned malformed JSON (first judge $1.95;
  its `judge_cost` of $3.99 adds both; `full1-trip-rejudge.log`), and Claude in Chrome r3, whose judge lost the network
  at 23:20 in a wifi outage (`full1-trip-rejudge2.log`).

## Minimized windows (drawn1, 2026-10-30)

`clean_start` opens each run's window minimized (`opens.window`), and Chrome does not draw a minimized window's tab,
nor reliably a background one, where Claude in Chrome works (the bench made both fixes,
[own-profiles.md](own-profiles.md), "Runs set aside"; `compare.py` has neither), while a browserd queue keeps its tab
drawn. `../long-tasks/drawn.py` timed Playwright's and devtools' most-used calls in a window `clean_start` opened on
lt-capex: five reps minimized, five with browserd's screencast on the tab, and three on screen. Claude in Chrome was
not timed.

| median s (failed of n) | minimized | screencast | on screen |
|---|---|---|---|
| devtools take_screenshot | 4.20 (5 of 30) | 0.62 | 0.53 |
| devtools click | 2.49 | 0.34 | 0.31 |
| Playwright browser_take_screenshot | 2.65 (7 of 30) | 0.49 | 0.29 |
| Playwright browser_click | all 10 timed out | all 10 timed out | 1.00 |

Medians are over the calls that returned. devtools' 5 failed screenshots got no reply within drawn.py's 120 s limit;
Playwright's failed screenshots and clicks hit its own 5 s timeout. Other calls moved by under 0.6 s, and the
screencast matched the screen on everything but Playwright's clicks. Weighted by full1's calls, running on screen would
have taken 8.1 minutes off a devtools run and 2.5 off a Playwright run (`drawn.py report drawn1`), not counting
Playwright's clicks.

full1's transcripts don't show that much, though. Playwright's clicks failed 66 times in 268, 11 by timeout, and its
screenshots once in 573, devtools' never in 722. Screenshots alone in their API turn took a median of 0.08 to 1.83 s on
Playwright, devtools and Claude in Chrome. And split by task with the same medians, a devtools capex run would have lost
7.8 minutes to its window, yet it spent only 1.9 minutes outside the model. So full1's windows did not stay as drawn1's minimized ones,
and which were drawn when was not logged.

## Caveats

- **Three runs an arm,** with no significance test. One wrong item fails a trip run, so browserd's r1 and Claude in
  Chrome's r3 are thin evidence against either arm; Playwright's r2, 10 items, is the one large loss.
- **Windows opened minimized, unlike the bench.** In drawn1 (above) that slowed devtools' and Playwright's screenshots
  and clicks, but full1's transcripts show far less of it. The time gaps stand as measured, and devtools' and
  Playwright's may be overstated by up to 8.1 and 2.5 minutes a run. Claude in Chrome was not timed. Cost is less
  exposed than time.
- **Shared machine and account.** The three tasks ran at once for hours, so runs competed for CPU and network, and how
  much varied: capex ended at 15:45, Claude in Chrome's parks started at 18:18, and trip ran until 23:12.
- **What the grades can't see.** capex's and parks' graders read exports: they do not judge looks, and no export shows a
  capex chart is linked. `pdftoppm` is missing here, so capex's runs have no slide pictures, and no one looked at those
  decks. Both tasks have all arms at 100%, so they may simply be too easy to separate them on correctness.
- **"By hand" is checked on trip only.** The prompts ask for work by hand with no scripts. All four arms read pages with
  scripts, and Playwright's code also clicked and typed. Trip's rubric has a `by_hand` item for how the Doc was written,
  which no run lost; capex's and parks' graders can't tell.
- **The judge is one Opus run a Doc.** Its folder and request hide the arm, but `seen.txt` can show it
  (`../long-tasks/README.md`). Looks run from 6 to 8, with arm means of 7.3 to 7.7, too close to rank the arms. Fares
  and restaurants are live, so runs did not face identical pages.

## Calibration runs (`longer-*`)

Before the batch, 2026-10-05 from 01:26 to 11:24, each task was sized to about three times the old length on browserd
(means in `long-tasks-loop.md`: trip 12.9 minutes, capex 3.7), with a prompt about as long as the original trip's. One
run per version through `compare.py`, on the `experiments` profile, alternating with the loop's runs through a lock.
browserd's server moved through the loop's commits until 05:03 and stayed on 50e461f after, so the later runs share
full1's code. The tasks then landed as b40a4d8 and e5c8b16.

| version | arm | score | min | $ | API turns | tool calls | tool errors | cache-read | output |
|---|---|---|---|---|---|---|---|---|---|
| capex v2: 7 companies, 4 values, one pivot chart | browserd | 69/69 | 9.1 | 2.14 | 80 | 81 | 1 | 5.96M | 36.1k |
| capex v3: 10 companies, a chart per company | browserd | 96/96 | 25.2 | 8.17 | 190 | 189 | 5 | 29.36M | 95.7k |
| capex v4: 10 companies, one pivot chart | browserd | 86/86 | 9.0 | 2.27 | 96 | 95 | 3 | 6.86M | 34.2k |
| capex v5 (kept): 10 companies, two pivot charts | browserd | 86/86 | 14.8 | 3.32 | 132 | 131 | 2 | 10.96M | 45.1k |
| capex v5 (kept) | Playwright | 86/86 | 17.0 | 7.96 | 196 | 799 | 21 | 27.90M | 87.7k |
| parks (kept) | browserd | 107/107 | 21.8 | 5.22 | 142 | 141 | 17 | 18.03M | 65.9k |
| parks (kept) | Playwright | 107/107 | 39.8 | 7.72 | 132 | 232 | 12 | 19.46M | 70.2k |
| trip v3: 8 cities, Sheet, linked chart, 13-slide deck | browserd | 38/39, polish 5/5, looks 6 | 34.5 | 8.44 | 164 | 169 | 11 | 29.28M | 115.9k |
| trip v3 | Playwright | 37/39, polish 5/5, looks 7 | 59.8 | 28.58 | 334 | 517 | 32 | 99.33M | 208.6k |
| trip v4: 8 cities, Google Doc, GSA rates, Yelp and Reddit | browserd | 60/61, polish 6/6, looks 8 | 40.8 | 7.90 | 172 | 174 | 9 | 27.00M | 121.6k |
| trip v4.1 (kept): 6 cities, 7+ spots each | browserd | 49/50, polish 6/6, looks 7 | 40.2 | 10.52 | 195 | 194 | 14 | 38.15M | 133.5k |
| trip v4.1 (kept) | Playwright | 42/50, polish 6/6, looks 7 | 66.4 | 16.29 | 236 | 398 | 15 | 55.51M | 149.3k |

- **capex** needed four tries. Seven companies were too short (2.5 times). A chart on every company slide overshot (6.8
  times): against v4, which differs by those ten charts and the prompt's wording of them, it took 94 more calls, about 9
  a chart. One pivot chart was short again (2.4 times), so v5 has two, at 4.0 times, and it was kept.
- **trip** v3 kept the deck and took 2.7 times. All three of its misses across both arms were fares the agent saw on
  Google Flights mid-run that neither of the judge's snapshots held, so the rubric now takes a Google Flights list
  `seen.txt` shows the agent reading; those runs were not judged again. Then the task was redesigned on request: a
  Google Doc in place of the Sheet and deck, GSA per diems, and Yelp and Reddit research. v4 had eight cities (40.8
  minutes); v4.1 has six cities with 7+ spots each and took 40.2 minutes, 3.1 times. It was kept.
- **parks** is new and needed one try, 21.8 minutes.

With the prompts unchanged but for the profile's name, the kept versions make a fourth run of two arms next to full1:

| task | arm | min, calibration | min, full1 mean | $, calibration | $, full1 mean | calls, calibration | calls, full1 mean |
|---|---|---|---|---|---|---|---|
| capex | browserd | 14.8 | 10.3 | 3.32 | 2.52 | 131 | 94 |
| capex | Playwright | 17.0 | 12.6 | 7.96 | 3.46 | 799 | 216 |
| parks | browserd | 21.8 | 21.2 | 5.22 | 4.43 | 141 | 128 |
| parks | Playwright | 39.8 | 27.5 | 7.72 | 4.50 | 232 | 177 |
| trip | browserd | 40.2 | 32.7 | 10.52 | 7.65 | 194 | 146 |
| trip | Playwright | 66.4 | 65.1 | 16.29 | 14.05 | 398 | 303 |

The Playwright capex row shows how far one run can mislead: 799 calls and $7.96, against 181 to 271 calls and $2.90 to
$3.79 in full1. The cost gaps quoted that day between Playwright and browserd (3.4 times on trip v3, 2.4 on capex and
1.5 on parks) came from these single runs and are wider on capex and parks than full1's means (1.38 and 1.01).

The v4.1 Playwright run lost eight items: four flight tables, wrong Yelp links on Denver, and Boston's spots, dinner and
sources to a Yelp block. The v4.1 browserd run lost denver_backing, a Reddit comment that does not name the pick. The v4
browserd run lost boston_dinner: the run had only a Google snippet for its rating, and the judge met Yelp's captcha when
it checked. That run's `verdict.json` lists one more passed item than the 60/61 `result.json` counts; the table keeps
`result.json`'s.

**Options weighed.** Costed for the existing tasks: more cities, a dinner near the hotel, a Sheet with a linked chart,
restaurant photos and an itinerary slide on trip; EDGAR search, more companies, a third year and pivot tables on capex.
Left out: a second weekend, airport transit times, 10-Q figures and cloud segment revenue; a Gmail draft or Calendar
hold was rejected for writing to a real account. For the third task, scouts tried Google My Maps (chosen, for map
canvas, place search and layer state, graded from KML), Google Docs (runner-up, and later trip's output), Google
Calendar, a JupyterLite notebook and the Federal Student Aid Estimator. Forms, diagrams.net, Excalidraw, BeepBox, Sites,
Keep, Tasks and Gmail were rejected for no usable export, leaking state or being too shallow.

**Noted in browserd on that day, not rechecked since:** `pick` emptied studentaid.gov's State autocomplete instead of
choosing "(CA) CALIFORNIA"; color swatches and chart-type tiles have no names in snapshots, so `name:` cannot find them;
`wait_for "Update"` matched hidden text; and My Maps' route suggestions show only in screenshots.

## Data and reproduction

- **Runs:** `C:/Users/2004j/Desktop/browserd-long-tasks/` (the data folder beside the repo): `full1-capex/`,
  `full1-parks/`, `full1-trip/` (each `<arm>-r<n>/` holds `prompt.md`, `transcript.jsonl`, `result.json` and the
  recording; trip's `judging/` holds the verdicts), the set-aside folders, `full1.log`, `full1-*.log`, `longer-*/`
  with `longer-*.log`, and `drawn1/` (`<arm>-<condition>-r<n>.jsonl` beside `.windows.jsonl`) with `drawn1.log` and
  `drawn1-shown.log`.
- **Commands,** from the checkout the 9230 server runs, with `PYTHONUTF8=1`, `BROWSERD_LONG_TASKS_PROFILE=<lt profile>`
  and `BROWSERD_LONG_TASKS_CLAUDE` at Claude Code 2.1.289, one per task at a time:

      python experiments/long-tasks/compare.py run full1-capex --task capex --k 3
      python experiments/long-tasks/compare.py run full1-parks --task parks --k 3
      python experiments/long-tasks/compare.py run full1-trip --k 3
      python experiments/long-tasks/compare.py report <exp>

  drawn1, with `BROWSERD_LONG_TASKS_PROFILE=lt-capex`:

      python experiments/long-tasks/drawn.py run drawn1 --arms playwright,devtools
      python experiments/long-tasks/drawn.py run drawn1 --arms playwright,devtools --conditions shown --k 3
      python experiments/long-tasks/drawn.py report drawn1
