#!/usr/bin/env python3
"""Flag WebGames runs whose tool calls read scripts, source or network responses, or name the password.

    python3 experiments/bench/tools/cheats.py wg1
"""
import json, re, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # experiments/bench/, for paths and webgames
import paths  # noqa: E402
import webgames  # noqa: E402

SUSPECT = re.compile(r"\.js\b|document\.scripts|<script|innerHTML|outerHTML|fetch\(|performance\.getEntries|"
                     r"network_request|get_network|browser_network|PASSWORD_|password\s*[:=]|localStorage|sessionStorage|"
                     r"__reactFiber|__reactProps|memoizedProps", re.I)
tasks = webgames.load()
for path in sorted((paths.RESULTS / sys.argv[1]).glob("*/*.jsonl")):
    name = path.stem.rsplit("-r", 1)[0]
    hits = []
    for line in path.read_text().splitlines():
        e = json.loads(line)
        for b in (e.get("message") or {}).get("content") or []:
            if isinstance(b, dict) and b.get("type") == "tool_use":
                text = json.dumps(b["input"])
                if SUSPECT.search(text) or tasks[name]["password"] in text:
                    hits.append("%s %s" % (b["name"].split("__")[-1], text[:220]))
    if hits:
        print("==", path.parent.name, name)
        for h in hits[:4]:
            print("   ", h)
