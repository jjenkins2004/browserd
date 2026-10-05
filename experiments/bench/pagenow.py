"""Suite pagenow: tasks on replicas of real pages, each made so that a queue step fails early, to measure what a
stopped queue's reply should show of the page now (../findings/page-now.md). The apps are pnserver.py's, which must be
up first:

    python3 pnserver.py

Each app logs its state to results/pn-logs/<token>.jsonl, one event per line with run, scenario, kind and t (epoch ms)
from the page, and at (epoch s) and attempt from the server; score reads the run's latest attempt with its answer.
"""
import json
import re
import urllib.request

import pnserver  # pyright: ignore[reportMissingImports]  (run from this folder, as run.py is)

BASE = "http://127.0.0.1:%d" % pnserver.PORT
# consent's page is on another host name than its consent frame (127.0.0.1), so the frame is from another site.
SITE = "http://localhost:%d" % pnserver.PORT
SYSTEM = "You are an agent for browser automation."
MAX_TURNS = 60
SESSIONS = True  # run.py keeps each run's Claude Code session, which pnfork.py forks at its stops
PIXELS = ("This page is worked from screenshots of the viewport: press with move_at, click_down and click_up at points "
          "read off them. ")

PROMPTS = {
    "helpdesk": ("Open {url}. It is our support desk. Four billing tickets need escalating: the ones from Ana Ruiz, "
                 "Tom Becker, Wei Chen and Fatima Noor. Assign each of those four tickets to Priya Shah and set its "
                 "priority to Urgent. Change nothing else. When you are done, reply with the four ticket numbers."),
    "checkout": ("Open {url}, our store's checkout, and place the order for delivery to:\n\nJane Doe\n200 Bay Street, "
                 "Apt 1201\nToronto, Ontario M5J 2J2\nCanada\nPhone 416-555-0199, email jane.doe@example.com\n\nUse "
                 "Express shipping and the discount code SPRING15. Pay with the card 4242 4242 4242 4242, expiry 12/29, "
                 "security code 123, name on card Jane Doe. Reply with the order number."),
    "export": ("Open {url}, our revenue dashboard. Generate the Q3 2026 revenue report and reply with the EMEA net "
               "revenue the generated report states."),
    "deck": ("Open {url}, a slide deck in our presentation editor. " + PIXELS + "On slide 4 (Budget): change the title "
             "to \"Q3 Budget Review\" and make the title's text color \"dark green 2\" from the Text color palette; then "
             "change the second bullet to \"Travel: $4,200\" and give that bullet the highlight color \"yellow\" from "
             "the Highlight color palette. Leave everything else as it is. Reply DONE when all four changes are made."),
    "consent": ("Open {url}. " + PIXELS + "Read the article and reply with the full name of the scientist it quotes "
                "in its last paragraph."),
}
URLS = {name: BASE for name in PROMPTS}
URLS["consent"] = SITE
URLS["deck"] = SITE
# helpdesk: the four tickets the task names, by requester, and their numbers.
ESCALATE = {"Ana Ruiz": 4832, "Tom Becker": 4823, "Wei Chen": 4814, "Fatima Noor": 4806}
EMEA_NET = "4812350"  # the export report's EMEA net revenue, 5,134,870 - 322,520
DARK_GREEN_2, YELLOW = "#38761d", "#ffff00"


def load():
    return {name: {"app": name} for name in PROMPTS}


def prompt(task, token=None):
    return PROMPTS[task["app"]].format(url="%s/%s?run=%s" % (URLS[task["app"]], task["app"], token))


def prepare(task, mcp_url, session, token):
    """Start the run's attempt anew, so a run started again starts from a fresh page; the browserd session goes
    unused."""
    urllib.request.urlopen(urllib.request.Request("%s/reset?run=%s" % (BASE, token), data=b"", method="POST"),
                           timeout=10).read()


def events(token):
    """The run's logged events of the server's latest attempt for it, in the order the server got them."""
    latest = pnserver._attempts().get(token, 0)
    return [e for e in pnserver.events(token) if e.get("attempt", 0) == latest]


def _digits(text):
    return re.sub(r"\D", "", text or "")


