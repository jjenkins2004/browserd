# experiments

## Module TL;DR

Everything that measures browserd rather than being it: nothing here ships (`.gitattributes` leaves `/experiments` out
of release archives), and nothing in `browser/` imports from here. Each folder but `findings/` (the write-ups) holds its
own code and README; the data their runs make (transcripts, scores, recordings) lives outside the repo, since it holds
the user's own browsing.

## Directory Layout

    experiments/
      bench/      the runner, its suites, arms and scoring: browserd against other browser MCP servers, and against
                  variants of itself
      findings/   what each experiment found, one write-up per experiment
      long-tasks/ long, bounded tasks on real sites in the user's own Chrome, for a demo video, with answer keys

## Core Abstractions & Shared Pieces

- Code here that imports browserd's own modules puts the checkout root on the import path itself (long-tasks'
  scripts; `nextserver.py` takes a worktree's); `bench/paths.py`'s `ROOT` is that root for the bench's own use.
- Write-ups in `findings/` name the code they measured by path, from the repo root or relative to `findings/`.

## Agent Gotchas & Invariants (⚠️)

- A path to the repo root from a file here is one level deeper than it looks: `experiments/<folder>/` sits two below
  the root.
- Never commit run data (transcripts, records, screenshots, videos): it holds the user's accounts and browsing.
- Every window and tab the code here opens in a profile's Chrome comes from `opens.window` or `opens.tab`
  (`../browser/chrome/README.md`); in `long-tasks/compare.py` the other arms' own servers open theirs, and
  `bench/run.py`'s `clear_chrome` opens each `chrome` arm's window itself, normal and on screen (`bench/README.md`).
