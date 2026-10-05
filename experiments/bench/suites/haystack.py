"""Find a needle in a long page: a staff handbook of 40 or 80 sections, one fact hidden in a middle section. A 40-section
page's view is about 57,000 characters, an 80-section page's about twice that.

Three needles, each on two seeds and both page lengths: a locker code in a sentence (code), the one staff row whose badge expired in 2019
(row), and the one section whose safety officer is also its deputy (twin). The page must be up first:

    python experiments/bench/sites.py start
"""
import re
import urllib.request

from suites import hayserver

BASE = "http://127.0.0.1:%d" % hayserver.PORT
SEEDS = (1, 2)
QUESTIONS = {
    "code": "What locker holds the spare key for visitors to the %s depot? Answer with the locker code only."
            % hayserver.NEEDLE_CITY,
    "row": "Exactly one staff member listed on this page has a badge that expired in 2019; every other badge expires "
           "in 2026 or later. What is that person's full name? Answer with the name only.",
    "twin": "In exactly one section of this page, the safety officer and the deputy are the same person. What is "
            "that section's number? Answer with the number only.",
}
TASK_PROMPT = "Open {url}. It is a long staff handbook. {question}"
SYSTEM = "You are an agent for Browser automation. Find every answer on the page, not from memory."
MAX_TURNS = 30


def load():
    return {"%s-%d-s%d" % (needle, sections, seed): {"needle": needle, "seed": seed, "sections": sections}
            for needle in QUESTIONS for sections in (40, 80) for seed in SEEDS}


def prompt(task, token):
    url = "%s/?seed=%d&needle=%s&sections=%d" % (BASE, task["seed"], task["needle"], task["sections"])
    return TASK_PROMPT.format(url=url, question=QUESTIONS[task["needle"]])


def score(task, answer, token):
    expected = hayserver.page(task["seed"], task["needle"], task["sections"])[2]
    found = re.search(r"(?<![\w-])%s(?![\w-])" % re.escape(expected), answer or "", re.I)
    return {"passed": found is not None, "expected": expected}


def check():
    """Refuse to start runs while the page is down, or while another site answers on its port."""
    if b"Staff Handbook" not in urllib.request.urlopen(BASE + "/?seed=1&needle=code", timeout=5).read():
        raise SystemExit("%s is not the haystack page" % BASE)
