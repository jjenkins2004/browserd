"""One browser tool call's record in a job's workspace, under run/.

README.md, "Core Abstractions & Shared Pieces", has how a call is numbered. The folder is handed over by the
caller — `../../setup-workspace <job id>` is what makes one and prints its path.
"""

import json
import os
import re
import threading

RUN = "run"
NUMBERED = re.compile(r"(\d+)-", re.ASCII)
_numbering = threading.Lock()


class Call:
    def __init__(self, workspace, tool):
        """Number a new call after every call already in the workspace's run/, and hold that number.

        Args:
            workspace (str): the workspace's path, as ../../setup-workspace prints it.
            tool (str): the tool called, which names the call's files: <n>-<tool>.json and <n>-<tool>.txt.
        """
        run = os.path.join(workspace, RUN)
        os.makedirs(run, exist_ok=True)
        self._tool = tool
        with _numbering:
            taken = [int(found.group(1)) for found in map(NUMBERED.match, os.listdir(run)) if found]
            self._prefix = os.path.join(run, "%03d-" % (max(taken, default=0) + 1))
            # Made while the lock is held, so the next call counts this number as taken.
            open(self.path(tool + ".json"), "x").close()

    def path(self, name):
        """Where a file of this call's goes: run/<n>-<name>."""
        return self._prefix + name

    def asked(self, arguments):
        """Write what the call was asked, as <n>-<tool>.json."""
        with open(self.path(self._tool + ".json"), "w", encoding="utf-8") as handle:
            json.dump(arguments, handle, indent=2, ensure_ascii=False)
            handle.write("\n")

    def answered(self, text):
        """Write what came back, as <n>-<tool>.txt."""
        with open(self.path(self._tool + ".txt"), "w", encoding="utf-8") as handle:
            handle.write(text.rstrip("\n") + "\n")
