# Findings: the click guard on real sites

Run 2026-09-26. Follows [click-guard.md](click-guard.md), where B (a check before every `click_at`) won on a made-up
app with settings tuned on a lab page. The plan, with every rule fixed before the counted runs and a record of each
change, is kept with the raw data, as `EXPERIMENT-real-sites.md` in `.run/experiments/archive-2026-09-26/`.

## The result

- **Part 1 (the detectors, 19 real sites, no agents) locked the lab pick:** S1 at s 48, T 12, F 20%, Fc 25%, with S2a
  and S3.
  - It missed 3.4% of the spots whose target changed (95% upper bound 10.3%, bar 15%).
  - It stopped 8.1% of the spots whose target did not (upper bound 12.3%).
  - It caught every popup in the data: 805 points of cookie banners, marketing modals, chat panels, floating ads and
    new layers. The 29 counted misses were layout shifts on Kayak and Medium.
- **Part 2 (54 agent runs with B live) did not validate it in use,** by a narrow margin.
  - Needless stops were 8.7% of clicks on an unchanged target, in line with Part 1, but the upper bound was 13.1%
    against a cap of 12.3%. The plan's rule makes that "not validated in use".
  - The needless stops were on Maps, Allrecipes, tldraw and CNN.
  - B made 14 needed stops, and no click that went out met a changed target (only 4 such clicks: too few to judge).
  - Needless stops cost about 1.7 s a run.
- **Part 2b (72 more agent runs with Part 1's selection live) passed the same rule.**
  - The selection is S1 s 24, T 64, F 10%, Fc 75%, with S2c and S6.
  - Needless stops were 6.5%, with an upper bound of 10.9%, so the selection is validated in use at that bar.
  - It made 20 needed stops and 0 misses (of 7 such clicks).
  - **But the two settings stop the same clicks.** Re-scored offline on each run's clicks, they make the same needed
    stops, and within 3 of the same needless ones (40 against 37 over 575 clicks). The pass came from which clicks that
    run's agents made, not from the setting.
- **In use the choice hardly matters. Part 1 favours the selection:** in-sample, 0% misses and 3.1% false alarms,
  against 3.4% and 8.1%.
- **Truth took three runs.** A blind audit of the ground truth failed twice, each time on a real bug, and passed on
  the third run.

Raw data, images and scripts are kept in `.run/experiments/real-sites/`, which is gitignored:
- Part 1: `part1/` (the counted run 3), `part1-run1-audit-failed/`, `part1-run2-audit-failed/`,
  `part1-score-audited.txt`, `part1/frontier.txt`;
- Part 2: `part2/` (one record and one agent stream per run), `part2-score.txt`; Part 2b: `part2b/`,
  `part2b-score.txt`, `part1/lock-selection.json`; the guard and truth logs are in `.run/calls/test/`;
- the crawl: `crawl/`; the pilots: `pilot*/`;
- the code: `part1.py`, `part1score.py`, `audit.py`, `crops.py`, `part2.py`, `part2score.py`, `adblock.py`,
  `clearsites.py`, `checks2.py`;
- the experiment's code in browserd (`truth.py`, `guard.py`, `experiment.py`, and the hook in `server.py`,
  `steps.py` and `worker.py`, as a patch): `.run/experiments/archive-2026-09-26/`. It was taken out of the repo after
  the experiment.

## The question

1. **Mechanism:** on real pages, how often does B miss a spot whose target changed since the agent's screenshot, and
   how often does it stop a click whose target did not? Does the lab pick hold, or does another setting do better?
2. **In use:** sealed agents doing real tasks by `click_at`, with B live. How many stops were needed, how many clicks
   landed on a target the agent never saw, how many stop loops, and what does it cost in time?

**Why two parts:** Part 1 measures every setting on the same raw readings, with ground truth at many points, but
with a scripted harness. Part 2 shows what agents meet, but gives few clicks on a changed target and only one setting.
Part 1 chooses, and Part 2 checks the choice in use.

## How it was designed, and why

### An adversarial review first

Before any code, the plan went through 9 rounds with an adversarial reviewer (a subagent told to find what would make
the result wrong). Round 1 found 11 must-fix problems, round 8 found 1, and round 9 found none. Every finding was taken
except those listed under "Not taken" in the plan.

