"""The user's focus: which app has it, and bringing one to the front.

system asks the OS; opens.py says when browserd moves the focus, and why.
"""

from .. import system


def front():
    """The pid of the app the user's focus is in, or None when the OS cannot say."""
    return system.front()


def bring(pid):
    """Bring the app with this pid to the front, and return whether the OS let it.

    Args:
        pid (int): the app's process id.
    """
    return system.bring(pid)
