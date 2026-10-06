# browser/chrome

## Module TL;DR

Each profile's Chrome, from the outside: the proof that what answers on a profile's port is that profile's Chrome
(`cdp.require`), the browser-wide connection every caller talks to it through (`cdp.Browser`), starting and quitting it
(`launch`, `chromes.Chromes`), every window and tab it opens, kept off the user's focus (`opens`), the profiles
themselves (`profiles`), and where its downloads go (`downloads`). `../README.md`'s Gotchas bind this folder too: read
it first.

## Directory Layout

    chrome/
      cdp.py        require, the proof; Browser, one browser-wide websocket; owner, listener, check_folder
      chromes.py    Chromes: the server's one keeper of every profile's Chrome
      opens.py      every new window and tab; what a page opens put back; Show

## Core Abstractions & Shared Pieces

- **`cdp.Browser(profile)`** is the one way to talk to a profile's Chrome, and it calls `require` first. A `CdpError`'s
  message is written for an agent or the browserd page to read.
- **A connection per listener.** Code that listens for events opens a `cdp.Browser` of its own; its docstring says
  why.
- **Threads.** This code runs on the MCP server's and the browserd page's request threads, on one `opens.watch` thread
  and one `downloads.Folder` thread per running Chrome, and on one `downloads.Watcher` thread per tab with a process.
  So `Chromes` takes a lock per Chrome folder to start or quit it, `opens.tab` one to open the placeholder, and
  `profiles.make` one to pick a port.
- **`launch.launch`** is also called by the checks' throwaway Chrome (`../../tests/throwaway.py`) and
  `check_browser.py`, so a switch every profile's Chrome needs goes in `launch._open`.
- **Downloads**: a Chrome's `downloads.Folder` (started by `Chromes`) sets where it saves and hears where each file
  went; each tab's `downloads.Watcher` (started by its `Worker`, `../tabs/`) hears what the tab begins and asks the
  Folder; `steps.run` reports it after each step (`../steps/`).

## Agent Gotchas & Invariants (⚠️)

- **An answer on a profile's port proves nothing by itself.** Every connection goes through `cdp.require`, which
  refuses, saying what is wrong and what to do, unless the port's one listener is the Chrome that holds the profile's
  folder (`system.chrome_owner`, `../system/README.md`), started with `INPUT_FLAG`, with no Chrome profile in its folder
  but `Default`. chrome-devtools-mcp's own connection (`--browser-url`) skips `require`, so each queue proves the Chrome
  first through `Tabs.target`.
- **`require` proves the Chrome, not the tab.** An Incognito, Guest or other Chrome profile's window is another
  browser context: `Tabs` neither lists such a tab nor gives it an id. Chrome's default context is its last-used Chrome
  profile, and `Local State` reaches disk seconds after a Chrome profile is added, so for those seconds a new Chrome
  profile's tab would pass. No Chrome profile is ever to be added to a profile's folder.
- **A Chrome with no window open has no Chrome profile loaded.** Chrome keeps running after its last window closes (a
  Mac keeps every app running; on Windows the debugging port keeps it, measured) and unloads its Chrome profile, so
  `Target.getBrowserContexts` names no default context. With no page open either, `Tabs` lists no tabs rather than
  refusing, and `opens.tab` loads it again. A Chrome with pages open but no default context is still refused, since
  its tabs cannot be told apart.
- **Every window and tab is opened in `opens.py`, and only the browserd page's Open Chrome and Show bring one forward.**
  The checks (`../../tests/`) and the experiments (`../../experiments/`) keep this too, but for the bench's `chrome`
  arms, whose windows `run.clear_chrome` opens and puts on screen itself (`../../experiments/bench/README.md`).
  `opens.window` asks Chrome for a minimized window, in the background where the OS needs it
  (`system.BACKGROUND_WINDOWS`). `launch` starts Chrome with no window (`--no-startup-window`), so the tabs it had open
  when it last quit do not come back. On Windows it starts Chrome with `SW_SHOWMINNOACTIVE`, so windows open minimized
  without showing on screen first (`system.launch_chrome`). On a Mac, Chrome raises itself over the app in front when it
  shows a window, which macOS allows at launch, even under `open -g`, and afterwards only once Chrome has been in front;
  so Show brings it forward through the OS (`system.bring`). Chrome still shows a tab a page opens (a `target=_blank`
  link, `window.open`), and `opens.from_page` puts back what it did on screen.
- **Only `check_server.py --headed` puts windows on screen.** The offline checks hold `opens.py`, `launch.py` and the
  OS's window code to what they ask of Chrome and the OS (`focus_offline`, `owning_windows`, `owning_mac`), but the
  live checks run headless by default, so whether a real Chrome then shows a window or takes the focus is seen only
  headed. Ask the user for that run (`../../tests/README.md`).
- **Every profile's Chrome starts with `--allow-pre-commit-input` (`cdp.INPUT_FLAG`).** Without it Chrome holds a
  page's input until the page first draws, and it never draws a background tab by itself, so such a tab drops every key
  press and click while `Input.dispatchKeyEvent` and `dispatchMouseEvent` report success (measured: 40 of 40 first key
  presses on fresh background tabs dropped). `require` refuses a Chrome started without it, naming its pid to quit.
- **`tab_open` never opens its tab in a window of its own.** Chrome prerenders a Google search page in each new window,
  and when Puppeteer attaches that page first, a new chrome-devtools-mcp lists no page for the tab and its queues cannot
  pair it (`../../experiments/findings/benchmark.md`, "The pairing bug: a tab chrome-devtools-mcp never lists"). So
  `opens.tab`, in a Chrome with no page of its Chrome profile open, first opens `opens.PLACEHOLDER` in a window and puts
  the tab beside it.
- **Nothing but a profile's `downloads.Folder` calls `Browser.setDownloadBehavior`.** Chrome keeps one download
  behaviour per browser, the last setter's, so any other setter undoes the Folder's (its docstring says how it keeps
  it).
