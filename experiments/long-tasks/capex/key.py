"""Build key.json, the capex task's answer key, from SEC EDGAR's XBRL data for the latest 10-K each of the ten
companies prompt.md names filed before 2026-10-01.

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
FILINGS = [  # ticker, names, CIK, accession, primary document: the latest original 10-K of each filed before 2026-10-01
    ("AAPL", ["Apple"], 320193, "0000320193-25-000079", "aapl-20250927.htm"),
    ("MSFT", ["Microsoft"], 789019, "0001193125-26-323660", "msft-20260630.htm"),
    ("GOOGL", ["Alphabet", "Google"], 1652044, "0001652044-26-000018", "goog-20251231.htm"),
    ("AMZN", ["Amazon"], 1018724, "0001018724-26-000004", "amzn-20251231.htm"),
    ("META", ["Meta"], 1326801, "0001628280-26-003942", "meta-20251231.htm"),
    ("NVDA", ["NVIDIA"], 1045810, "0001045810-26-000021", "nvda-20260125.htm"),
    ("TSLA", ["Tesla"], 1318605, "0001628280-26-003952", "tsla-20251231.htm"),
    ("ORCL", ["Oracle"], 1341439, "0001193125-26-277521", "orcl-20260531.htm"),
    ("AVGO", ["Broadcom"], 1730168, "0001730168-25-000121", "avgo-20251102.htm"),
    ("CRWV", ["CoreWeave"], 1769628, "0001769628-26-000104", "crwv-20251231.htm"),
]
# us-gaap concepts per value; the first one a filing reports wins, except revenue, where the total is the largest.
CONCEPTS = {
    "revenue": ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax"],
    "operating_cash_flow": ["NetCashProvidedByUsedInOperatingActivities"],
    "capex": ["PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets"],
}
TABLE = ["revenue", "capex", "free_cash_flow"]  # each company slide's table rows, prompt.md's order


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
    return str((Decimal(millions) / 1000).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))


def company(ticker, names, cik, accession, document):
    """One company's key entry: its three fiscal years (oldest first) of each value, free cash flow and capex as a
    share of revenue, and its slide's title and table in $ billions."""
    url = "https://www.sec.gov/Archives/edgar/data/%d/%s/%s" % (cik, accession.replace("-", ""), document)
    facts = json.loads(get("https://data.sec.gov/api/xbrl/companyfacts/CIK%010d.json" % cik))["facts"]["us-gaap"]
    printed = get(url)
    entry = {"ticker": ticker, "names": names, "filing": url}
    for value_name, concepts in CONCEPTS.items():
        found = [yearly(facts, c, accession) for c in concepts]
        found = [f for f in found if len(f) >= 3]
        if not found:
            raise SystemExit("%s: no three years of %s in %s" % (ticker, value_name, accession))
        chosen = max(found, key=lambda f: max(f.values())) if value_name == "revenue" else found[0]
        ends = sorted(chosen)[-3:]
        entry[value_name] = [chosen[end] for end in ends]
        fy = [int(end[:4]) for end in ends]  # the calendar year each fiscal year ends in, as each names it
        if entry.setdefault("fy", fy) != fy:
            raise SystemExit("%s: %s's years %s are not %s" % (ticker, value_name, fy, entry["fy"]))
        for value in entry[value_name]:
            if "{:,}".format(value) not in printed:
                raise SystemExit("%s: %s %s is not printed in %s" % (ticker, value_name, value, url))
    entry["free_cash_flow"] = [cash - capex for cash, capex in zip(entry["operating_cash_flow"], entry["capex"])]
    entry["capex_share"] = [round(capex / revenue, 6) for capex, revenue in zip(entry["capex"], entry["revenue"])]
    entry["title"] = "%s — FY%d" % (ticker, entry["fy"][-1])
    entry["table"] = {name: [billions(value) for value in entry[name]] for name in TABLE}
    return entry


if __name__ == "__main__":
    key = {"companies": [company(*filing) for filing in FILINGS]}
    (HERE / "key.json").write_text(json.dumps(key, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    for entry in key["companies"]:
        print(entry["title"], entry["fy"], entry["revenue"], entry["operating_cash_flow"], entry["capex"])