def _helpdesk(logged, answer):
    states = [e["tickets"] for e in logged if e.get("kind") == "state"]
    if not states:
        return {"passed": False, "why": "no state logged"}
    first = {}
    for tickets in states:  # each ticket as it first appeared: the seed's, or as it arrived with the first save
        for ticket in tickets:
            first.setdefault(ticket["id"], ticket)
    last = {ticket["id"]: ticket for ticket in states[-1]}
    wrong = [number for number in ESCALATE.values()
             if last[number]["assignee"] != "Priya Shah" or last[number]["priority"] != "Urgent"
             or last[number]["status"] != first[number]["status"]]
    stray = [number for number, ticket in last.items() if number not in ESCALATE.values()
             and any(ticket[k] != first[number][k] for k in ("status", "priority", "assignee"))]
    unnamed = [number for number in ESCALATE.values() if str(number) not in (answer or "")]
    return {"passed": not wrong and not stray and not unnamed, "wrong": wrong, "stray": stray, "unnamed": unnamed}


def _checkout(logged, answer):
    orders = [e for e in logged if e.get("kind") == "order"]
    rebuilds = [e.get("by") for e in logged if e.get("kind") == "rebuild"]
    if not orders:
        return {"passed": False, "why": "no order placed", "rebuilds": rebuilds}
    data, order = orders[-1]["data"], orders[-1]["order"]
    norm = {k: (v or "").strip().lower() for k, v in data.items()}
    checks = {
        "email": norm.get("email") == "jane.doe@example.com",
        "country": norm.get("country") == "canada",
        "name": norm.get("first") == "jane" and norm.get("last") == "doe",
        "address": "200 bay" in norm.get("address1", "") and "1201" in norm.get("address1", "") + norm.get("address2", ""),
        "city": norm.get("city") == "toronto",
        "province": norm.get("region") == "ontario",
        "postal": norm.get("postal", "").replace(" ", "") == "m5j2j2",
        "phone": _digits(norm.get("phone")).endswith("4165550199"),
        "shipping": norm.get("shipping") == "express",
        "discount": orders[-1].get("code") == "SPRING15",
        "card": _digits(norm.get("card")) == "4242424242424242",
        "expiry": _digits(norm.get("expiry")) == "1229",
        "cvc": _digits(norm.get("cvc")) == "123",
        "cardname": norm.get("cardname") == "jane doe",
        "answer": order in (answer or ""),
    }
    return {"passed": all(checks.values()), "failed": [k for k, ok in checks.items() if not ok],
            "orders": len(orders), "rebuilds": rebuilds}


def _export(logged, answer):
    named = EMEA_NET in re.sub(r"[,\s.$]", "", answer or "")
    return {"passed": named, "ready": any(e.get("kind") == "ready" for e in logged),
            "generated": sum(e.get("kind") == "generate" for e in logged)}


def _deck(logged, answer):
    states = [e["slides"] for e in logged if e.get("kind") == "state"]
    if not states:
        return {"passed": False, "why": "no state logged"}
    first, last = states[0], states[-1]
    slide = last[3]
    checks = {
        "title": [line["text"] for line in slide["title"]] == ["Q3 Budget Review"],
        "title_color": all(line["color"] == DARK_GREEN_2 for line in slide["title"]),
        "bullet": [line["text"] for line in slide["body"]] == ["Venue: $12,800", "Travel: $4,200", "Catering: $6,450",
                                                               "Contingency: $2,000"],
        "highlight": [line["highlight"] for line in slide["body"]] == [None, YELLOW, None, None],
        "others": all(last[i] == first[i] for i in range(len(first)) if i != 3),
        "done": "DONE" in (answer or ""),
    }
    return {"passed": all(checks.values()), "failed": [k for k, ok in checks.items() if not ok],
            "popover": [e.get("phase") for e in logged if e.get("kind") == "popover"]}


def _consent(logged, answer):
    return {"passed": "amara okonkwo" in (answer or "").lower(),
            "consent": next((e.get("choice") for e in logged if e.get("kind") == "consent"), None)}


SCORERS = {"helpdesk": _helpdesk, "checkout": _checkout, "export": _export, "deck": _deck, "consent": _consent}


def score(task, answer, token=None):
    """passed, and what the app's log says of how the run went; each app's own checks are in its _<app>."""
    return SCORERS[task["app"]](events(token), answer)


def check():
    """Refuse to start runs while the apps are down, or while another site answers on their port."""
    try:
        page = urllib.request.urlopen(BASE + "/helpdesk", timeout=5).read()
    except OSError as error:
        raise SystemExit("the page-now apps are not up on %s (%s): start experiments/bench/pnserver.py" % (BASE, error))
    if b"Northwind Support" not in page:
        raise SystemExit("%s is not the page-now apps' server" % BASE)
