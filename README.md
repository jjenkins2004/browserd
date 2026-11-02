# browserd

A browser MCP server for Claude Code. Each profile gets its own real Chrome and logins, and agents work in background
tabs while you keep using your computer. Free and open source ([MIT](LICENSE)), for macOS and Windows 11.

**In total across three long jobs on real sites, it finished 31 to 39% faster and cost 34 to 68% less than Playwright MCP, chrome-devtools-mcp and Claude in Chrome. [See the numbers.](#how-it-compares)**

https://github.com/user-attachments/assets/95165ce0-9e91-463f-8455-488fe74efeb2

*Cut from a real run. The Claude Code window and dashboard were rebuilt for the video, and no real booking happened.*

## Install

You need Google Chrome. Homebrew installs everything else; the other installers need Python 3.10+ and Node 20.19+.

macOS, with Homebrew:

    brew tap jjenkins2004/browserd https://github.com/jjenkins2004/browserd
    brew install browserd

macOS, without Homebrew:

    curl -fsSL https://raw.githubusercontent.com/jjenkins2004/browserd/main/install.sh | sh

Windows, from PowerShell:

    irm https://raw.githubusercontent.com/jjenkins2004/browserd/main/install.ps1 | iex

Then connect Claude Code, from a new terminal:

    claude mcp add --scope user browserd -- browserd mcp

Other MCP clients can run `browserd mcp` over stdio, or connect to `http://127.0.0.1:9230/mcp` while browserd is running.

## First run

In a new Claude Code session, ask for a profile:

    make a browserd profile called work

Open the dashboard at `http://127.0.0.1:9231/`, press Open Chrome on that profile, and sign in to the sites the agent
will use. Then name the profile in your request:

    use browserd's work profile to pull last month's invoices into a Sheet

## What it does

- Profiles keep work, personal and test accounts apart from each other and from your everyday Chrome. Sign in once and
  the logins stay.
- If a page opens a popup, Chrome takes your focus for a moment and browserd gives it back.
- Each agent sees only its own tabs, and agents on one profile share its logins.
- The `queue` tool runs a list of steps (click, type, screenshot) in one call and stops at the first failure, so agents
  make fewer tool calls.
- Before a click at screen coordinates, browserd checks that the words the agent expects are there, so a popup in the way
  stops the click. Agents can skip the check, as they do on a map or canvas.
- The dashboard shows every agent's tabs and manages profiles. When a tab needs you for a sign-in or captcha, the agent
  flags it there with a note.

## How it compares

Three long jobs on real sites, three runs per tool, all on `claude-sonnet-5-5`: a spreadsheet and deck from ten 10-Ks, a
map of 28 national parks, and an offsite Doc comparing six cities.

<img src="assets/long-tasks.svg" alt="browserd, Playwright MCP, chrome-devtools-mcp and Claude in Chrome. Runs passed, of 9: 8, 7, 9, 8. Total time: 193, 315, 285, 278 minutes. Total cost: $43.81, $66.03, $138.48, $76.09. Tool calls: 1,107, 2,089, 8,320, 3,256.">

That's 36 runs with no significance test. Chrome opened minimized, which slows Playwright MCP and chrome-devtools-mcp but not
browserd, so their times may be inflated by up to 8.1 minutes a run for chrome-devtools-mcp and 2.5, not counting its
clicks, for Playwright MCP (Claude in Chrome wasn't measured). At those limits, browserd's lead on total time falls to
9% over chrome-devtools-mcp and 34% over Playwright MCP.

On four public benchmarks (900 runs), browserd ties Playwright MCP and chrome-devtools-mcp: every difference in tasks
passed is within noise.

<img src="assets/public-benchmarks.svg" alt="Tasks passed by browserd, Playwright MCP, chrome-devtools-mcp and Claude in Chrome. MCP-Universe strict, of 48: 38, 31, 32, 38. MCP-Universe lenient, of 46: 41, 37, 40, 42. FormFactory, of 50: 29, 29, 30, 22. WebGames, of 51: 47, 47, 45, 41. MiniWoB++, of 76: 76, 73, 74, 71. Total time: 91, 123, 96, 145 minutes. Total cost: $15.57, $13.85, $17.43, $20.00.">

- browserd beats Claude in Chrome beyond noise on FormFactory (p=0.016) and WebGames (p=0.031). Part of the FormFactory
  gap is that Claude Code wouldn't let Claude in Chrome read the sample PDF it had to upload.
- Playwright MCP's $13.85 leaves out two runs that timed out, which its total time includes.

Both comparisons ran on an earlier commit; setup and caveats are in [long-tasks-full1.md](experiments/findings/long-tasks-full1.md)
and [own-profiles.md](experiments/findings/own-profiles.md). The transcripts stay private because they hold my accounts.
Vercel's agent-browser was only in [an earlier run](experiments/findings/benchmark.md), and the Playwright CLI and local
models haven't been tested.

## Limits and safety

- browserd doesn't filter what pages say, so a page can try to instruct your agent (prompt injection), and the agent can
  use every login in its profile. Give each task a profile with only the logins it needs.
- Sign in yourself: everything an agent types, and every screenshot, stays in plain files on your machine until you
  delete them.
- browserd listens only on 127.0.0.1, web pages can't drive it, and it sends nothing off your machine. What your agent
  reads goes to its model provider.

## Use

    browserd start      start the server in the background
    browserd stop       stop the server and quit every profile's Chrome
    browserd restart    restart the server; every Chrome and its tabs stay open
    browserd status     whether it's running, its ports, and where its records are
    browserd setup      change the ports (9230 for MCP, 9231 for the dashboard)
    browserd uninstall  remove browserd; your records and profiles stay

To update, run the installer again, or `brew upgrade browserd` then `browserd restart`.

Records are in `~/Library/Application Support/browserd` on a Mac and `%LOCALAPPDATA%\browserd` on Windows. They hold the
server's log and state, every `queue` call, and each profile's downloads.
