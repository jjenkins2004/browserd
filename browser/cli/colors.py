"""Colors for browserd's command: ANSI codes, written only to a terminal, never with NO_COLOR set or TERM=dumb, and on
Windows only once system.ansi has turned them on in the console. Piped output stays plain, which the installers read.
"""

import os
import sys

from .. import system

_CODES = {"bold": "1", "dim": "2", "red": "31", "green": "32", "yellow": "33", "cyan": "36"}


def _on(stream):
    return (stream.isatty() and not os.environ.get("NO_COLOR") and os.environ.get("TERM") != "dumb"
            and system.ansi(stream))


OUT = _on(sys.stdout)
ERR = _on(sys.stderr)


def paint(text, *styles, err=False):
    """text in styles (names of _CODES), for stdout, or stderr when err; text as it is when that stream shows none."""
    if not styles or not (ERR if err else OUT):
        return text
    return "\033[%sm%s\033[0m" % (";".join(_CODES[style] for style in styles), text)
