# browser/dashboard/preview

## Module TL;DR

A gallery of every state of the browserd page's parts, drawn by the page's own code (`../ui/`) from made-up data:
`python3 -m browser.dashboard.preview` from the repo root, then http://127.0.0.1:9320/. It never reaches the browserd
server or a Chrome. The sidebar lists each part with its number of stories; a part's stories show as frames, each under
its name and `about`. The gallery reloads when a file under `../ui/` or here changes. Design variants in
`variants/` show as extra columns beside the current design, across every story.

## Directory Layout

    preview/
      __main__.py    the gallery's server on 127.0.0.1:9320: /, /data.js, /frame, /version
      gallery.html   sidebar of parts; chosen part's stories as frames, per variant
      harness.js     in each frame: stories(), onPanel(), show(); api answers from the story
      frame.css      a lone part's 16px room; a failed story's error
      variants/      temporary, made when a design is being picked: <name>.css and/or <name>.js

## Core Abstractions & Shared Pieces

**`/frame?file=<part>&story=<name>&variant=<name>`** is one story: `page.css`, the variant's CSS and `frame.css`,
then the scripts of `page.PARTS`, the variant's script, `harness.js`, `samples.js` and `<part>.stories.js`, each
its own inline script, then `show(<name>)`. **`show`** draws a `render` story into `<main>`, with `api` answering
every POST `{}` after 300ms and `refresh` doing nothing; a `state` story gets `api` answering `/state` from it
(`null` throws as a server not answering) and runs the page's `start`.

**`/data.js`** is the gallery's list: `VARIANTS`, `samples.js`, and every stories file wrapped in a function of its
own, the page first, then `page.PARTS`' order. The gallery's own `stories()` keeps each story's name and `about`.
**`/version`** is the newest file or folder time under `../ui/` and here; the gallery reloads when it
changes.

## Agent Gotchas & Invariants (⚠️)

- **A variant is temporary.** To offer design choices, add `variants/<name>.css` (rules after `page.css`) and/or
  `variants/<name>.js` (functions that redefine a part's, run after the parts). Once one is picked, put it into
  `../ui/` and delete every variant.
- **A story shows its state at rest**, with no click: a message a button would leave is put in with `tell()`.
- **The port is 9320**, outside 9223-9299, where `profiles.make` gives profiles' Chromes their ports;
  `python3 -m browser.dashboard.preview <port>` takes another.
- **Never installed**: `.gitattributes` leaves this folder out of every archive GitHub serves, which the installers and
  the formula install from, and the formula removes it from a `--HEAD` build, so no installed module may import it.
- **Stories files are named `<part>.stories.js`**; the gallery lists every one it finds in `../ui/`.
