"""Build key.json, the capex task's answer key, from SEC EDGAR's XBRL data for the four filings prompt.md names.

    python3 experiments/long-tasks/capex/key.py

Each value is the filing's own XBRL fact (accession and a one-year period matched), and is checked to be printed in the
filing's document, in $ millions, before it is written.
"""
import json
import os
import urllib.request
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

HERE = Path(__file__).resolve().parent
# SEC refuses a User-Agent without a contact address in it; SEC_USER_AGENT gives a real one.
AGENT = os.environ.get("SEC_USER_AGENT", "browserd-long-tasks key-builder@browserd.invalid")
FILINGS = [  # ticker, CIK, accession, primary document: the latest 10-K of each as of 2026-10-01
    ("MSFT", 789019, "0001193125-26-323660", "msft-20260630.htm"),
    ("GOOGL", 1652044, "0001652044-26-000018", "goog-20251231.htm"),
    ("AMZN", 1018724, "0001018724-26-000004", "amzn-20251231.htm"),
    ("META", 1326801, "0001628280-26-003942", "meta-20251231.htm"),
]
# us-gaap concepts per value; the first one a filing reports wins, except revenue, where the total is the largest.
CONCEPTS = {
    "revenue": ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax"],
    "net_income": ["NetIncomeLoss"],
    "capex": ["PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets"],
}
LABELS = {"revenue": "Revenue", "net_income": "Net income", "capex": "Capex"}


def get(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": AGENT}), timeout=60) as reply:
        return reply.read().decode("utf-8", "replace")


def yearly(facts, concept, accession):
    """{period end: value in $ millions} of a concept's one-year facts in that filing."""
    found = {}
    for fact in facts.get(concept, {}).get("units", {}).get("USD", []):
        if fact["accn"] != accession or "start" not in fact:
            continue
        days = (date.fromisoformat(fact["end"]) - date.fromisoformat(fact["start"])).days
        if 350 <= days <= 380:
            found[fact["end"]] = fact["val"] // 1_000_000
    return found


def billions(millions):
    return "$%sB" % (Decimal(millions) / 1000).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)


def company(ticker, cik, accession, document):
    """One company's key entry: both years of each value, the growth, and the slide's expected lines."""
    url = "https://www.sec.gov/Archives/edgar/data/%d/%s/%s" % (cik, accession.replace("-", ""), document)
    facts = json.loads(get("https://data.sec.gov/api/xbrl/companyfacts/CIK%010d.json" % cik))["facts"]["us-gaap"]
    printed = get(url)
    entry = {"ticker": ticker, "filing": url}
    for name, concepts in CONCEPTS.items():
        found = [yearly(facts, c, accession) for c in concepts]
        found = [f for f in found if len(f) >= 2]
        if not found:
            raise SystemExit("%s: no two years of %s in %s" % (ticker, name, accession))
        chosen = max(found, key=lambda f: max(f.values())) if name == "revenue" else found[0]
        prior_end, latest_end = sorted(chosen)[-2:]
        entry[name] = [chosen[prior_end], chosen[latest_end]]
        entry["fy"] = [int(prior_end[:4]), int(latest_end[:4])]
        for value in entry[name]:
            if "{:,}".format(value) not in printed:
                raise SystemExit("%s: %s %s is not printed in %s" % (ticker, name, value, url))
    entry["growth"] = round(entry["capex"][1] / entry["capex"][0] - 1, 6)
    entry["title"] = "%s — FY%d" % (ticker, entry["fy"][1])
    entry["lines"] = ["%s: %s (prior %s)" % (LABELS[n], billions(entry[n][1]), billions(entry[n][0])) for n in LABELS]
    return entry


if __name__ == "__main__":
    key = {"companies": [company(*filing) for filing in FILINGS]}
    (HERE / "key.json").write_text(json.dumps(key, indent=2, ensure_ascii=False) + "\n")
    for entry in key["companies"]:
        print(entry["title"], entry["revenue"], entry["net_income"], entry["capex"], "%.1f%%" % (100 * entry["growth"]))
