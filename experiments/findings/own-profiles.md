# Findings: browserd against Playwright MCP, chrome-devtools-mcp and Claude in Chrome on the user's own Chrome profiles

Run 2026-10-06 with `../bench/` (`pub3`). It follows `final2` in [benchmark.md](benchmark.md), with Claude in Chrome
in agent-browser's place, MiniWoB++ run in full for the first time, and every arm on a headed Chrome of the user's
own. The same four arms on the long tasks are in [long-tasks-full1.md](long-tasks-full1.md).

## Setup

A Windows 11 machine, model `claude-sonnet-5-5` (Claude Code 2.1.289, pinned), the arms as `../bench/README.md` ("An
arm") gives them:
- browserd: the main server on profile research, on 50e461f's code throughout, restarted at 16:26, mid-WebGames
  (`browser/` next changed at 16:42, in edits committed at 16:48 that it never loaded);
- Playwright MCP 0.0.82 over `--cdp-endpoint` on profile lt-capex's Chrome;
- chrome-devtools-mcp 1.9.0 over `--browserUrl` on lt-parks's;
- Claude in Chrome (Claude Code's `--chrome`) on lt-trip's, through its Claude extension.

Runs went in rounds, one task on all four arms at once. MCP-Universe 24 tasks × 2, FormFactory 50 forms, WebGames 51
challenges and MiniWoB++ 38 tasks × 2 seeds: 900 runs, $66.86 in all, in the data folder's `results/pub3-mcpuniverse/`,
`pub3-formfactory/`, `pub3-webgames/` and `pub3-miniwob/`.

## Results

| Passed | browserd | Playwright | devtools | Claude in Chrome |
|---|---|---|---|---|
| MCP-Universe, strict (of 48) | **38** | 31 | 32 | **38** |
| MCP-Universe, lenient (of 46) | 41 | 37 | 40 | **42** |
| FormFactory (of 50) | 29 | 29 | **30** | 22 |
| FormFactory, fields right | **94%** | **94%** | **94%** | 62% |
| WebGames (of 51) | **47** | **47** | 45 | 41 |
| MiniWoB++ (of 76) | **76** | 73 | 74 | 71 |

| Across the four suites | browserd | Playwright | devtools | Claude in Chrome |
|---|---|---|---|---|
| Median time | 13s | 13s | **11s** | 31s |
| Total time | **91 min** | 123 min | 96 min | 145 min |
| Total cost | $15.57 | **$13.85** | $17.43 | $20.00 |

Playwright's total time counts its two runs that timed out at 900s (one on MCP-Universe, one on WebGames); their cost
was not recorded and is left out.

- **browserd** passed the most or tied for it on MCP-Universe's strict score, WebGames and MiniWoB++ (the only perfect
  score), and was within one form of the best on FormFactory. Against Playwright and devtools every difference is
  within noise (MCP-Universe strict: browserd better on 6 tasks and worse on 3 against devtools, p=0.508; 6 and 2
  against Playwright, p=0.289).
- **Claude in Chrome** tied browserd on MCP-Universe (4 tasks each way) and fell behind it beyond noise on FormFactory
  (browserd better on 7 forms, worse on none, p=0.016) and WebGames (6 and 0, p=0.031); on MiniWoB++, 5 and 0, just
  within noise (p=0.062). It never uploads FormFactory's sample PDF, since Claude Code lets it read only files a
  session was given (`/add-dir`), so on 12 forms it stopped to ask instead of submitting; on G12's two it would not
  type a Social Security number.
- **Playwright and devtools** ran about as fast as browserd here, as Playwright did in `final2`; devtools, at 39s to
  browserd's 19s on `final2`'s WebGames, took 12s to its 15s here. Playwright timed out on WebGames' brick-buster
  (twice, the second run kept) running game loops of its own writing, of up to 20,000 iterations, and on one
  MCP-Universe run a `browser_evaluate` never returned.
- `../bench/tools/cheats.py` flagged Playwright listing network requests on combination-lock and file-credentials, and
  devtools reading `innerHTML`'s length on block-stack; all three runs failed.

## Runs set aside

Two batches before `pub3` (the data folder's `results/_invalid/pub1-covered-windows/` and `pub2-hidden-tab/`):
- `pub1`: VS Code, maximized, covered the other three arms' Chromes, and Chrome draws no covered or minimized window,
  so Playwright's and Claude in Chrome's screenshots and wheel input waited for frames that never came (139 Playwright
  tool calls timed out on FormFactory alone). Fixed in the setup (`../bench/README.md`): the windows are normal and
  cascaded, the screen was kept clear, and the three Chromes were restarted with
  `--disable-features=CalculateNativeWinOcclusion --disable-backgrounding-occluded-windows`, which keeps a covered
  window's screenshots working but not its wheel input.
- `pub2`: Claude in Chrome works in a tab of its own, behind the blank tab the runner leaves, and Chrome draws only a
  window's front tab. Fixed in a231c73: the blank tab closes once the run's own appears.

Within `pub3`, set aside and run again:
- every Chrome quit at 16:26, as the browserd server was restarted (by another session, it seems): the WebGames round
  then running (button-hold, all four arms), in `results/_invalid/pub3-webgames-chrome-outage/`;
- four Claude in Chrome MiniWoB++ runs whose `select_browser` Claude Code's auto-mode safety check refused while its
  model was unavailable ("temporarily unavailable (timed out)"), in `results/_invalid/pub3-miniwob-classifier-outage/`.

## Caveats

- One run a task (two on MCP-Universe), so a few tasks' difference is noise.
- Each arm had a persistent profile of its own, the other three signed in to Google and browserd's research not, and
  the runner resets no site's state between runs.
- FormFactory's file fields are served optional since 27ace9c, so F13, G13 and H11 differ from `final2`'s; and the
  model is `claude-sonnet-5-5`, not `final2`'s `claude-sonnet-5`.
- A timing-sensitive game may differ from a headless run.
