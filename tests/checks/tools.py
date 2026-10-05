"""profile_new and profile_delete.
"""

import json
import os
import shutil
import tempfile
import time

from browser.chrome import chromes, profiles
from browser.records.state import State
from browser.tabs.tabs import Tabs
from browser.tabs.worker import Workers
from browser.tools import profile_tools
from harness import FakeChrome, call, check, open_session, serving, unsynced


def profile_tools_offline():
    """profile_new and profile_delete over HTTP, with a stand-in Google folder and Chrome."""
    saved = (profiles.GOOGLE, profiles.FIRST_PORT)
    workdir = tempfile.mkdtemp(prefix="browser-profile-tools-")
    state = unsynced(State(os.path.join(workdir, "state.db")))

    class Quitting:
        quits = []

        def quit(self, profile):
            self.quits.append(profile.name)

    chromes = Quitting()
    httpd = serving(profile_tools(state, chromes, Tabs(state, FakeChrome().connect), Workers(workdir), ()))
    try:
        google = profiles.GOOGLE = os.path.join(workdir, "Google")
        os.makedirs(google)
        profiles.FIRST_PORT = profiles.LAST_PORT - 40
        text, is_error = call(httpd, "profile_new", name="Jobs")
        made = state.profile("Jobs")
        check("profile_new makes a profile in a new folder, Chrome-<name>, and points at session_start",
              not is_error and made is not None and made.folder == os.path.join(google, "Chrome-Jobs")
              and os.path.isdir(made.folder) and "session_start" in text, text)
        text, is_error = call(httpd, "profile_new", name="jobs")
        check("a name taken, whatever its case, is refused", is_error and "already" in text, text)
        text, is_error = call(httpd, "profile_new", name="no good")
        check("a name against the rule is refused, giving the rule", is_error and profiles.NAME_RULE in text, text)
        assert made is not None
        session = open_session(state, "apply acme", made)
        text, is_error = call(httpd, "profile_delete", name="Jobs")
        check("profile_delete refuses while the profile has an open session, naming it, and changes nothing",
              is_error and session.id in text and state.profile("Jobs") == made
              and state.session(session.id).closed is None and chromes.quits == [], text)
        state.close_session(session.id, time.time())
        with open(os.path.join(made.folder, "Local State"), "w") as handle:  # as its Chrome, once run, leaves it
            json.dump({"profile": {"info_cache": {"Default": {"name": "Default"}}}}, handle)
        text, is_error = call(httpd, "profile_delete", name="jobs")
        check("with none open, it quits the profile's Chrome and removes the profile, keeping its folder",
              not is_error and state.profile("Jobs") is None and chromes.quits == ["Jobs"] and os.path.isdir(made.folder)
              and "kept" in text, text)
        text, is_error = call(httpd, "profile_new", name="Jobs")
        again = state.profile("Jobs")
        check("profile_new with the same name takes that folder back over",
              not is_error and again is not None and again.folder == made.folder and "taking over" in text, text)
        text, is_error = call(httpd, "profile_delete", name="Nobody")
        check("profile_delete refuses a profile there is not", is_error and "no profile named 'Nobody'" in text, text)
    finally:
        profiles.GOOGLE, profiles.FIRST_PORT = saved
        httpd.shutdown()
        httpd.server_close()
        state.close()
        shutil.rmtree(workdir, ignore_errors=True)