**Why:** the lab result could be an artifact of its own page. Each rule below exists because a round showed how the
result could otherwise mislead: a ground truth that agrees with the detector by construction, settings tuned on the
data they are scored on, or sites that never show a popup.

### The sites

- **The crawl:** 24 candidate sites, loaded through browserd in the test profile by subagents, recording where and
  when dialogs popped up (`crawl/`).
  - **Ruled out:** Target and Wayfair (PerimeterX bot walls), Reddit (reCAPTCHA wall) and Photopea (Cloudflare
    check in a fresh profile).
  - **Kept as reserves:** weather.com, Pinterest (its wall cannot be dismissed) and Windy (its particle map animates
    everything).
- **19 sites:** 13 DOM sites and 6 drawing apps.
  - **DOM sites:** Home Depot, Sephora, Old Navy, Best Buy, Booking, Kayak, Airbnb, HubSpot, CNN, Forbes, the Guardian,
    Medium and Allrecipes.
  - **Drawing apps:** Google Maps, Excalidraw, tldraw, diagrams.net, GeoGebra and Desmos.
- **Why 19:** points cluster by site: one site's pages behave alike across trials. A new site adds more independent
  evidence than a 5th or 6th trial of a site already in, and 19 sites at 4 trials each fit in one day at 2 trials at
  a time.
- **Why this mix:**
  - consent banners (c1), marketing modals (c2), sign-in walls (c3), other modals (c4), chat and drawers (c5) and
    canvas apps (c6) each on more than one site;
  - Home Depot as a control, with no popups in the crawl;
  - Allrecipes for its timed popups.
- **Each site's script** (`sites.json`) was written from the crawl, then confirmed in 5 discarded pilots.

### Ground truth: what a click at a point would reach

Truth is computed from the DOM (`browser/truth.py`), never from the detectors' signals, so it cannot agree with them
by construction.

- **The target at a point** is the first of:
  1. the nearest actionable ancestor: a link, button, input, select, textarea, summary or label, a contenteditable
     root, an element with a clickable role or `tabindex` of 0 or more, else the top of a run of `cursor: pointer`
     ancestors;
  2. the drawing surface under it: a canvas, a large SVG, or the app's own surface. It wins over a focusable container
     that wraps it, since apps put the canvas in one;
  3. the element hit.

  **Why:** a click reaches a control, not a pixel. Two different spans inside the same button are the same click.
- **Identity, core, descriptor:**
  - every node gets an id, and whether it is still attached is tracked;
  - the core is tag, name and href;
  - the descriptor adds the card it sits in and its place among drawn same-name targets.

  **Why each detail:**
  - role is left out, since a re-render can add one with nothing else changed (Booking);
  - digit runs in names and long digit runs in hrefs are masked, since timestamps and counters change every render;
  - only drawn elements count for the place, since GeoGebra keeps a hidden copy of its old keyboard.
- **How a target changed:**
  - **same:** the same node, same core;
  - **changed in place:** the same node, new name or href;
  - **rebuilt:** the old node gone and a new one with the same descriptor, which is a re-render;
  - **different:** anything else.

  A point is **unsafe** when its target is changed in place or different, and at least one end is actionable.
  **Why rebuilt is safe:** a re-rendered list item still does what it did; Booking's calendar rebuilds its cells
  continuously.
