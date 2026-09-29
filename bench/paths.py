"""Where the bench's code and data are: its code in this folder of the browserd repo, its data in a folder outside the
repo; README.md, "Directory Layout", says what the data folder holds."""
import os
from pathlib import Path

BENCH = Path(__file__).resolve().parent
ROOT = BENCH.parent  # the browserd checkout this bench is in, whose chrome-devtools-mcp the devtools arm runs
DATA = Path(os.environ.get("BROWSERD_BENCH_DATA", ROOT.parent / "browserd-bench")).expanduser().resolve()
RESULTS = DATA / "results"
