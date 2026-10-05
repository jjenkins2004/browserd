"""browserd start, stop, restart, status and version against stand-in servers, and where the records go.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import time
import types

from browser import server, system
from browser.cli import service
from browser.config import paths
from browser.dashboard import page
from browser.protocol import mcp
from harness import check, refusal, serving


def service_offline():
    saved = (server.URL, server.PORT, server.ROOT, server.RUN, server.PID_FILE, server.LOG_FILE, service.LOCK_FILE,
             service.time, subprocess.Popen)
    code = paths.ROOT  # the folder holding the browser package
    workdir = tempfile.mkdtemp(prefix="browser-service-")
    stand_ins = []

    def aim(port):
        """Point service at a port, as both the pid it asks the OS about and the URL it asks MCP at."""
        server.PORT, server.URL = port, "http://127.0.0.1:%d%s" % (port, mcp.PATH)

    try:
        # A records folder of the checks' own: never the real server's, so a stop asked here reaches no server but the
        # stand-in (on Windows that folder names the events browserd stop and restart set), and a start here could not
        # run one.
        server.ROOT = server.RUN = workdir
        server.PID_FILE = os.path.join(workdir, "server.pid")
        server.LOG_FILE = os.path.join(workdir, "server.log")
        service.LOCK_FILE = os.path.join(workdir, "start.lock")
        # service's looks at a stopping stand-in a tenth as far apart; its deadlines stay real, as the stand-ins are real
        # processes.
        service.time = types.SimpleNamespace(time=time.time, sleep=lambda seconds: time.sleep(seconds / 10))

        free = mcp.Server("127.0.0.1", 0, [], "nobody")
        aim(free.server_address[1])
        free.server_close()
        check("nothing answering is None", service.answering() is None)

        ours = serving([], server.NAME)
        aim(ours.server_address[1])
        check("the browser MCP server is told apart by its name", service.answering() == server.NAME)
        check("start leaves a running server alone", service.start().startswith("already running"))
        ours.shutdown()
        ours.server_close()

        other = serving([], "someone-else")
        aim(other.server_address[1])
        said = refusal(service.start, SystemExit)
        check("start refuses a port that answers as another program", "someone-else" in said, said)
        other.shutdown()
        other.server_close()

        aim(free.server_address[1])

        class Dies:
            pid = 99999

            def poll(self):
                return 1

        def spawn(argv, **kwargs):
            """The server's spawn writes why it stopped and exits; anything else, such as the lsof start asks first, runs."""
            if "browser.server" not in argv:
                return saved[-1](argv, **kwargs)
            kwargs["stdout"].write("the School Chrome: port 9223 is held by pid 1\n")
            kwargs["stdout"].flush()
            return Dies()

        subprocess.Popen = spawn
        said = refusal(service.start, SystemExit)
        check("a server that dies while starting says why, from its log", "held by pid 1" in said, said)
        subprocess.Popen = saved[-1]

        with open(server.PID_FILE, "w") as handle:
            handle.write(str(os.getpid()))
        check("stop never asks a pid that is not the browser MCP server to stop", service.stop() == "not running")

        ours = serving([], server.NAME)
        aim(ours.server_address[1])
        # A process the OS shows running -m browser.server, listening for browserd stop and restart as the server does
        # (signals on macOS, named events on Windows), which exits 1 when asked to restart and 2 when asked to stop.
        script = ("import os, sys, time\n"
                  "from browser import system\n"
                  "system.listen_for_stop(sys.argv[1], lambda kind: os._exit({'restart': 1, 'stop': 2}[kind]))\n"
                  "print('set', flush=True)\n"
                  "while True:\n"
                  "    time.sleep(0.05)\n")
        said = {}
        for name, code_wanted in (("restart", 1), ("stop", 2)):
            stand_in = subprocess.Popen([sys.executable, "-c", script, workdir, "-m", "browser.server"], cwd=code,
                                        env=dict(os.environ, PYTHONPATH=code), stdout=subprocess.PIPE, text=True)
            stand_ins.append(stand_in)
            assert stand_in.stdout is not None
            ready = stand_in.stdout.readline()  # it listens
            with open(server.PID_FILE, "w") as handle:
                handle.write(str(stand_in.pid))
            try:
                said[name] = getattr(service, name)()
            except SystemExit as exc:
                said[name] = "refused: %s" % exc
            try:
                exited = stand_in.wait(5)
            except subprocess.TimeoutExpired:
                exited = None
            check("%s asks the server to %s and waits for it to exit" % (name, name),
                  ready.strip() == "set" and exited == code_wanted, "%r, exit %r, %r" % (ready, exited, said[name]))
        check("restart then starts the server again, which here answers already",
              said["restart"].startswith("restarted, every Chrome and session kept\nalready running  pid ")
              and "  MCP        %s\n" % server.URL in said["restart"], said["restart"])
        ours.shutdown()
        ours.server_close()
    finally:
        for stand_in in stand_ins:
            if stand_in.poll() is None:
                stand_in.kill()
            stand_in.wait()
            if stand_in.stdout:
                stand_in.stdout.close()
        (server.URL, server.PORT, server.ROOT, server.RUN, server.PID_FILE, server.LOG_FILE, service.LOCK_FILE,
         service.time, subprocess.Popen) = saved
        shutil.rmtree(workdir, ignore_errors=True)


