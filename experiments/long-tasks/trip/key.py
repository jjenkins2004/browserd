"""Build key.json, the trip task's answer key: each city's November "Mean daily maximum °F" from a pinned Wikipedia
revision (rubric.md allows 2°F), and its FY2027 M&IE total from gsa.gov. judge.py gives both to the judge. Fares,
hotels and dinner spots are live, so the key has none: judge.py checks them against Google Flights as grade.py reads it
and against the run's transcript.

    python3 experiments/long-tasks/trip/key.py
"""
import html
import json
import re
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
CITIES = [  # city, state, airport, Wikipedia revision id
    ("Seattle", "WA", "SEA", 1377723485),
    ("Denver", "CO", "DEN", 1377063237),
    ("Chicago", "IL", "ORD", 1377352940),
    ("Austin", "TX", "AUS", 1377668706),
    ("Nashville", "TN", "BNA", 1378078983),
    ("Boston", "MA", "BOS", 1378587368),
]
FISCAL_YEAR = 2027  # GSA's FY2027 runs Oct 2026 to Sep 2027, so it holds Nov 2026


def read(url):
    request = urllib.request.Request(url, headers={"User-Agent": "browserd-long-tasks key builder"})
    return urllib.request.urlopen(request, timeout=60).read().decode("utf-8")


def cells(row):
    return [html.unescape(re.sub(r"<[^>]+>", " ", cell)).strip() for cell in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)]


def november_high(oldid):
    """The 11th month's cell of the climate table's "Mean daily maximum °F" row, as the page shows it."""
    row = re.search(r"Mean daily maximum °F.*?</tr>", read("https://en.wikipedia.org/w/index.php?oldid=%d" % oldid), re.S)
    if not row:
        raise SystemExit("revision %d has no Mean daily maximum °F row" % oldid)
    return re.findall(r"[−-]?[\d.]+", cells(row.group(0))[10])[0].replace("−", "-")


def per_diem(city, state):
    """The city's GSA destination (its first name is the city's: Denver's is "Denver / Aurora", listed after
    "Boulder / Broomfield"), its county and the M&IE total, from gsa.gov's results page."""
    page = read("https://www.gsa.gov/travel/plan-book/per-diem-rates/per-diem-rates-results?" + urllib.parse.urlencode(
        {"action": "perdiems_report", "state": state, "fiscal_year": FISCAL_YEAR, "zip": "", "city": city}))
    # Split at each row's end, since a row after the first may lack its <tr> (Denver's M&IE row does). An M&IE row is
    # the destination, county, M&IE total, its parts and the first and last day's amount.
    meals = [found for found in map(cells, page.split("</tr>"))
             if len(found) == 8 and found[0].split(" / ")[0] == city]
    if len(meals) != 1:
        raise SystemExit("gsa.gov's FY%d page for %s, %s has %d M&IE rows for it" % (FISCAL_YEAR, city, state, len(meals)))
    return {"destination": meals[0][0], "county": meals[0][1], "mie": int(meals[0][2].strip("$"))}


if __name__ == "__main__":
    key = {"cities": [{"city": city, "airport": airport, "wikipedia": "https://en.wikipedia.org/w/index.php?oldid=%d"
                       % oldid, "nov_high_f": november_high(oldid), "gsa": per_diem(city, state)}
                      for city, state, airport, oldid in CITIES]}
    (HERE / "key.json").write_text(json.dumps(key, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    for entry in key["cities"]:
        print(entry["city"], entry["nov_high_f"], entry["gsa"])
