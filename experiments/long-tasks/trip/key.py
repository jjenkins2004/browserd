"""Build key.json, the trip task's answer key: each city's November "Mean daily maximum °F" from a pinned Wikipedia
revision, which judge.py gives the judge (rubric.md allows 2°F). Fares, hotels and restaurants are live, so the key has
none: judge.py checks them against Google Flights as grade.py reads it and against the run's transcript.

    python3 experiments/long-tasks/trip/key.py
"""
import html
import json
import re
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
CITIES = [  # city, airport, Wikipedia revision id
    ("Seattle", "SEA", 1377723485),
    ("Portland", "PDX", 1376118667),
    ("Denver", "DEN", 1377063237),
    ("Chicago", "ORD", 1377352940),
    ("Austin", "AUS", 1377668706),
    ("Nashville", "BNA", 1378078983),
    ("Atlanta", "ATL", 1378615022),
    ("Boston", "BOS", 1378587368),
]


def november_high(oldid):
    """The 11th month's cell of the climate table's "Mean daily maximum °F" row, as the page shows it."""
    request = urllib.request.Request("https://en.wikipedia.org/w/index.php?oldid=%d" % oldid,
                                     headers={"User-Agent": "browserd-long-tasks key builder"})
    page = urllib.request.urlopen(request, timeout=60).read().decode("utf-8")
    row = re.search(r"Mean daily maximum °F.*?</tr>", page, re.S)
    if not row:
        raise SystemExit("revision %d has no Mean daily maximum °F row" % oldid)
    cells = re.findall(r"<td[^>]*>(.*?)</td>", row.group(0), re.S)
    text = html.unescape(re.sub(r"<[^>]+>", " ", cells[10]))
    return re.match(r"\s*([−-]?[\d.]+)", text).group(1).replace("−", "-")


if __name__ == "__main__":
    key = {"cities": [{"city": city, "airport": airport, "wikipedia": "https://en.wikipedia.org/w/index.php?oldid=%d"
                       % oldid, "nov_high_f": november_high(oldid)} for city, airport, oldid in CITIES]}
    (HERE / "key.json").write_text(json.dumps(key, indent=2, ensure_ascii=False) + "\n")
    for entry in key["cities"]:
        print(entry["city"], entry["nov_high_f"])
