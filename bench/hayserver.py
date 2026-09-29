#!/usr/bin/env python3
"""The haystack page on 127.0.0.1:4396: a long field-office handbook (40 or 80 sections, each a heading, paragraphs
and a staff table), built the same for a seed, with one needle in a middle section.

    python3 hayserver.py

GET /?seed=<n>&needle=<code|row|twin>&sections=<40|80> serves the page. haystack.py builds the tasks and their answers from the same
seed with page(), so the answer never has to be read back from the server.
"""
import html
import http.server
import random
import urllib.parse

PORT = 4396
FIRST = ["Ada", "Bram", "Cleo", "Dag", "Esme", "Finn", "Greta", "Hugo", "Ines", "Jonas", "Kaia", "Lars", "Mira",
         "Nils", "Oda", "Per", "Rune", "Sigrid", "Tor", "Ulla", "Vera", "Wim", "Ylva", "Zeno"]
LAST = ["Aasen", "Berg", "Dahl", "Eide", "Fjeld", "Haug", "Holm", "Lie", "Moe", "Nygard", "Rud", "Strand", "Vik",
        "Lund", "Bakke", "Hagen", "Myhre", "Solberg", "Tangen", "Wold", "Aune", "Brekke", "Dale", "Engen", "Foss",
        "Grande", "Hovland", "Kvam", "Lien", "Ness"]
CITIES = ["Bergen", "Bodø", "Drammen", "Ålesund", "Hamar", "Harstad", "Kristiansand", "Lillehammer", "Molde",
          "Narvik", "Porsgrunn", "Sandnes", "Skien", "Stavanger", "Tønsberg", "Trondheim", "Arendal", "Gjøvik",
          "Halden", "Kongsberg"]
TOPICS = ["Arrival and badges", "Vehicle bookings", "Cold-weather gear", "Radio checks", "Sample storage",
          "Visitor escort", "Waste handling", "First aid kits", "Night shifts", "Fuel logs", "Boat launches",
          "Drone flights", "Freezer alarms", "Key cabinets", "Weather holds", "Road closures"]
FILLER = ["Staff sign the daily sheet before leaving the yard, and again on return.",
          "Every trip over two hours is logged with its route and expected return time.",
          "Equipment that fails a check is tagged red and moved to the repair shelf.",
          "The duty lead reviews the log each evening and files it with the regional desk.",
          "Questions about this procedure go to the office manager first, then to the region.",
          "Seasonal changes to this procedure are announced at the Monday briefing.",
          "Contractors follow the same rules as staff while on site, without exception.",
          "Copies of the current form are kept in the binder by the main door."]
NEEDLE_CITY = "Tromsø"  # named nowhere else on the page


def page(seed, needle, sections_count=40):
    """(title, sections, answer): the handbook for a seed, each section {heading, paragraphs, officer, deputy, rows},
    with the needle placed in a middle section, and what a run must answer."""
    rng = random.Random("%s-%s-%s" % (seed, needle, sections_count))
    people = ["%s %s" % (first, last) for first in FIRST for last in LAST] * 2
    rng.shuffle(people)
    sections = []
    for n in range(1, sections_count + 1):
        city = rng.choice(CITIES)
        officer, deputy = people.pop(), people.pop()
        while deputy == officer:  # only the twin needle's section has one person twice
            deputy = people.pop()
        rows = [(people.pop(), city, "%d%03d" % (rng.randint(2, 8), rng.randint(0, 999)),
                 "%04d-%02d-%02d" % (rng.randint(2026, 2029), rng.randint(1, 12), rng.randint(1, 28)))
                for _ in range(rng.randint(8, 12))]
        sections.append({"heading": "%d. %s, %s office" % (n, rng.choice(TOPICS), city),
                         "paragraphs": rng.sample(FILLER, 3), "officer": officer, "deputy": deputy, "rows": rows})
    middle = sections[rng.randint(sections_count * 45 // 100, sections_count * 55 // 100)]
    if needle == "code":
        code = "%d-%s" % (rng.randint(1000, 9999), "".join(rng.choice("QXZKJW") for _ in range(2)))
        middle["paragraphs"].insert(1, "Visitors to the %s depot collect the spare key from locker %s at the front "
                                       "desk." % (NEEDLE_CITY, code))
        answer = code
    elif needle == "row":
        at = rng.randrange(len(middle["rows"]))
        name, city, extension, _ = middle["rows"][at]
        middle["rows"][at] = (name, city, extension, "2019-%02d-%02d" % (rng.randint(1, 12), rng.randint(1, 28)))
        answer = name
    else:
        middle["deputy"] = middle["officer"]
        answer = middle["heading"].split(".")[0]
    return "Northern Field Offices: Staff Handbook", sections, answer


def render(seed, needle, sections_count=40):
    title, sections, _ = page(seed, needle, sections_count)
    out = ["<!doctype html><meta charset=utf-8><title>%s</title><body style='font-family:sans-serif;max-width:900px;"
           "margin:auto'><h1>%s</h1>" % (html.escape(title), html.escape(title))]
    for section in sections:
        out.append("<section><h2>%s</h2>" % html.escape(section["heading"]))
        out.extend("<p>%s</p>" % html.escape(text) for text in section["paragraphs"])
        out.append("<p>Safety officer: %s. Deputy: %s.</p>" % (html.escape(section["officer"]),
                                                                html.escape(section["deputy"])))
        out.append("<table border=1><tr><th>Name</th><th>Office</th><th>Extension</th><th>Badge expires</th></tr>")
        out.extend("<tr>%s</tr>" % "".join("<td>%s</td>" % html.escape(cell) for cell in row)
                   for row in section["rows"])
        out.append("</table></section>")
    return "".join(out) + "</body>"


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        body = render(query.get("seed", ["1"])[0], query.get("needle", ["code"])[0],
                      int(query.get("sections", ["40"])[0])).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    http.server.ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
