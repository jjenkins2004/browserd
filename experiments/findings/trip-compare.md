# Findings: the trip deck on four browser MCP servers (prelim3)

Run 2026-10-04 on the `personal` profile with `../long-tasks/compare.py`. This is preliminary and incomplete: one run
per arm (round 1). Round 2 never ran, because the plan's session limit cut it off. Playwright MCP beat browserd on this
task. The transcripts were then diagnosed call by call, and the gap traced to browserd's click guard in Slides, not to
the method or to Playwright. **The guard has since been removed** (7dc5101), so Next steps 1 and 2, on `guard.py`,
no longer apply; step 3 landed in d8de3f4 ([page-now.md](page-now.md)). Code named below is as it was at 48bee1f.

Raw data lives outside the repo, on the Mac that ran it only: `../browserd-long-tasks/prelim3/`.
- Per run (`<arm>-r1/`): `transcript.jsonl`, `result.json`, `video.mp4`, `frames/`, `slides/`, `deck.pdf`.
- The judges' folders: `judging/`.
- `prelim3.log`, `gallery.html`, `gallery-blind.html`.
- `diag/`: the four diagnosis reports and their scripts:
  - `browserd.md`;
  - `playwright.md`, which tables every one of its 166 calls;
  - `metrics.md`, built by `metrics.py`;
  - `devtools-agentbrowser.md`;
  - `bd/pairs/stopNNN.jpg`, which puts the screenshot the agent last saw beside each guard capture.

browserd's own records of the run are under `.run/calls/personal/2xy9uk-*/`.

## What was run

- **Task:** `../long-tasks/trip/prompt.md` as of 48bee1f (later versions ask for more), asked as a person would.
  - Research flights (3 cheapest non-ad nonstops from LAX per city on Google Flights), the cheapest 4-star 4.0+ hotel
    (Google Hotels), and the November high, for Seattle, Denver and Chicago.
  - Then build a 6-slide Google Slides deck by hand in the editor, with no Apps Script, and finish without asking.
- **Model:** claude-sonnet-5-5, headless `claude -p`, clean: no user settings, the arm's MCP server alone, no other
  tools, so no Read either.
- **Arms:** browserd; Playwright MCP 0.0.82 (`--cdp-endpoint`); chrome-devtools-mcp (`--browserUrl`); agent-browser
  (`agent-browser mcp`).
- **Browser:** all four drive browserd's `personal` Chrome, one run at a time, each from one fresh tab (`clean_start`).
- **Grading:** claude-opus-5-5 judges the deck through browserd against `../long-tasks/trip/rubric.md` as of 48bee1f:
  13 correct items, 5 polish items, and looks scored 1–10.

## Results

| arm | correct | polish | looks | min | $ | API turns | calls | errors |
|---|---|---|---|---|---|---|---|---|
| browserd | 12/13 | 4/5 | 6 | 17.3 | 6.61 | 145 | 151 | 27 |
| Playwright | 12/13 | 5/5 | 7 | 11.3 | 3.31 | 107 | 166 | 4 |
| chrome-devtools-mcp | 11/13 | 5/5 | 6 | 16.8 | 10.64 | 243 | 621 | 19 |
| agent-browser | 12/13 | 5/5 | 6 | 21.4 | 21.57 | 236 | 843 | 14 |
| browserd, prelim2 (same task, earlier) | 12/13 | 5/5 | 7 | 18.3 | 8.12 | 171 | 181 | 22 |

- **Turns:** "API turns" counts unique assistant message ids. result.json's `turns`, which `compare.py report`
  prints, is Claude Code's `num_turns`, tool calls + 1. For agent-browser that is 844 against 236 real turns.
- **Failed runs:** `agentbrowser-r2-session-limit/` and `devtools-r2-stopped-*/` were cut off by the session limit and
  are not results.
- **Prices:** every run's `total_cost_usd` fits exactly at $0.20/M cache read, $4/M one-hour cache write and $10/M
  output.
- **Weather:** most runs wrote Denver's November high as 57°F, the number on Google's weather card. That fails the
  rubric's ±3°F of 52.9°F. Later versions name Wikipedia for the weather in the prompt, and the rubric takes ±2°F of
  its mean daily maximum.

## Where the gap is

Phases split at the first call that opens `presentation/create` or `slides.new`.

| | browserd research | Playwright research | browserd deck | Playwright deck |
|---|---|---|---|---|
| wall s | **170** | 212 | 858 | **458** |
| turns | **19** | 46 | 126 | **61** |
| errors | 1 | 1 | 26 | 3 |
| mean context per turn | 40k | 36k | 189k | 139k |
| $ | **0.44** | 0.67 | 6.17 | **2.64** |

