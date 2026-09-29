"""MiniWoB++'s form and widget tasks (Farama-Foundation/miniwob-plusplus), two seeds each, served by mwserver.py.

A run passes when its episode's raw reward, as the task's own script gave it, is above 0. The server must be up:

    python3 mwserver.py
"""
import json
import urllib.request

import paths

REWARDS = paths.RESULTS / "miniwob-rewards"
BASE = "http://127.0.0.1:4390"
SEEDS = (1, 2)

# The tasks that fill a form or drive a widget: text boxes, dates and times, selects, checkboxes, autocompletes,
# sliders and spinners, multi-step forms, an email client, a terminal, a rich-text editor.
TASKS = [
    "book-flight", "buy-ticket", "choose-date", "choose-list", "click-checkboxes", "click-checkboxes-large",
    "click-checkboxes-soft", "click-option", "click-tab-2", "copy-paste", "copy-paste-2", "email-inbox",
    "email-inbox-forward-nl", "enter-date", "enter-password", "enter-text", "enter-text-2", "enter-text-dynamic",
    "enter-time", "form-sequence", "form-sequence-2", "form-sequence-3", "login-user", "login-user-popup",
    "multi-layouts", "multi-orderings", "navigate-tree", "order-food", "phone-book", "search-engine",
    "sign-agreement", "social-media", "terminal", "text-editor", "text-transform", "use-autocomplete",
    "use-slider", "use-spinner",
]

SYSTEM = "You are an agent for Browser automation."
MAX_TURNS = 25
PROMPT = ("Go to {url}. The page shows an instruction at its top; carry it out on the page. The task counts only "
          "once, so do it carefully. When you have done it, reply with the single word DONE.")


def load():
    return {"%s-s%d" % (task, seed): {"task": task, "seed": seed} for task in TASKS for seed in SEEDS}


def url(task, token):
    return "%s/miniwob/%s.html?seed=%d&run=%s" % (BASE, task["task"], task["seed"], token)


def prompt(task, token):
    return PROMPT.format(url=url(task, token))


def score(task, answer, token):
    path = REWARDS / ("%s.jsonl" % token)
    records = [json.loads(line) for line in path.read_text().splitlines() if line.strip()] if path.exists() else []
    reward = records[0]["reward"] if records else None
    return {"passed": reward is not None and reward > 0, "reward": reward}


def check():
    urllib.request.urlopen("%s/miniwob/login-user.html" % BASE, timeout=5).read()