def paths_offline():
    """Where the records go: .run in a checkout, the user's own folder in an installed copy, BROWSERD_HOME over both;
    and browserd status and version."""
    saved = (paths.ROOT, os.environ.get("BROWSERD_HOME"), server.PORT, server.URL, server.RUN)
    workdir = tempfile.mkdtemp(prefix="browser-paths-")
    check("ROOT is the folder holding VERSION and the browser package, however deep paths.py sits",
          all(os.path.isfile(os.path.join(paths.ROOT, *name)) for name in (["VERSION"], ["browser", "server.py"])),
          paths.ROOT)
    try:
        os.environ.pop("BROWSERD_HOME", None)
        paths.ROOT = workdir
        check("an installed copy keeps its records in the user's own folder", paths._run() == system.DATA, paths._run())
        with open(os.path.join(workdir, ".git"), "w") as handle:
            handle.write("gitdir: elsewhere\n")  # a worktree's
        check("a checkout keeps its records in .run beside the code", paths._run() == os.path.join(workdir, ".run"),
              paths._run())
        os.environ["BROWSERD_HOME"] = os.path.join("~", "records")
        check("BROWSERD_HOME names the records folder over either, ~ expanded",
              paths._run() == os.path.join(os.path.expanduser("~"), "records"), paths._run())
        check("the version is read from VERSION, and unknown without one", paths.version() == "unknown")
        with open(os.path.join(workdir, "VERSION"), "w") as handle:
            handle.write("1.2.3\n")
        check("browserd version names the version and the folder",
              service.version() == "browserd 1.2.3 (%s)" % service._home(workdir), service.version())

        free = mcp.Server("127.0.0.1", 0, [], "nobody")
        server.PORT = free.server_address[1]
        server.URL = "http://127.0.0.1:%d%s" % (server.PORT, mcp.PATH)
        free.server_close()
        server.RUN = workdir
        check("browserd status says not running, the ports start serves on, and where the records are",
              service.status() == "not running\n  MCP        %s\n  dashboard  http://127.0.0.1:%d/\n  records    %s"
              % (server.URL, page.PORT, service._home(workdir)), service.status())
    finally:
        paths.ROOT, home, server.PORT, server.URL, server.RUN = saved
        if home is None:
            os.environ.pop("BROWSERD_HOME", None)
        else:
            os.environ["BROWSERD_HOME"] = home
        shutil.rmtree(workdir, ignore_errors=True)