- **browserd won research.** It already extracted text: `find` filters, `evaluate_script` with card text, and 3
  weather searches in one queue.
- **The deck holds the whole gap:** +$3.53 of the +$3.30 total, and +399 s of the +357 s.
- **Turn count drives both cost and time.**
  - Both start the deck with about the same context (65k vs 69k), and browserd adds less per turn (1.95k vs 2.73k).
    At Playwright's 61 turns, browserd's deck cache reads would come to $1.51, against Playwright's $1.67.
  - browserd's deck turns are faster (6.8 s vs 7.5 s).
- **Cost:** cache reads are 87% of the extra $3.30. Of that, $1.91 is more turns and $0.96 is the bigger context
  those extra turns build. Output and thinking come to $0.11.
- **Time:** the extra 357 s is 280 s of model time and 77 s of tool time.
- **Not causes:**
  - tool definitions: the first turn is 8.7k tokens for browserd against 9.9k for Playwright;
  - image tokens: browserd's scaled JPEGs came to about 60k, Playwright's 1600×1000 PNGs to about 87k;
  - thinking.

## How Playwright built the deck

It used browserd's own method: read pixels off a screenshot, then send real mouse and keyboard input.
- **Setup:** `browser_resize` to 1600×1000, and `browser_take_screenshot {scale:'css'}`, so screenshot pixels equal
  mouse coordinates.
- **Each deck turn:** one `browser_run_code_unsafe` macro, plus a `browser_take_screenshot` in the same message. That
  shape covers 47 of its 60 deck turns.
- **Typing:** a real double-click on a placeholder or cell focuses Slides' hidden text iframe
  (`textbox "Document content"`). From there `page.keyboard.type` lands, so the drawn canvas is never addressed.
- **Volume:** 48 macros hold about 411 actions, 8.6 per macro and up to 48.
  - 99 coordinate clicks, 12 double-clicks, 11 drags, 107 `keyboard.type` and 173 key presses.
  - The key presses were Escape 47, Tab 42, Meta+a 40 and Enter 23.
- **DOM-addressed actions:** only 11 of the ~415. These were `getByRole('menuitem', {name: 'Insert'})` then
  `/^Table/` or `/^Image/`, `getByText('Replace image')`, and the deck's title box. Everything else was coordinates:
  - the toolbar at y=85;
  - colour swatches;
  - filmstrip thumbnails at `(118, 280 + i*102)`;
  - table cells;
  - drag handles;
  - Stock & web tiles.
- **Loops:** 12 of the 48 loop, all over data: table cells as type + Tab, 5 slide titles, duplicates. None retries or
  polls.
- **Errors:** 3 came in the first 25 s of Slides, from trying `browser_click` on Slides targets. After it switched to
  `page.mouse`, 94 calls had no error.
- **Silent misfires:** 11, for example Meta+M added no slide and Tab appended into filled cells. Each was caught by
  the screenshot in the same turn and fixed within 1–2 turns.
- **Reuse:** it built Seattle's slide, then duplicated it for Denver and Chicago, swapping text and Replace image.
- **Research:** search URLs built directly, then `innerText` slices of a median 1.8k chars. Playwright 0.0.82 writes
  page snapshots to files rather than replies, so a navigate or click reply was about 550 chars.

```js
// fill a 6x4 table in one call: 24 types + 23 Tabs
await page.mouse.click(460, 458);
for (let i=0;i<cells.length;i++){ await page.keyboard.type(cells[i]); if(i<cells.length-1) await page.keyboard.press('Tab'); }
// header row: drag-select, then fill and text colour by pixel
await page.mouse.move(430, 470); await page.mouse.down(); await page.mouse.move(1300, 470, {steps:6}); await page.mouse.up();
await page.mouse.click(545, 85); await page.mouse.click(551, 185); await page.mouse.click(987, 85);
```

## Why browserd took twice the deck turns

126 turns against 61. Of the extra 65 turns, +27 come from more actions (419 vs 309) and +38 from fewer actions per
turn (3.3 vs 5.1). Both trace mostly to the guard.

### 1. 22 guard stops, all false alarms

Of browserd's 27 errors, 22 were guard stops: 14 spot stops and 8 focus stops. The other 5 were the agent's own slips:
2 stale uids, `click_down` before `move_at`, the key name `Ctrl+m`, and a rename that drew Slides' error dialog.
**In 14 of 14 spot stops the retry pressed the same target, and every focus stop's retry without the click worked. No
stop prevented a misclick.**

