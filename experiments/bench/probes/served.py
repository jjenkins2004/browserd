"""What the probes share: this checkout's browserd modules on the import path, and the Bench profile of the worktree
nextserver.py serves, named by BENCH_TREE."""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]

from browser.state import State  # noqa: E402


def bench_profile():
    """The Bench profile of the worktree BENCH_TREE names, the one experiments/bench/nextserver.py --tree serves."""
    state = State(os.path.join(os.environ["BENCH_TREE"], ".run", "state.db"))
    try:
        return state.profile("Bench")
    finally:
        state.close()
