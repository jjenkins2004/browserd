---
name: benchmark
description: Rerun browserd's benchmarks against Playwright MCP, chrome-devtools-mcp and agent-browser (MCP-Universe, FormFactory, botwall, and more) and score them. Use when asked to benchmark browserd, rerun the benchmarks, measure a browserd change on them, or compare browser MCP servers.
---

# Rerun the benchmarks

`experiments/bench/README.md` is the contract: the suites, the arms, the scoring and the rules. Read it first. These are the
steps, in order. `<data>` is the data folder, `../browserd-bench` beside the repo unless `$BROWSERD_BENCH_DATA` says
otherwise.

## 1. Before anything

- Check free memory with `memory_pressure -Q`: a run starts only at 25% free, and other agents on browserd (a batch of
  job applications, say) count against it. Another session's `claude -p` batch makes `run.py` refuse to start: do not
  wait for it, pass `--beside` in each suite's arguments (`"mcpuniverse:--k 2 --beside" formfactory:--beside`) and
  watch memory.
- Never restart the main server on 9230. Never delete a run: set it aside in `<data>/results/_invalid/`.
- Ask the user which suites and arms, and say the cost first. On 2026-09-29 (`final2`), MCP-Universe cost $0.45 a run
  on average ($0.33 Playwright to $0.68 agent-browser; $87 for 4 arms × 48 runs), FormFactory $0.47 a form ($0.19
  browserd to $1.21 agent-browser; $95 for 4 arms × 50) and WebGames $0.65 a run ($0.56 Playwright to $0.77
  agent-browser; $133 for 4 arms × 51); MCP-Universe and FormFactory about 70 to 80 minutes each for 4
  arms, 2 runs at a time, WebGames about 2.5 hours.

## 2. Set up (once, and after a pull)

- `experiments/bench/setup.sh` fills the data folder; it skips what is there.
- `npm ci` in the repo root, for the devtools arm; `npm i -g agent-browser@0.38.1 && agent-browser install` for the
  agentbrowser arm.

## 3. Serve the browserd to measure (the `next` arm)

- Use a worktree of its own, never the main checkout (`nextserver.py` refuses one):
  - an existing bench worktree: `git -C <tree> switch --detach <commit>`;
  - or a new one: `git worktree add ../browserd-bench-tree <commit>`, then
    `ln -s "$PWD/node_modules" ../browserd-bench-tree/node_modules` (its browserd runs chrome-devtools-mcp from there),
    then once `python3 experiments/bench/nextserver.py --tree ../browserd-bench-tree --profile Bench`, which takes over the
    Chrome-Bench folder an earlier worktree made.
- Stop the `nextserver.py` already on 9250, if any, since it keeps running the code it started with:
  `kill $(lsof -tiTCP:9250 -sTCP:LISTEN)`; that quits its Bench Chrome too. Serve one bench worktree at a time.
- Serve it detached, so a closed window does not stop it:
  `python3 -c "import subprocess; subprocess.Popen(['python3', 'experiments/bench/nextserver.py', '--tree', '<tree>'], start_new_session=True, stdin=subprocess.DEVNULL, stdout=open('<data>/nextserver.log', 'a'), stderr=subprocess.STDOUT)"`,
  then wait for `curl -s http://127.0.0.1:9251/` to answer and check `<data>/nextserver.log`'s last line.

## 4. Run

- `experiments/bench/sites.sh start` for a suite with a local site (formfactory, webgames, miniwob, clicks, haystack).
- `experiments/bench/chain.sh <name> <arms> "<suite>[:<run.py args>]"...`, for example
  `experiments/bench/chain.sh final3 next,playwright,devtools,agentbrowser "mcpuniverse:--k 2" formfactory webgames`. Give each
  measurement a `<name>` not yet in `<data>/results/`: a used one resumes that experiment, keeping its done runs from
  whatever commit made them. It detaches itself; progress is in `<data>/results/chain.log` and each suite's
  `<data>/results/<name>-<suite>.log`.
- Check the first run of each arm reached a browser: `python3 experiments/bench/report.py <name>-<suite>` after a few runs, and
  read one transcript's last line. A batch whose runs all end in seconds with no tool call is broken, not fast.

## 5. While it runs

- `stopped (try 1)` in `chain.log` resumes by itself 2 minutes later: leave it. A halt (`chain <name> halts`): read the
  suite's log for the line with "the batch stops", fix the cause, and run the same chain again; done runs are skipped.
- When it stopped on runs that reached no browser ("no browser call succeeded"), set those aside first, as
  `experiments/bench/README.md`, "The stops", says (with their formfactory, miniwob or clicks records): they count as done.
- An outage (wifi, a site down): set aside the runs of every arm in its window, not only the flagged ones, then resume.
- The plan's session limit ("You've hit your session limit"): the run it cut off part way counts as done. Set aside
  every run whose transcript says "session limit", with its record, then resume after the reset or under another
  account.
- The Mac short on memory: stop the batch (kill the chain, its `run.py`, and each `claude -p` run's process group),
  close the cut-off runs' browserd sessions with `run.close_sessions` on their transcripts and their agent-browser
  sessions with `AGENT_BROWSER_SESSION=<run token> agent-browser close`, and resume later.

## 6. Score and report

- `python3 experiments/bench/report.py <exp>` for each suite; `python3 experiments/bench/tools/paired.py <exp>` for sign tests (and
  `--key lenient <exp>` on MCP-Universe); `python3 experiments/bench/tools/analyze.py <exp>` for tool use.
- Report per arm: passed (and the lenient score on MCP-Universe), fields right on FormFactory, median time, turns,
  input tokens, cost, tool errors, and the sign tests. Say plainly when a difference is within noise.
- Write the results into `experiments/findings/benchmark.md`, then `experiments/bench/sites.sh stop` and stop the bench server
  (`kill $(lsof -tiTCP:9250 -sTCP:LISTEN)`) when done.
