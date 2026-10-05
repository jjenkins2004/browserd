"""Sessions: one agent's task on one profile. What an id and a label may be, the record folder they name, and when a
session counts as paused.
"""

import random
import re

from .tabs import LETTERS

ID_LENGTH = 6  # longer than a tab id, so the two are never mistaken for each other
LABEL_MOST = 60
PAUSE_AFTER = 30 * 60  # seconds without a call before a session counts as paused
NOT_AN_ID = "%r is not a session id (six characters, like k3f9x2); session_start gives one"


def is_id(value):
    return isinstance(value, str) and len(value) == ID_LENGTH and set(value) <= set(LETTERS)


def new_id():
    return "".join(random.choice(LETTERS) for _ in range(ID_LENGTH))


def label_problem(label):
    """Why a label cannot be used, or None."""
    if not isinstance(label, str) or not label.strip():
        return "label is required: a few words saying what this session is for, like \"apply acme backend\""
    if len(label.strip()) > LABEL_MOST:
        return "a label is at most %d characters: a few words, not a sentence" % LABEL_MOST
    return None


def folder(session):
    """The name of a session's record folder: its id, then its label as lowercase words joined by dashes, cut to 40
    characters, or "session" when the label has no words.

    Args:
        session (Session): whose folder.
    """
    words = re.sub(r"[^a-z0-9]+", "-", session.label.lower()).strip("-")[:40].strip("-")
    return "%s-%s" % (session.id, words or "session")


def paused(session, now):
    return session.closed is None and now - session.last_call > PAUSE_AFTER
