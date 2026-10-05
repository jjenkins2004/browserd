"""Build key.json, the parks task's answer key: every national park in the task's eight states, read from the pinned
revision of Wikipedia's "List of national parks of the United States" that prompt.md links, with its layer (the first
state the list gives), its date established, area in acres, 2025 visitors and coordinates; and the Mighty 5 route's
stops in order, the towns prompt.md names.

    python3 experiments/long-tasks/parks/key.py
"""
import html
import json
import re
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
REVISION = 1378075927  # 2 Oct 2026; prompt.md links it
STATES = ["California", "Oregon", "Washington", "Nevada", "Arizona", "Utah", "Colorado", "New Mexico"]  # prompt.md's
ROUTE = [  # stop, lat, lon: Las Vegas and the parks' gateway towns, in prompt.md's order
    ("Las Vegas", 36.1716, -115.1391),
    ("Springdale", 37.1889, -112.9986),
    ("Bryce Canyon City", 37.6744, -112.1574),
    ("Torrey", 38.2994, -111.4199),
    ("Moab", 38.5733, -109.5498),
    ("Las Vegas", 36.1716, -115.1391),
]


def text(cell):
    """A cell's text, without its footnote references ([111])."""
    cell = re.sub(r"<sup[^>]*>.*?</sup>", "", cell, flags=re.S)
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", cell))).strip()


def search(pattern, string):
    found = re.search(pattern, string, re.S)
    if not found:
        raise SystemExit("revision %d: no %r in a row of the list" % (REVISION, pattern))
    return found


def parks(page):
    """Each row of the list's main table: its name, states, date, acres, visitors and coordinates."""
    found = []
    for row in re.findall(r"<tr[^>]*>\s*<th scope=\"row\".*?</tr>", page, re.S):
        cells = re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)
        if len(cells) < 6:  # the table of parks by state has 3 cells a row
            continue
        name = text(search(r"<th[^>]*>\s*<a [^>]*>(.*?)</a>", row).group(1))  # not the marks after it (*, †, ‡)
        location = cells[1]
        states = [text(state) for state in re.findall(r"<a [^>]*>([^<]+)</a>", location.split("<br")[0])]
        lat, lon = map(float, search(r'<span class="geo">([-\d.]+); ([-\d.]+)</span>', location).groups())
        acres = search(r"([\d,.]+) acres", text(cells[3])).group(1)
        found.append({"name": name, "states": states, "established": text(cells[2]), "acres": acres,
                      "visitors": text(cells[4]), "lat": lat, "lon": lon})
    return found


if __name__ == "__main__":
    request = urllib.request.Request("https://en.wikipedia.org/w/index.php?oldid=%d" % REVISION,
                                     headers={"User-Agent": "browserd-long-tasks key builder"})
    listed = parks(urllib.request.urlopen(request, timeout=60).read().decode("utf-8"))
    chosen = []
    for park in listed:
        if not set(park["states"]) & set(STATES):
            continue
        if park["states"][0] not in STATES:
            raise SystemExit("%s is listed first under %s, outside the task's states"
                             % (park["name"], park["states"][0]))
        chosen.append(dict(park, layer=park.pop("states")[0]))
    route = [{"stop": stop, "lat": lat, "lon": lon} for stop, lat, lon in ROUTE]
    key = {"wikipedia": "https://en.wikipedia.org/w/index.php?oldid=%d" % REVISION, "states": STATES, "parks": chosen,
           "route": route}
    (HERE / "key.json").write_text(json.dumps(key, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    for state in STATES:
        print(state, [park["name"] for park in chosen if park["layer"] == state])
