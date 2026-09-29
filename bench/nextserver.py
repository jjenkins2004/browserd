"""browserd's server from a worktree, run beside the main one on ports of its own, with that worktree's own .run/ for
its state, logs and records, so the main server (9230) is never restarted. Only the ports differ from the tree's code.

    python3 bench/nextserver.py --tree PATH [--port N]      serve, in the foreground: MCP on N, the page on N+1
    python3 bench/nextserver.py --tree PATH --profile NAME  first add a profile (its own Chrome folder and port) there

The tree is a git worktree of its own, never the checkout the main server runs from, whose .run/ it would share; the
port defaults to 9250.
"""
import argparse
import os
import sys

BENCH_PORT = 9240  # the first Chrome port a bench profile may take

parser = argparse.ArgumentParser()
parser.add_argument("--tree", required=True)
parser.add_argument("--port", type=int, default=9250)
parser.add_argument("--profile")
args = parser.parse_args()
args.tree = os.path.abspath(args.tree)
if os.path.isdir(os.path.join(args.tree, ".git")):  # a worktree has a .git file; a main checkout, a folder
    raise SystemExit("%s is a main checkout, whose .run/ its own server uses; serve a worktree of its own (git "
                     "worktree add)" % args.tree)
sys.path.insert(0, args.tree)
os.chdir(args.tree)
from browser import page, profiles, server  # noqa: E402
from browser.state import State  # noqa: E402

server.PORT, page.PORT = args.port, args.port + 1
server.URL = "http://%s:%d/mcp" % (server.HOST, server.PORT)
server.PAGE_URL = "http://%s:%d/" % (server.HOST, page.PORT)

if args.profile:
    os.makedirs(server.RUN, exist_ok=True)
    state = State(server.STATE_FILE)
    # The main server gives its own profiles' Chrome the first free ports from profiles.FIRST_PORT up, so a bench
    # profile takes one from BENCH_PORT up, where a profile the main server makes later does not land on it. Its
    # Chrome folder is one per Mac, so a worktree after the first takes over the folder an earlier one made.
    folder = profiles.PREFIX + args.profile
    taken_over = folder if folder in profiles.free_folders(state.profiles()) else None
    print(profiles.make(state, args.profile, folder=taken_over, reserved=range(profiles.FIRST_PORT, BENCH_PORT)))
    state.close()
else:
    server.serve()