- **The focus check is blind to iframes.**
  - Code: `browser/steps/guard.py`, `_keys`, with the page-side `focusKept` and `deepActive`. `deepActive` follows
    shadow roots but not iframes.
  - Slides takes keystrokes in a same-origin `about:blank` iframe, so `document.activeElement` is the `<iframe>`, which
    never matches the canvas element the click hit.
  - So every "click the canvas, then type" queue is stopped. That was all 8 focus stops.
  - 7 retries sent the same keys without the click and worked; in the eighth the agent changed approach.
  - `paste` is not key-checked. The agent found this and pasted all text after its 53rd call.
- **The spot check's reference is stale inside a queue.**
  - Code: `guard.py`, `_press`. The reference is the last screenshot a reply gave before the queue arrived
    (`browser/tabs/worker.py`, `reference_before`); screenshots inside the queue become the reference only when the
    queue closes.
  - So any earlier step of the same queue that changes the page under a later press stops that press: open a menu,
    then pick its item; open a palette, then pick a swatch.
  - Of the 14 spot stops:
    - 12 were the agent's own menu, palette, dialog, duplicate, search-clear or typing changes;
    - 1 was a button's selected state;
    - 1 (`#097`) had pixel-identical reference and capture.
- **The effect on batching:**
  - 25 deck queues tried 2 or more presses, and 17 of them were stopped (68%). So the agent stopped chaining.
  - 50 of its 125 deck calls were one click plus a screenshot.
  - Insert > Table took 4–7 calls each time, header styling 5–7, and a photo 5–8; Playwright did each in one macro.
  - Table fills, which are paste + Tab with no press in between, did go in one queue of 34–43 steps.
- **The cost:** the 27 failed turns, and what they added to the context, came to about $2.11 of cache reads and about
  160 s of recovery. Without them the run would have cost about $4.50.

### 2. Error replies: bloated, and their screenshots lost

- **The page view:** every failed queue appends a "page now" accessibility view from `_after_stop`
  (`browser/steps/steps.py`), cut to `ERROR_MOST` = 9,000 chars.
  - That is 166k chars in all, 46% of browserd's tool text.
  - It is about 108k tokens, 35% of the final context, and carried $1.49 of cache reads.
  - A failed deck reply averaged 7.2k chars, against 820 for a good one.
- **The lost screenshots:** Claude Code flattens every `isError` result into one text string and drops its images
  (`steps.py` returns `content` + images with `isError: failed`).
  - The 22 "screenshot below" captures never reached the model, nor did 14 screenshots taken earlier in those queues.
    That is 36 images lost.
  - The agent took them again: 10 retries began with a screenshot, plus 1 screenshot-only turn.
  - The guard keeps those unseen captures as its next reference.
  - `browser/README.md`'s click-guard promise, that a stop fails "with a screenshot of the page now", does not hold
    under Claude Code.

### 3. Smaller items

- **Research text** (88.8k chars, 13.2k of it Google `aclk` ad URLs) rode through 126 deck turns: $1.55 of reads,
  against Playwright's 69k chars over 61 turns.
- **Boilerplate:** step "ok" lines came to about 48.8k chars. Screenshot result lines (about 357 chars each, holding
  the full saved path) came to about 41k.
- **Tool time** was +72 s:
  - the agent's own `wait still` steps, 146 s (96 s of that the floor its `still` values set);
  - the 0.1 s `GAP` between steps (`steps.py`), 66 s over about 659 gaps;
  - opening tabs, 34 s.

prelim2's browserd run shows the same mechanics:
- 15 guard stops (8 focus, 7 spot), with retries sending the same keys without the click, or the same coordinates;
- page-now views at 41% of tool text;
- 24 images lost.

It typed with `type_text` rather than `paste`, so it hit the focus check for longer.

## The control arms: does scripting explain it?

chrome-devtools-mcp and agent-browser can both run scripts, and both lost by more.

| deck phase | Playwright | devtools | agent-browser |
|---|---|---|---|
| turns | 61 | 153 | 173 |
| mean context per turn | 139k | 230k | 417k |
| setup / table slide / first city slide, turns | 19 / 11 / 12 | 32 / 46 / 36 | 56 / 23 / 50 |
| a duplicated city slide (Chicago), turns | 7 | 7 | 9 |

