"""One browser tool call's record: its numbered files in the folder the caller names.

tools.queue_steps says which folder the queue records into.
"""

import json
import os
import re
import threading

NUMBERED = re.compile(r"(\d+)-", re.ASCII)
_numbering = threading.Lock()


class Call:
    def __init__(self, folder, tool):
        """Number a new call after every call already in folder, and hold that number.

        Args:
            folder (str): where the call's files go; made when missing.
            tool (str): the tool called, which names the call's files: <n>-<tool>.json and <n>-<tool>.txt.
        """
        os.makedirs(folder, exist_ok=True)
        self._tool = tool
        with _numbering:
            taken = [int(found.group(1)) for found in map(NUMBERED.match, os.listdir(folder)) if found]
            self._prefix = os.path.join(folder, "%03d-" % (max(taken, default=0) + 1))
            # Made while the lock is held, so the next call counts this number as taken.
            open(self.path(tool + ".json"), "x").close()

    def path(self, name):
        """Where a file of this call's goes: <folder>/<n>-<name>."""
        return self._prefix + name

    def asked(self, arguments):
        """Write what the call was asked, as <n>-<tool>.json."""
        with open(self.path(self._tool + ".json"), "w", encoding="utf-8", newline="\n") as handle:
            json.dump(arguments, handle, indent=2, ensure_ascii=False)
            handle.write("\n")

    def answered(self, text):
        """Write what came back, as <n>-<tool>.txt."""
        with open(self.path(self._tool + ".txt"), "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text.rstrip("\n") + "\n")
