# long-tasks

## Module TL;DR

Long, bounded browser tasks for a demo of browserd: a real agent doing an hour-scale job on real sites, in the user's own
logged-in Chrome (the `personal` profile), ending in Google files. Each task names its entities, its sources (pinned
where they can be), its fields and the exact files and slides it ends in, so a longer run means the harness struggled,
not that the model chose to dig deeper. Each task has an answer key wherever the sources hold still.

## Directory Layout

    long-tasks/
      capex/
        prompt.md   the task as the agent gets it: 4 10-Ks, then a Sheet with a chart and a 6-slide deck
        key.py      builds key.json from EDGAR's XBRL facts, each checked against the filing's text
        key.json    the 24 values in $ millions, each company's growth, slide title and lines

## Core Abstractions & Shared Pieces

- **A task** is a folder with `prompt.md`, which holds everything the agent is told: the shared rules (browserd's
  profile and the session label, only the named sources, "n/a" over another source, exactly the slides listed,
  `tab_needs_input` on a login or captcha, nothing submitted or sent, stop at the end state), then the task itself.
- **A key** is `key.json`, written by the task's `key.py`, never by hand: the values a correct run ends with.

## Agent Gotchas & Invariants (⚠️)

- The sources are pinned so a key stays right: capex names each 10-K by its accession. A new filing is a new task
  version: change `FILINGS` in `key.py` and the URLs in `prompt.md` together, then run `key.py` again.
- SEC refuses a User-Agent without a contact address: `key.py` sends a placeholder one, `SEC_USER_AGENT` a real one.
