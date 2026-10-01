# browserd

The browser MCP server and everything it needs: it owns one Chrome per profile and serves the tools a
Claude Code agent reads and drives pages with. Nothing here knows what a job is; it is handed a tab, and
records each `queue` call in that tab's record folder, `calls/<profile>/<session>-<label>/<tab>/` in its records
folder. An agent starts with `session_start {profile, label}`, and sees only its session's tabs; asked to, it makes or
deletes a profile with `profile_new` or `profile_delete`.

## Install

It runs on macOS and Windows 11, with the same tools, page and behaviour. Each needs Google Chrome, Python 3.10
or later (on Windows the python.org or Store one; not MSYS2's), and Node 20.19 or later for chrome-devtools-mcp.

macOS, with Homebrew, which brings Python and Node with it:

    brew tap jjenkins2004/browserd https://github.com/jjenkins2004/browserd
    brew install browserd

macOS, without Homebrew's formula (into `~/.local/share/browserd`, with `browserd` in `~/.local/bin`):

    curl -fsSL https://raw.githubusercontent.com/jjenkins2004/browserd/main/install.sh | sh

Windows, from PowerShell (into `%LOCALAPPDATA%\Programs\browserd`, with its `bin` added to your PATH):

    irm https://raw.githubusercontent.com/jjenkins2004/browserd/main/install.ps1 | iex

Then, from any terminal:

    browserd setup
    browserd start

`browserd setup` asks for the MCP and dashboard ports, Enter keeping 9230 and 9231, and prints a line to paste
to your agent (Claude Code or any other MCP client), which registers browserd itself. Run it again to change
the ports.

Running an installer again updates browserd, and a server that was running is restarted on the new version
with every Chrome and session kept; after `brew upgrade browserd`, run `browserd restart`. Each installer's
header lists its settings (a version to pin, where it goes); `browserd uninstall` removes what it installed.

## Use

    browserd setup    the ports, 9230 for MCP and 9231 for the dashboard unless changed, and the line to
                      paste to your agent; a running server restarts on new ones
    browserd start    the server, in the background; each profile's Chrome starts on its first use. The
                      dashboard, http://127.0.0.1:9231/, makes profiles and shows their sessions and tabs
    browserd stop     stops the server, which quits every profile's Chrome with it and closes every session
    browserd restart  restarts the server alone: every Chrome keeps running and every session stays open
    browserd status   whether the server is running, on which ports, and where its records are
    browserd version  which browserd this is, and where it is installed
    browserd uninstall  stops the server and removes browserd and its command, after asking, and prints the
                      line for your agent to remove it; the records and every profile's Chrome folder stay

Its records (server.pid, server.log, start.lock, state.db with the profiles, sessions and tabs, ports.json,
devtools-*.log, calls/, and downloads/<profile>/, where each profile's Chrome saves its downloads, with no Save As
window) go in `~/Library/Application Support/browserd` on a Mac and `%LOCALAPPDATA%rowserd` on Windows,
whichever version runs; `BROWSERD_HOME` names another folder.

## From a clone

A git checkout runs as it is, and keeps its records in `.run/` beside the code: `npm ci` once, then
`./browserd start`, or on Windows `browserd start` from its folder (browserd.cmd, from cmd or PowerShell);
`./browserd` also runs in Git Bash. `browserd uninstall` refuses a checkout. A release is cut with
`scripts/release 0.2.0`, which tags it, pushes it, and points the Homebrew formula at it.

## Layout

    browserd, browserd.cmd  the command: browser/cli/service.py's start, stop, restart, status, version and uninstall
    browser/                the server, a folder per domain (chrome/, tabs/, steps/, dashboard/, cli/, ...), and its own
                            README, whose Directory Layout maps them; browser/system/ is what differs by OS
    package.json            chrome-devtools-mcp, pinned; `npm ci` once, into node_modules/
    VERSION                 the version on main, which install.sh and install.ps1 install as its tag, v<VERSION>
    install.sh, install.ps1 the macOS and Windows installers, run from GitHub; Formula/browserd.rb is Homebrew's
    scripts/release         cuts a release: VERSION, the tag, and the formula's archive and sha256
    .run/                   gitignored, a checkout's records: server.pid, server.log, start.lock, state.db (profiles, sessions, tabs, needs_input), ports.json, devtools-*.log, calls/<profile>/<session>-<label>/<tab>/
    tests/                  check_browser.py, check_server.py; throwaway.py, the live checks' own Chrome
    preview/                every state of the page's parts from made-up data: `python3 preview/preview.py`, then http://127.0.0.1:9320/
    experiments/            what measures browserd: bench/, the benchmarks against other browser MCP servers; long-tasks/, the demo tasks; findings/, what each found
    .claude/skills/         benchmark: a rerun's steps, for an agent

`browser/README.md` is the one to read before changing any of it: the Chrome proof, the tab ids, the
queue's checked steps, and what each tool refuses. Agents connect over HTTP at `http://127.0.0.1:9230/mcp`, or the port `browserd setup` chose.
After a restart that changed the tools, a Claude Code session already open lists them again at its next
browserd call and has them from its next turn; one that connected to a browserd from before
`tools.listChanged` needs `/mcp` to reconnect once. `browser/README.md` says why.

## Tests

    python3 tests/check_browser.py    framing, a profile's Chrome proof, launch; each OS's own owner and launch checks
    python3 tests/check_server.py     protocol, tab ids, sessions, focus, queue, recording, profiles, page, service

On Windows, `py -3 tests\check_browser.py` and `py -3 tests\check_server.py`.

The live groups start a Chrome of their own on a new folder and a free port, and quit it after, so
they never touch a profile's Chrome; `check_server.py`'s queue checks need `npm ci` done.
