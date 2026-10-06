#!/usr/bin/env python3
"""Flag WebGames runs whose tool calls read scripts, source or network responses, or name the password.

    python experiments/bench/tools/cheats.py wg1
"""
import json, re, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # experiments/bench/, for paths and the suites
import paths  # noqa: E402
import transcripts  # noqa: E402
from suites import webgames  # noqa: E402

SUSPECT = re.compile(r"\.js\b|document\.scripts|<script|innerHTML|outerHTML|fetch\(|performance\.getEntries|"
                     r"network_request|get_network|browser_network|PASSWORD_|password\s*[:=]|localStorage|sessionStorage|"
                     r"__reactFiber|__reactProps|memoizedProps", re.I)
tasks = webgames.load()
for path in sorted((paths.RESULTS / sys.argv[1]).glob("*/*.jsonl")):
    name = path.stem.rsplit("-r", 1)[0]
    hits = []
    for b in transcripts.blocks(path.read_text(encoding="utf-8", errors="replace")):
        if b.get("type") == "tool_use":
            text = json.dumps(b["input"])
            # The name too: Playwright's, chrome-devtools-mcp's and Claude in Chrome's network tools say so only there.
            if SUSPECT.search(b["name"] + " " + text) or tasks[name]["password"] in text:
                hits.append("%s %s" % (b["name"].split("__")[-1], text[:220]))
    if hits:
        print("==", path.parent.name, name)
        for h in hits[:4]:
            print("   ", h)
