# browser/tabs

## Module TL;DR

A session's tabs, and the chrome-devtools-mcp process that drives each one. `sessions.py` says what a session id and
label may be and when a session counts as paused; `tabs.Tabs` gives each page of a profile's Chrome a short tab id (but
the placeholder, and pages of another browser context) and says which session owns it; `worker.Worker` pairs one tab
with a chrome-devtools-mcp of its own (`devtools.Devtools`), and `Worker.lock` makes that tab's queues run one at a
time. `../README.md`'s Gotchas bind this folder too: read it first.

## Directory Layout

    tabs/
      sessions.py  session ids, labels, record folder names, when a session is paused
      tabs.py      Tabs: tab ids in state.db; open, list, show, close, hand over
      worker.py    Worker: one tab's process, paired with its page; Workers, one per tab id
      devtools.py  Devtools: an MCP client for one chrome-devtools-mcp over stdio; FILE_ROOTS

## Core Abstractions & Shared Pieces

- **From a tab to its process.** `Tabs` writes each tab as a `state.Tab` row. A queue (`../tools.py`, `_worker`) takes
  the tab's `Worker` from `Workers.get` and holds `Worker.lock` while `ensure()` gives it a running, paired `Devtools`.
- **When a tab's process stops.** Every path that closes a tab or quits its Chrome, and `tab_list` or a queue that
  finds the tab closed, calls `Workers.drop`, which stops the process for good; `Workers.stop_all` stops every one as
  the server stops. A tab that a listing for the browserd page, `/show`, `/close-tab` or `tab_needs_input` finds closed
  keeps its process until one of those. `Workers.pause` stops a paused session's processes but keeps each `Worker`, so
  the next queue's `ensure` starts a new one and reports the uids gone.

## Agent Gotchas & Invariants (⚠️)

- **One chrome-devtools-mcp per tab.** It runs one tool call at a time (one `Mutex` in its `McpServer`, taken by every
  `ToolHandler.handle`), so one shared process would put every tab's queue in one line. Each costs about 180MB.
- **Element uids live in the tab's process.** They stay valid across queues until the page navigates or the element
  goes. When the process died, was paused, or belonged to an earlier server, they are gone, and the next report opens
  saying so (`steps.RESTARTED`).
- **A tab opened by hand belongs to no session** until `Tabs.hand_over` gives it to one; no tool does. A tab closed
  outside browserd is marked closed at the next listing or use.
- **chrome-devtools-mcp's file tools may touch only `devtools.FILE_ROOTS`** (each passed as `--workspace`) **and the
  temporary folder.** `steps.check` refuses a `filePath` or `filePaths` anywhere else, or relative, before any step runs
  (`devtools.may_touch`).
