"""This bench's own probe, not a public benchmark: can each arm use a live site known for bot checks?

Each task opens one site, searches it and names the first result. A run passes when it reports a first result and
no block. Answers are not checked against the live site; report.py's table says who got through, and a run's
transcript shows what the page showed.
"""
import json

SITES = {
    "amazon": ("https://www.amazon.com/", "wireless headphones"),
    "bestbuy": ("https://www.bestbuy.com/", "usb-c cable"),
    "booking": ("https://www.booking.com/", "Lisbon"),
    "costco": ("https://www.costco.com/", "olive oil"),
    "crunchbase": ("https://www.crunchbase.com/", "Anthropic"),
    "etsy": ("https://www.etsy.com/", "ceramic mug"),
    "g2": ("https://www.g2.com/", "project management"),
    "homedepot": ("https://www.homedepot.com/", "cordless drill"),
    "indeed": ("https://www.indeed.com/", "software engineer"),
    "lowes": ("https://www.lowes.com/", "garden hose"),
    "nike": ("https://www.nike.com/", "running shoes"),
    "reddit": ("https://www.reddit.com/", "mechanical keyboards"),
    "stackoverflow": ("https://stackoverflow.com/", "python list comprehension"),
    "target": ("https://www.target.com/", "coffee maker"),
    "ticketmaster": ("https://www.ticketmaster.com/", "Lakers"),
    "upwork": ("https://www.upwork.com/", "python developer"),
    "walmart": ("https://www.walmart.com/", "paper towels"),
    "wayfair": ("https://www.wayfair.com/", "desk lamp"),
    "zillow": ("https://www.zillow.com/", "Seattle, WA"),
    "glassdoor": ("https://www.glassdoor.com/", "Stripe"),
}

SYSTEM = ("You are an agent for Browser automation. If a site shows a bot check, captcha or access-denied page that "
          "you cannot get past, say so rather than guessing.")
MAX_TURNS = 20
PROMPT = """Go to {url}, search the site for "{query}", and report the title of the first result.

Answer with JSON only, no code fences: {{"blocked": <true if a bot check, captcha or access-denied page stopped you, else false>, "first_result": "<the first result's title, or null>"}}"""


def load():
    return {name: {"url": url, "query": query} for name, (url, query) in SITES.items()}


def prompt(task, token):
    return PROMPT.format(**task)


def score(task, answer, token):
    try:
        text = (answer or "").strip().strip("`").strip()
        decoded = json.loads(text[4:] if text.startswith("json") else text)
    except ValueError:
        return {"passed": False, "blocked": None}
    blocked = bool(decoded.get("blocked"))
    return {"passed": not blocked and bool(decoded.get("first_result")), "blocked": blocked}