- **Turns, not calls, are the cost.** Both fire many calls per turn: devtools up to 35, agent-browser up to 19.
  devtools' 527 deck calls took 134 s of tool time in all; its wall time is 85% model time.
- **The gap sits on Slides widgets an agent had not yet worked with.** Once a slide is duplicated, all three are even.
- **No click at coordinates:**
  - The accessibility snapshot shows menus, the toolbar, the theme panel, the image picker and placeholder text.
  - It does not show colour swatches or the table-size grid. Canvas objects appear as one
    `image "Click to select borders"`, and cell uids go stale at once.
  - devtools faked clicks with synthetic events from `evaluate_script`. agent-browser tagged
    `document.elementFromPoint(x,y)` with an id, then clicked it, 35 times.
  - Synthetic input does not work everywhere in Slides: agent-browser's scripted `fill` into the image search did
    nothing until it sent real keys.
- **Context bloat:**
  - devtools' snapshots: Google Flights' results were 38.8k chars, and in Slides two thirds of a snapshot is filmstrip
    text, one word per node.
  - agent-browser wraps every result in a ~700-char JSON envelope that repeats the response, which cost about $6.50 of
    re-reading. Its context reached 692k, and Claude Code's clearing of old results re-wrote 551k tokens to cache
    ($2.20).
- **Harness artifact:** `--tools ""` leaves no Read tool. devtools lost about 12 calls saving snapshots to files it
  could not read, and Playwright's macros saved a `t.png` it never saw.

Conclusion: what wins is batched real input at screenshot coordinates. Loops mattered little, and page-side JavaScript
did not help. browserd already has that input (`move_at` / `click_down` / `click_up`, `press_key`, `paste` in one
queue). Its guard is what kept it from batching in Slides.

## Corrections to what was said before the diagnosis

- **Playwright's context:** about 95k tokens per turn on average (deck 139k), not 60k. browserd's is 170k (deck 189k).
- **Guard stops:** 22 (spot 14, focus 8), not 21.
- **Images:** not the difference. browserd's images cost fewer tokens than Playwright's; the gap is text, mostly
  error replies.
- **The fix is not "add a script tool":** browserd's queue already batches. The batching broke on false stops.

## Decision so far

Do not move browserd onto Playwright MCP. Playwright won with browserd's own method, and browserd won research
outright. Moving would give up sessions, tab isolation, profiles on the user's own Chrome, the hand-off for logins and
captchas, the records and the dashboard. In this very experiment, Playwright took over browserd's placeholder tab on
its first move. The click-guard experiment (`click-guard.md`) held that a check before a click wins; these were
two blind spots in it.

## Next steps (proposed: 1 and 2 dropped with the guard, 3 done in d8de3f4)

1. **Focus check (`guard.py` `_keys`, `focusKept` / `deepActive`).**
   - Follow focus into same-origin iframes.
   - Count focus in an iframe inside the document the click landed in as kept when nothing entered the top layer
     since. That top-layer test is the keyboard check's other half.
   - When the check cannot tell, let the keys through rather than stop.
2. **Spot check (`guard.py` `_press`, `worker.reference_before`).** Planned chains stop because a press compares
   against the screenshot from before the queue. The click-guard experiment counted "a popup the previous click
   opened" as unsafe, so this is a trade-off to decide. Options:
   - skip the pixel and DOM test for a press after an earlier step of the same queue changed the page, keeping the
     top-layer and new-document tests;
   - a per-step flag the agent sets when it expects the change, for example a menu item after opening its menu;
   - accept a match against any screenshot the agent has seen of that state.
3. **Stop replies (`steps.py` `_after_stop`, `ERROR_MOST`; the `isError` return).**
   - Send a guard stop as a normal result that says the queue stopped, so its screenshot reaches the agent under
     Claude Code.
   - Cut the page-now view to a short note.
   - Fix `browser/README.md`'s click-guard paragraph to match.
4. **Smaller:**
   - shorten screenshot result lines (the full path) and step "ok" lines;
   - consider dropping ad URLs from views.
5. **Rerun:**
   - a quick Slides smoke test of 1–3 first: click a placeholder, then type, in one queue; open a menu, then click its
     item;
   - then `compare.py run prelim4 --arms browserd,playwright --k 2` (about $20), and compare with
     `diag/metrics.py`-style numbers: turns per phase, guard stops, error-reply share.
6. **Open:**
   - the weather rule (accept a value seen on a weather page, or within 6°F?);
   - whether to give arms a Read tool (devtools and Playwright both wrote files they could not read);
   - make `compare.py report` count API turns, not `num_turns`.