- **Drawn** (added after run 2's audit): a target is drawn when its opacity, times its ancestors', is at least half.
  - A target that is not drawn still takes the click, but the screenshot shows what is under it, so the reference is
    compared as the image showed it.
  - **Why:** HubSpot's cookie banner takes clicks at opacity 0 for about a second before it fades in.
- **Set apart, not scored:**
  - **View changes:** the same canvas under the point after a pan or zoom. Most look the same before and after (an
    empty grid), so whether the click is unintended is unclear. They are reported apart.
  - **Dead:** neither end is a control.
  - **Ambient:** points in videos, carousels and tickers. B is expected to stop there.
  - **Fail-closed:** a check that errored, a scroll, a resize or a new document. These stop at every setting, so they
    tell nothing about settings.
- **Classes of change:** popup signatures, one frozen list for all sites, matched only on layers (fixed, sticky,
  absolute, frames, top layer): c1 consent, c2 marketing, c5 chat, ad, dialog. With no signature:
  - new layer;
  - shift (the old node moved away);
  - own click (a change the harness's own click made);
  - other.

  **Why own clicks are apart:** they are what a click chained after one's own click meets, which the agent partly
  expects. They get their own guardrail.

### Part 1: the detectors, without agents

- **The browser:** a throwaway Chrome per trial, with production's launch flags, in a hidden background tab with
  focus emulation on. Each trial is a first visit, as an agent's first run on a site is.
  - **Why production flags:** the no-throttling flags would make a hidden page behave unlike browserd's.
  - **Why focus emulation:** chrome-devtools-mcp turns it on for every page it drives. Without it a mouse move to a
    hidden tab waits 5 s.
- **Windows:** a reference capture, then checks at 0.5, 3 and 10 s. Each window is one of:
  - **idle:** nothing done, as while the agent thinks. Timed onsets from the crawl are placed 1 to 8 s after the
    reference.
  - **post-click:** the harness clicks a control that opens something (suggestions, a menu). This is what a chained
    click meets.
  - **dismiss:** the harness closes the load-time banner. The page is settling after it.

  **Why 0.5, 3 and 10 s:** a chained click comes within a second, and an agent's next turn takes 5 to 12 s.
- **Points:** 48 per window.
  - **36 on controls from the reference's map:** 18 in main content, 12 in page chrome, 6 on overlays.
  - **12 on the drawing surface,** on the apps.
  - **12 random,** as diagnostics only.

  **Why stratified:** agents click controls, and uniform points would mostly land on empty page.
- **Each check** records raw S1 pixel counts for every size and threshold, S2 (all four variants), S3 and S6, before
  and after a re-test, then truth.
  - Every one of the 4,519 settings is scored offline from the same readings, so settings are compared on identical
    events.
- **The reference capture is taken again** (added after run 2's audit) up to 3 times while the readings just before
  and just after it disagree.
  - **Why:** a popup mounting during the capture leaves no way to tell which state the image holds.
  - In run 3, 13 of 524 captures were retaken, and none stayed unstable.
- **Harm and load guards:**
  - **Bot walls:** the first one ends the site for the day. Repeated challenges can flag the home IP.
  - **Memory:** a watchdog pauses new trials under memory level 35 and quits a trial's Chrome under 25, after a past
    batch crashed the Mac.
  - **Stopping:** a STOP file stops trials cleanly.
  - **The frozen hash:** every window carries the hash of the code and site list, and the scorer reads no other, so a
    change to the code means a full rerun.

### The truth audit

Before any rate is printed, blind judges (4 subagents in parallel) compare the reference and check images at sampled
points, with the point marked in 480×320 crops, and answer "same control", "different" or "unsure".

- **Strata**, each chosen to catch a specific way truth could be wrong:
  - 50 unsafe points the lab pick missed, since a false "unsafe" label inflates misses;
  - 50 it caught (at most 8 per site, pixel-identical ones first), since a false label inflates catches;
  - 25 safe points only S1 stopped, where fade-ins and canvas popups hide;
  - 25 safe points S2 or S3 stopped;
  - 50 hard cases: rebuilt, changed in place, frames, overlays, new layers;
  - 25 unstable references;
  - 30 view changes, reported only.
- **Pass:** at most 5% truth errors in each stratum, at most 20% "unsure". Changes drawn inside a canvas, which truth
  cannot see, are counted apart.
- **Settling disagreements:** "truth wrong" only when the saved DOM readings show truth read the wrong element, layer
  or liveness. A node with a new name or href is "changed in place" by definition.
- **Why gate on it:** every rate below rests on truth. A failed audit means fixing `truth.py` and rerunning all of
  Part 1, never patching labels.

### Scoring and choosing a setting

- **Miss rate:** the share of unsafe points a setting let through, averaged over checks.
- **False-alarm rate:** the share of safe points it stopped, likewise.
- **Cost of a click:** P·K·misses + (1 − P)·false alarms.
  - **P = 5%:** the share of clicks that meet a changed target. Layer 2 measured 7% on a page built to be busy.
  - **K = 10:** one missed click costs as much as 10 needless stops. A misclick can delete, publish or buy, while a
    needless stop costs one turn of 7 to 12 s.
  - **Why a cost rule:** the earlier plan capped false alarms at 2%. Pilot 2 showed that cap misses about half of real
    changes. The rule states the trade-off, and the frontier of settings is reported so another can be chosen.
- **Eligible:**
  - at most one site over 25% false alarms, since past that a site is unusable with the setting and is named as a
    known limit;
  - own-click misses at most 25%.
- **Selection:** the cheapest eligible setting, run leave-one-site-out. For each site it is chosen on the other 18 and
  scored on the held-out one. **Why:** choosing and scoring on the same sites overstates any winner.
- **Lock:** the lab pick stays unless the selection beats it held out, with the paired cost difference's 95% lower
  bound above 0 (a bootstrap over sites, then trials, then checks).
  - **Why favour the lab pick:** it was chosen before these data existed, so it cannot be overfit to them.
- **Miss bar:** the locked setting's held-out misses must have a 95% upper bound of at most 15%.
- **Minimums:** at least 40 windows with a counted unsafe point, and 12 popup windows on 4 sites. **Why:** without
  enough real changes, low miss rates mean nothing.

### Part 2: agents on real sites

- **Sites:** 9, fixed by rule before the run.
  - the 3 DOM sites with the highest Part 1 idle change rate, since that is where B is tested hardest;
  - 3 more drawn at random with a fixed seed, so the choice is not cherry-picked;
  - Maps, tldraw and diagrams.net, with tasks that click the canvas;
  - HubSpot is left out, since its chat reaches people.

  The draw gave Home Depot, the Guardian, Allrecipes, Airbnb, CNN and Forbes.
- **Tasks:** from the crawl, each doable with screenshots, clicks, keys and typing. Results are "the first result not
  marked Sponsored", to keep agents off ads.
- **Runs:** 54, 6 per site, 2 at a time, on the experiment instance of browserd (9240) with B live at the lock.
- **Agents:** sealed `claude -p` agents (Sonnet 5, medium effort), with only browserd's tools.
  - **Caps:** 8 minutes or 40 queues a run.
  - **Clearing:** before each run, only its site's origins and cookies are cleared, so each run is a first visit.
    Maps runs alone, since clearing google.com would touch the other run.
- **Truth at a click:**
  - **The reference target:** the one the 4 grid cells around the point share, in a 16 px map taken with the agent's
    screenshot.
  - **Where the click landed:** the trusted `pointerdown` it caused.
  - **Needed stops:** a stop is needed when the target at the stop differs from the reference's.
- **Decision rules, fixed before the run:**
  - **Needless stops:** the setting-dependent needless-stop rate's run-clustered 95% upper bound must not exceed Part
    1's held-out false-alarm upper bound (12.3%).
  - **Misses:** judged only with 60 or more clicks onto a changed target; below that, Part 2 only describes.

  Failing either unlocks the setting: "not validated in use".
- **Harm guards:**
  - **Ads:** a browser-level request blocker fails ad-network click requests.
  - **Chat:** keys are refused while the focus is in a chat frame.
  - **Bot walls:** a wall ends the run and the site for the day.
  - **Relaunch:** the test Chrome is relaunched every 12 runs, to keep memory flat.
  - **Cleanup:** each run's tabs and session are closed at its end.

## Part 1 results

**The data (run 3):** 76 of 76 trials, no bot walls, no failures.
- 516 windows, 1,544 checks.
- 38,921 scored points: 5,971 unsafe, of which 3,446 were the harness's own clicks.
- 60 windows with a counted unsafe point (minimum 40).
- 28 popup windows on 7 sites (minimum 12 on 4).

**The lab pick, held out site by site:**

| | Misses | False alarms |
|---|---|---|
| All windows | 3.4% (95% upper 10.3%) | 8.1% (95% upper 12.3%) |
| Idle windows | 3.6% | 6.6% (95% upper 10.9%) |
| Checked at 0.5 s | 5.2% | 6.8% |
| Checked at 3 s | 3.3% | 8.7% |
| Checked at 10 s | 2.8% | 8.9% |

- Post-click windows had 11.2% false alarms, and dismiss windows 17.3%: the page is still settling after the
  harness's own click.
- Own-click misses: 2.1% (guardrail 25%).

**By what changed** (unsafe points, and how many the lab pick let through):

| Class | Points | Missed |
|---|---|---|
| Cookie and consent banners (c1) | 271 | 0 |
| Marketing modals (c2) | 56 | 0 |
| Chat panels (c5) | 27 | 0 |
| Ads over the page | 8 | 0 |
| Ads refreshing in place | 3 | 0 |
| New layers, no signature | 443 | 0 |
| Content that shifted | 282 | 26 |
| Other | 76 | 3 |
| The harness's own clicks | 3,446 | 72 |
| A drawing's view changed (reported apart) | 1,359 | 823 |

- **Every popup was caught.** The 29 counted misses came from two sites:
  - **Kayak, 23:** 20 where result cards moved down about 68 px when a sponsored card was inserted, and 3 other
    changes;
  - **Medium, 6:** an article's action icons sliding about 14 px left when their counts vanished.
- **View changes:** the judges called 21 of 30 sampled ones different. The lab pick let 9 of those through. B does not
  guard a canvas's pan and zoom.

**False alarms by site:**
- Allrecipes 26%, over the 25% line: its one allowed known limit.
- Airbnb 19%, Sephora 19% and Maps 12%.
- The other 15 sites: 0 to 8%.

**The lock:**
- **The selection:** on all 19 sites, the cheapest eligible setting is S1 s 24, T 64, F 10%, Fc 75%, with S2c and S6.
  - It costs 0.030 against the lab pick's 0.094.
  - 16 of the 19 folds picked nearly the same setting (s 24 or 32, T 64, F 10%, with S2c and S6).
  - It is the pick at every P and K tried, with S2r in place of S2c and S6 at K 3.
- **Held out, its gain is not sure:** the paired cost difference's 95% interval is −0.015 to 0.100, which includes 0.
- **Locked:** the lab pick. The selection is the setting to try next.

## Part 2 results

**Runs:** 54 of 54, no bot walls.
- 53 replied within the caps.
- One Maps run hit 40 queues: its searches kept landing on other places. It was not a stop loop.
- Median run: 31 s and 6 queue calls, 0.63 clicks per queue.

**Stops** (259 checked clicks):

| | Needed | Unneeded |
|---|---|---|
| Setting-dependent | 4 | 17 |
| Fail-closed | 10 | 4 |

- **Needed:**
  - CNN 5, where dialogs opened and pages reloaded;
  - Forbes 6, where the page reloaded under the click;
  - the Guardian 2, where its support overlay covered the target;
  - Allrecipes 1.
- **Unneeded, setting-dependent:**
  - Maps 7 and tldraw 3: S1 saw the map tiles or canvas redraw under the point;
  - Allrecipes 5: S2 saw its search box and images change;
  - CNN 2: S1.
- **The needless-stop rule failed:** 17 of 196 clicks on an unchanged target, 8.7%, run-clustered 95% upper bound
  13.1%, over the cap of 12.3%. By the plan's rule the setting is **not validated in use**. The rate itself matches
  Part 1's 8.1%; the bound is wide because 196 clicks is few.
- **Misses:** 0 of 4 clicks that went out onto a changed target in the same document. That is under the 60 the rule
  needs, so it is described only, and Part 1's miss rate stands.
- **Other measures:**
  - **Stuck** (3 stops in a row on one target): none.
  - **Key stops:** 9 of 102 key checks, 8 of them on the Guardian.
  - **The race window,** from the re-test to the click reaching the page: median 1.2 ms, at most 6 ms.
  - **Swallowed:** 3 clicks landed on a non-control over a control that was not topmost: an invisible layer already
    there, which B cannot see (below).
  - **Not scored:** 45 clicks with an ambiguous or unmapped reference (28 on Maps), and 11 with an unstable one
    (diagrams.net and tldraw). The agent's capture cannot be taken again.
- **Cost:** 17 needless stops at a median turn of 5.3 s is 91 s over 54 runs, about 1.7 s a run. The checks
  themselves took about 121 ms per click.
- **Ad clicks:** none. The blocker counted 12, but all were ad scripts on CNN and Allrecipes whose URLs carry a click
  URL as a parameter, which its patterns matched. The only effect was a few ads failing to load.
- **Exploratory, not a pre-registered rule:** re-scored offline on the same clicks, Part 1's selection would have made
  the same 14 needed stops, with 14 setting-dependent needless stops instead of 17. That is too few clicks to tell
  the two settings apart.

## Part 2b: the selection in use

**Why:** Part 2 failed the lab pick narrowly. Part 1's selection looked better but was not proven held out, and
Joshua asked for it to be tested in use. The rules were written into the plan before the first run.

**The design:** Part 2 again, with only the setting changed.
- **Setting:** S1 s 24, T 64, F 10%, Fc 75%, with S2c and S6, and no S3.
- **Kept the same:** the 9 sites, tasks, agents, caps, clearing, blocker and cleanup.
- **Runs:** 72, 8 per site rather than 6. Part 2's bound came out 1.5 times its rate at 54 runs, and more runs narrow
  it.
- **Decision rules:**
  - **Within budget:** the needless-stop bound at most 12.3%, the in-use limit Part 2 held the lab pick to. This one
    decides.
  - **As Part 1 predicted:** the bound at most 4.6%, the selection's own Part 1 bound. Reported only, since that bound
    comes from the sites the selection was chosen on and is optimistic.

**Runs:** 72 of 72, all replied within the caps, no bot walls. Median run 32 s, 6 queue calls.

**Stops** (317 checked clicks):

| | Needed | Unneeded |
|---|---|---|
| Setting-dependent | 7 | 15 |
| Fail-closed | 13 | 4 |

- **Needed:** Forbes 8, CNN 6, the Guardian 3, Home Depot 2, Allrecipes 1.
- **Unneeded, setting-dependent:** Maps 8, Allrecipes 6, CNN 1. tldraw had none, against 3 in Part 2.
- **Within budget: passed.** 15 of 231 clicks on an unchanged target, 6.5%, run-clustered 95% upper bound 10.9%,
  under 12.3%.
- **As Part 1 predicted: not met.** 10.9% is over 4.6%. In use, with a third of the runs on canvas apps, the selection
  stops more than its Part 1 figure promised.
- **Misses:** 0 of 7 clicks onto a changed target, under the 60 the rule needs, so described only.
- **Other measures:**
  - stuck: none;
  - key stops: 9 of 109, 6 of them on the Guardian;
  - race window: median 1.2 ms, at most 6.3 ms;
  - swallowed: 1;
  - cost: 15 needless stops at a median turn of 5.5 s is 83 s over 72 runs, about 1.2 s a run.
- **Ad clicks:** none. The blocker's 23 were ad scripts carrying a click URL, as in Part 2.

**The same clicks under both settings** (offline re-scoring, from each check's raw readings):

| Clicks from | Setting | Needed stops | Unneeded stops |
|---|---|---|---|
| Part 2 (lab pick live) | Lab pick | 14 | 21 |
| Part 2 (lab pick live) | Selection | 14 | 18 |
| Part 2b (selection live) | Lab pick | 20 | 19 |
| Part 2b (selection live) | Selection | 20 | 19 |

Unneeded stops here include the 4 fail-closed ones in each run, which stop at every setting.

- **The settings stop almost the same clicks in use.** Each catches every needed stop the other does.
- **The runs differ more than the settings do.** Part 2b's clicks met fewer needless-stop spots under either
  setting, which is why the lab pick failed on Part 2's clicks and the selection passed on Part 2b's.
- **So Part 2b validates the selection in use, and would likely have validated the lab pick too.** Part 1 is where
  the two differ.

## Truth took three runs

The audit failed twice. Each failure was a real truth bug, fixed before Part 1 ran again in full.

1. **Run 1** (252 items; up to 17% errors):
   - GeoGebra keeps its old keyboard, hidden, when it mounts a new one, which shifted every key's place;
   - a Share icon gaining its label read as a change.

   Fixed: places count drawn elements only, and a name arriving is no change.
2. **Run 2** (229 items; up to 24% errors): the reference reading did not match the reference image, in two ways.
   - **Undrawn layers:** HubSpot's cookie banner takes clicks at opacity 0 for about a second before it fades in.
     Truth read "Accept all" where the image showed Pricing. Fixed with the drawn reading (above).
   - **A capture during a mount:** the Guardian's support overlay mounted during a reference capture that predates
     it. 33 of the window's 39 points changed between the readings before and after the capture. Fixed by taking the
     capture again (above).
3. **Run 3 passed** (209 items, 4 judges): 4% errors in "hard" (2 of 50), none elsewhere. Both errors were changes
   inside a cross-origin frame (HubSpot's chat, a Best Buy ad), which truth cannot see by design.

**Undrawn layers are a hazard of their own.** A click aimed at what a screenshot shows can land on an invisible layer
that is already there. B checks what changed since the screenshot, so it cannot see a layer that was already there.
In run 3, 322 reference points were on an undrawn target.

## What this means

This section is judgement drawn from the numbers, not a rule of the plan.

- **B does what it was built for:** no popup, banner, modal, chat panel or new layer got past it, on 19 real sites
  and in 54 agent runs.
- **Its needless stops cluster** on canvas apps (Maps, tldraw), where S1 sees tiles and shapes redraw, and on a few
  busy pages (Allrecipes, Airbnb, Sephora). Elsewhere they are rare. Each costs one turn, about 5 s.
- **Its misses are layout shifts** that keep the old node attached (Kayak's cards, Medium's icons), and canvas pan and
  zoom, which it does not guard.
- **In use, with agents, the two settings are about equal.** Over all 575 Part 2 and 2b clicks, each makes the same
  34 needed stops, and 40 against 37 needless ones: about 7% of good clicks stopped, about 1.5 s a run.
- **Part 1 is where they differ, and it favours the selection:** 0% misses and 3.1% false alarms, against 3.4% and
  8.1%. Those figures come from the sites the selection was chosen on, and held out its gain was not sure.
- **The selection is the better default for building B into browserd.** It passed the in-use rule, is at least as
  good as the lab pick on every measure here, and missed nothing in Part 1. It drops S3 (the top-layer check); no
  top-layer popup got past it in Part 1.

## Limits

- **Cross-origin frames:** truth sees the frame, not what changes inside it (a chat opening, an ad rotating). B's S1
  still sees the pixels.
- **Invisible layers already present** at the screenshot are not a change, so B cannot catch them. There were 3
  swallowed clicks in Part 2.
- **Canvas pan and zoom** is not guarded (823 of 1,359 view-change points let through in Part 1).
- **US visitors** see fewer consent walls than European ones, and sites change daily.
- **Part 1 never moves the mouse,** so hover-only targets are missed there. Part 2's clicks cover them.
- **Truth runs in the page's main world,** as browserd's own page script does. Its effect on pages was not measured.

## Deviations from the plan

- **Checks 3, truth's cost, failed and was kept.** The truth map took up to 371 ms on some Guardian loads and 277 ms on
  a first Kayak read, against 150 ms. The cost is the page's, not the drawn test's: with that test off, the same pages
  cost the same within 15 ms. It adds 0.1 to 0.4 s between a screenshot and the next click in Part 2, next to model
  turns of seconds.
- **Checks 2, clearing,** passed on tldraw, CNN, Forbes, Airbnb and the Guardian. Home Depot, Maps and diagrams.net
  show no first-visit popup, so there was nothing to bring back. Allrecipes' popups need scrolling and were not
  tested.
- **Checks 5, memory:** measured at the end rather than after run 5, since 2 runs overlap.
  - Test-Chrome processes: 8 before, 9 after, since the Chrome was relaunched.
  - Left over: no page, no chrome-devtools-mcp, no agent.
  - Memory: 62% free after, against 64% before.
- **The unstable-reference rule changed after the counted run 2:** from "the reading after the capture is truth" to
  "take the capture again". The audit stratum made for exactly this case caught it.
- **A failed launch's log** (a site list zsh did not split) was overwritten when the run was relaunched. Its traceback
  is kept in the task output.

## Cleanup

- Every session the experiment instance opened (89, most from the earlier click-guard runs) was closed through its
  page, with no tab left.
- The main server's two test-profile sessions from the click-guard experiment were closed.
- The experiment instance (9240) and its test Chrome were stopped after Part 2, started again for Part 2b, and
  stopped again after it, with no session, tab, chrome-devtools-mcp process or agent left. `.run/experiment.json`
  is removed.
- The main server (9230) was not restarted.
