#!/usr/bin/env python3
"""Fill the bench's data folder (paths.DATA): each benchmark's own repo at the commit this bench was built against,
WebGames' site built, WebGames' task file, a venv with Flask for ffserver.py, and the file a form run uploads; then
fetch the playwright arm's pinned package and say what the devtools arm still needs. Safe to run again: it skips
what is there.

    python experiments/bench/setup.py
"""
import hashlib
import os
import shutil
import subprocess
import sys
import urllib.request
import venv

import paths
import procs

REPOS = {  # folder: (repo, commit)
    "MCP-Universe": ("https://github.com/SalesforceAIResearch/MCP-Universe.git", "48b4530"),
    "webgames": ("https://github.com/convergence-ai/webgames", "309866f"),
    "formfactory": ("https://github.com/formfactory-ai/formfactory", "b7ef0d6"),
    "miniwob-plusplus": ("https://github.com/Farama-Foundation/miniwob-plusplus", "33c3b4d"),
}
TOOLS = {"git": "git-scm.com", "pnpm": "npm i -g pnpm@9", "npx": "comes with Node.js"}  # tool: how to get it
WEBGAMES_TASKS = "https://huggingface.co/datasets/convergence-ai/webgames/resolve/main/test.jsonl"
WEBGAMES_SHA256 = "d76d51fffb6e69dba399f658a1fca80b0501703d7e46e20fd6848f8d81a3a03c"


def run(argv, cwd=None):
    subprocess.run(procs.command(argv), cwd=cwd, check=True)


def main():
    missing = ["%s (%s)" % (tool, how) for tool, how in TOOLS.items() if shutil.which(tool) is None]
    if missing:
        raise SystemExit("setup needs on PATH: %s" % "; ".join(missing))
    for folder in ("results", "webgames-data", "assets"):
        (paths.DATA / folder).mkdir(parents=True, exist_ok=True)
    upload = paths.DATA / "assets" / "sample.pdf"  # formfactory.UPLOAD
    if not upload.exists():
        shutil.copy(paths.BENCH / "suites" / "sample.pdf", upload)

    for folder, (repo, commit) in REPOS.items():
        if not (paths.DATA / folder / ".git").exists():
            # LF as committed, whatever this git's core.autocrlf or core.eol, so every OS runs the same files.
            run(["git", "clone", "-q", "-c", "core.autocrlf=false", "-c", "core.eol=lf", repo,
                 str(paths.DATA / folder)])
        run(["git", "-C", str(paths.DATA / folder), "checkout", "-q", commit])

    site = paths.DATA / "webgames" / "webgames"
    if not (site / "dist").exists():
        run(["pnpm", "i"], cwd=site)
        run(["pnpm", "build"], cwd=site)
    tasks = paths.DATA / "webgames-data" / "hf-test.jsonl"
    if not tasks.exists():
        tasks.write_bytes(urllib.request.urlopen(WEBGAMES_TASKS, timeout=60).read())
    if hashlib.sha256(tasks.read_bytes()).hexdigest() != WEBGAMES_SHA256:
        raise SystemExit("%s is not the task file this bench was built against" % tasks)

    python = procs.venv_python(paths.DATA / ".venv")
    if not (os.path.exists(python) and subprocess.run([python, "-c", "import flask"], capture_output=True).returncode == 0):
        venv.create(paths.DATA / ".venv", with_pip=True)
        run([python, "-m", "pip", "install", "-q", "Flask==2.3.3"])

    run(["npx", "-y", "@playwright/mcp@0.0.82", "--help"])  # the playwright arm's pinned version, fetched before any run
    if shutil.which(str(paths.ROOT / "node_modules" / ".bin" / "chrome-devtools-mcp")) is None:
        print("the devtools arm needs browserd's own: npm ci, in %s" % paths.ROOT)
    print("data folder ready: %s" % paths.DATA)


if __name__ == "__main__":
    sys.exit(main())
