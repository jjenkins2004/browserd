# experiments

## Module TL;DR

Everything that measures browserd rather than being it: nothing here ships (`.gitattributes` leaves `/experiments` out
of release archives), and nothing in `browser/` imports from here. Each folder holds its own code and README; the data
its runs make (transcripts, scores, recordings) lives outside the repo, since it holds the user's own browsing.

## Directory Layout

    experiments/
      bench/      the benchmarks against other browser MCP servers: suites, arms, runner, scoring
      findings/   what each experiment found, one write-up per experiment; benchmark.md is bench/'s
      long-tasks/ long, bounded tasks on real sites in the user's own Chrome, for a demo video, with answer keys

## Core Abstractions & Shared Pieces

- Code here reaches browserd's own modules through the checkout root, two folders up (`bench/paths.py`'s `ROOT`).
- Write-ups in `findings/` link the code they measured by relative path (`../bench/`).

## Agent Gotchas & Invariants (⚠️)

- A path to the repo root from a file here is one level deeper than it looks: `experiments/<folder>/` sits two below
  the root.
- Never commit run data (transcripts, records, screenshots, videos): it holds the user's accounts and browsing.
