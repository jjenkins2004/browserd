"""The two ports browserd serves on: the MCP server's, which agents connect to, and the dashboard's (page.py). 9230 and
9231 unless browserd setup saved others in the records folder's ports.json, which each server and command reads as it
starts.
"""

import json
import os

from . import paths

HOST = "127.0.0.1"  # both servers listen on this machine alone
MCP_DEFAULT, PAGE_DEFAULT = 9230, 9231
LEAST, MOST = 1024, 65535  # below 1024 a port needs administrator rights on a Mac
FILE = os.path.join(paths.RUN, "ports.json")


def load():
    """The saved ports, (mcp, page): the defaults when none are saved, or when ports.json does not hold two different
    ports browserd may use."""
    try:
        with open(FILE) as handle:
            saved = json.load(handle)
        mcp, page = saved["mcp"], saved["page"]
    except (OSError, ValueError, KeyError, TypeError):
        return MCP_DEFAULT, PAGE_DEFAULT
    if problem(mcp) or problem(page) or mcp == page:
        return MCP_DEFAULT, PAGE_DEFAULT
    return mcp, page


def problem(port):
    """Why port can be no port of browserd's, or None."""
    if not isinstance(port, int) or isinstance(port, bool) or not LEAST <= port <= MOST:
        return "a port is a whole number from %d to %d" % (LEAST, MOST)
    return None


def save(mcp, page):
    os.makedirs(paths.RUN, exist_ok=True)
    with open(FILE, "w") as handle:
        json.dump({"mcp": mcp, "page": page}, handle)
        handle.write("\n")


MCP, PAGE = load()
