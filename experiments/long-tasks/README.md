# long-tasks

## Module TL;DR

Long, bounded browser tasks for a demo of browserd: a real agent doing an hour-scale job on real sites, in the user's own
logged-in Chrome (the `personal` profile), ending in Google files, with the tab it works in recorded as a timelapse. Each task names its entities, its sources (pinned
where they can be), its fields and the exact files and slides it ends in, so a longer run means the harness struggled,
not that the model chose to dig deeper. Each task has an answer key wherever the sources hold still.

## Directory Layout

    long-tasks/
      capex/
        prompt.md   the task as the agent gets it: 4 10-Ks, then a Sheet with a chart and a 6-slide deck
        key.py      builds key.json from EDGAR's XBRL facts, each checked against the filing's text
        key.json    the 24 values in $ millions, each company's growth, slide title and lines
      trip/
        prompt.md   the task: 3 cities' nonstop fares on Google Flights and November highs, then a 6-slide deck
        key.py      builds key.json: each city's November high from the pinned Wikipedia revision
        key.json    the 3 November highs; fares are live, so it has none
      run.sh        one run: a clean Claude Code on claude-sonnet-5-5 with the task's prompt, record.py beside it
      record.py     the timelapse: the session's working tab, captured over DevTools, into frames/ and video.mp4

## Core Abstractions & Shared Pieces

- **A task** is a folder with `prompt.md`, which holds everything the agent is told: the shared rules (browserd's
  profile and the session label, only the named sources, "n/a" over another source, exactly the slides listed,
  `tab_needs_input` on a login or captcha, nothing submitted or sent, stop at the end state), then the task itself.
- **A key** is `key.json`, written by the task's `key.py`, never by hand: the values a correct run ends with.
- **A run** is `run.sh <task> <name>`: Claude Code, interactive so its turns can be filmed, started in the run's own
  folder, `<data>/<name>/` (`../browserd-long-tasks` beside the repo, or `$BROWSERD_LONG_TASKS_DATA`), with
  `--setting-sources ""` (none of the user's CLAUDE.md, rules, memory, skills, hooks or plugins), `--tools ""` and
  browserd from `--mcp-config` alone, its tools allowed up front. The login is the user's own, so nothing is set up
  first. The session label `record.py` waits for is read from the prompt's "with the label" line.
- **The working tab** is the one whose record folder (`.run/calls/<profile>/<session>-<label>/<tab>/`) changed last:
  each queue writes there. `record.py` finds the session by its label in `state.db`, maps the tab to its DevTools target
  (`tabs.target`) and captures it on a connection of its own, so browserd never knows and the tab need not be in front.

## Agent Gotchas & Invariants (⚠️)

- The sources are pinned so a key stays right: capex names each 10-K by its accession, trip each Wikipedia page by
  its revision (`oldid`). Fares move by the minute, so no key can hold them. A new filing is a new task
  version: change `FILINGS` in `key.py` and the URLs in `prompt.md` together, then run `key.py` again.
- `--safe-mode` would hide the user's setup too, but it also drops `--mcp-config`'s servers: a run would have no
  browserd. A fresh `CLAUDE_CONFIG_DIR` works but needs its own login.
- A run's folder is never reused: a second run needs a new name, so no recording is overwritten.
- `record.py` imports browserd from this checkout and reads the records `browser/paths.py` names: run it from the
  checkout the server on 9230 runs from, or set `BROWSERD_HOME` to that server's records.
- Keep the profile's Chrome window open, even behind others: a minimized window may stop drawing frames (untested).
- SEC refuses a User-Agent without a contact address: `key.py` sends a placeholder one, `SEC_USER_AGENT` a real one.
