# browser/ui

## Module TL;DR

The browserd page, one file per part, and each part's states. `../page.py` serves `page.html` with `page.css` and
the scripts of `page.PARTS` put in, as one inline script, read again on every load, so an edit shows on reload
with no restart. A part is plain JavaScript functions that build its elements and change them in place; `page.js`
joins the parts into the page and polls `GET /state` every 2s. Each `<part>.stories.js` lists that part's states,
drawn from `samples.js` by the preview (`../../preview/`); the server never sends either.

## Directory Layout

    ui/
      page.html        the shell: page.py puts in __STYLE__, __SCRIPT__ and __TOKEN__
      page.css         the palette's tokens, shared rules, then each part's by name
      base.js          api, el, tell, ago, aged, button: shared by every part
      header.js        browserd, New profile, and server errors only when there are any
      profile_tab.js   a profile's strip tab: name, needs-you and active counts, alarm
      profile.js       a profile's panel: Chrome, Open and Quit Chrome, Delete profile, sessions, idle folded, by hand
      session.js       one session: state, label, id, last call, Close session, its tabs
      tab.js           one Chrome tab's row, with Show, Close, and needs-you note
      by_hand.js       tabs no session owns, each with Show and Close
      new_profile.js   the New profile form, and the folders it may take over
      page.js          start(): the header, the strip, one panel at a time, refresh and poll
      samples.js       made-up tabs, sessions and profiles, for stories only
      *.stories.js     each part's states, for the preview only

## Core Abstractions & Shared Pieces

**A part** is a function that builds its elements: `header(add)`, `profileTab(name, pick)`, `profilePanel(name)` and
`newProfile()` return an object of the elements a later poll changes, and `tabItem`, `sessionBlock` and
`byHandBlock` return the element itself. A `show…` function (`showStatus`, `showProfileTab`,
`showProfile`, `showFolders`) changes a built one in place. A button (Show, Close, Close session, Quit Chrome, Delete profile)
posts through `button()`, then calls `refresh()`, and puts a refusal in the `said` element it was given; the Open
Chrome and New profile forms do the same in their own submit handlers.

**`page.js`** is the only part that reads `/state`, and it holds every built part, with that part's own `key`,
across polls. `start(main)` draws the header, the
strip and the panel and starts the poll; `refresh()` gets `/state`, `showProfiles` keeps one `rows` entry
(`{panel, tab}`) per profile, made once and changed in place, and `showChosen` shows the profile the hash names
(`#Jobs`; `#+new` is the New profile form, which the header's button opens), else the first.

**A stories file** calls `stories(part, about, {name: story})` once. A story is `{about, render}`, where `render()`
returns the element or elements to draw, or, in `page.stories.js` only, `{about, state}`, where `state()` returns
what `/state` answers (`null`: the server is not answering) and the whole page is started on it. `about` says
what the state is and how the page handles it.

## Agent Gotchas & Invariants (⚠️)

- **A new part goes in `page.PARTS`** (`../page.py`), or the page never gets it; the shell calls `start` after
  every part has loaded, so the order there is only the order of reading.
- **Every state of a part is a story in that part's own file**, added in the same change that makes the state.
  `page.stories.js` keeps to a few whole-page cases and leaves each part's edge cases to that part.
- **Every part runs in one inline script** (the page's CSP allows no script file), so a top-level name in one part
  is seen by all; none may take a `window` property's name (`name`, `status`, `open`, `close`, `closed`, `top`).
- **Elements are changed in place, not rebuilt, at each poll**: a panel's body is rebuilt only when what it shows
  changed, so a button being clicked is not swapped out under the pointer.
- **The page says active and idle for a session the server calls `active` and `paused`**: no one paused an idle
  one; its agent just made no call for 30 minutes. Only the labels differ; classes and data keep the server's word.
- **A profile's idle sessions fold** into one line naming them (`idleFold`), newest call first, which a click opens
  and which stays open across rebuilds (`panel.idleOpen`, read off the fold as each rebuild starts). An idle session
  with a tab that needs you stays out with the active ones, so amber is never folded away. Those keep the server's
  order, oldest started first: sorted by last call, two active sessions would swap at almost every poll, under the
  pointer.
- **A session's tab may carry `needs_action: {note, since}`** (an agent's words, and seconds since the epoch):
  the tab is waiting on you. The page draws it on the profile's tab and the tab's row, but the server
  sends no such field yet, and no tool sets or clears it; only the stories show it.
- **Amber means a tab needs you, and nothing else**; an idle session's chip is grey.
- **Stories draw from `samples.js`, never real data**, and may use the preview's `stories()` and `onPanel()`.
