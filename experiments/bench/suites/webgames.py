"""WebGames' base challenges (convergence-ai/webgames, the Hugging Face test split), served locally.

Each challenge shows a password once solved; a run passes when its final message holds that password, as
WebGames' own Inspect scorer checks. The site must be up first:

    python experiments/bench/sites.py start
"""
import json
import urllib.request

import paths

TASKS_FILE = paths.DATA / "webgames-data" / "hf-test.jsonl"
BASE = "http://127.0.0.1:4380"

# Copied verbatim from webgames/evals/inspect_ai_webgames.py.
TASK_PROMPT = "Your task is: {description}. You must go to {homepage} and obtain the password for the game. To prove you have completed the task you must obtain and report the password for the game. If you do not have the password, you have not managed to complete the task."

# The password sits in the site's scripts, so a run could read it there without solving anything.
SYSTEM = ("You are an agent for Browser automation. Solve the challenge through the page as a person would: never "
          "read the site's source code, scripts or network responses to find the password.")
MAX_TURNS = 40


def load():
    """Every challenge, as {id: task}, in the dataset's order."""
    tasks = {}
    for line in TASKS_FILE.read_text(encoding="utf-8").splitlines():
        task = json.loads(line)
        tasks[task["id"].replace("/*", "")] = task
    return tasks


def homepage(task):
    # The maze's route is "maze/*"; its start is /maze/.
    return "%s/%s" % (BASE, task["path"].replace("*", ""))


def prompt(task, token):
    return TASK_PROMPT.format(description=task["description"], homepage=homepage(task))


def score(task, answer, token):
    return {"passed": task["password"] in (answer or "")}


def check():
    """Refuse to start runs while the site is down, or while another site answers on its port."""
    if b"WebGames" not in urllib.request.urlopen(BASE, timeout=5).read():
        raise SystemExit("%s is not WebGames" % BASE)
