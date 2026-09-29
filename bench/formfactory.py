"""FormFactory's forms (formfactory-ai/formfactory): fill one form from one document, scored field by field.

A task is one gold record of one form: the form's page, and the record's document, cut from data/data2 (the "text"
input FormFactory's own batch processor gives a model). ffserver.py saves each run's submission under its token,
and a run passes when that submission holds the gold value of every field it can be scored on. The server must be
up first:

    bench/sites.sh start
"""
import json
import re
import urllib.request
from html.parser import HTMLParser

import paths

FF = paths.DATA / "formfactory"
SUBMISSIONS = paths.RESULTS / "formfactory-submissions"
BASE = "http://127.0.0.1:5055"
UPLOAD = paths.BENCH / "assets" / "sample.pdf"
INSTANCES = 2  # gold records per form: the first ones whose documents cut cleanly

# template: (its page, its gold records' file stem). /academic-research/student-registration is left out: it
# shows A13, as the scholarship page does, and has no gold records of its own.
FORMS = {
    "A11": ("/academic-research/job-application", "job_applications"),
    "A12": ("/academic-research/grant-application", "grant_applications"),
    "A13": ("/academic-research/scholarship-application", "scholarship_applications"),
    "A14": ("/academic-research/paper-submission", "paper_submissions"),
    "A15": ("/academic-research/course-registration", "student_courses"),
    "B11": ("/professional-business/startup-funding", "startup_funding_applications"),
    "B12": ("/professional-business/rental-application", "real_estate_rental_applications"),
    "B13": ("/professional-business/workshop-registration", "workshop_registrations"),
    "B14": ("/professional-business/membership-application", "membership_application"),
    "C11": ("/arts-creative/exhibition-submission", "Art_Exhibition_Submission_Form"),
    "C12": ("/arts-creative/literary-submission", "Literary_Magazine_Submission"),
    "C13": ("/arts-creative/speaker-application", "Conference_Speaker_Application"),
    "D11": ("/tech-software/bug-report", "Bug_report"),
    "D12": ("/tech-software/support-request", "IT_support"),
    "E11": ("/finance-banking/personal-loan", "person_loan_applications"),
    "E12": ("/finance-banking/account-opening", "bank_account_applications"),
    "E13": ("/finance-banking/financial-planning", "financial_planning"),
    "F11": ("/healthcare-medical/patient-consent", "Patient_Consent"),
    "F12": ("/healthcare-medical/research-enrollment", "Medical_study_Form"),
    "F13": ("/healthcare-medical/insurance-claim", "Health_Insurance"),
    "G11": ("/legal-compliance/nda-submission", "NDA"),
    "G12": ("/legal-compliance/background-check", "Background_check"),
    "G13": ("/legal-compliance/contractor-onboarding", "Contrator_onboard"),
    "H11": ("/construction-manufacturing/project-bid", "Project_Bid"),
    "H12": ("/construction-manufacturing/order-request", "Manufacturing_Order"),
}

# A12 builds its fields in the page from app.py's /form-config; these are that config's fields, as its script
# makes them (a date is a text box; a checkbox's value is the browser's "on").
A12_FIELDS = [
    {"type": "text", "name": "first_name", "label": "First Name"},
    {"type": "text", "name": "last_name", "label": "Last Name"},
    {"type": "text", "name": "email", "label": "Email"},
    {"type": "text", "name": "dob", "label": "Date of Birth"},
    {"type": "radio", "name": "gender", "label": "Gender", "options": [("Male", "Male"), ("Female", "Female")]},
    {"type": "checkbox", "name": "subscribe", "label": "Subscribe to Newsletter"},
]

SYSTEM = "You are an agent for Browser automation."
MAX_TURNS = 40
PROMPT = """Go to {url} and fill in the form there with the information in the document below, then submit it. Fill every field the document gives a value for. If the form requires a file, upload {upload}.

Document:
{document}

Once the form is submitted, reply with the single word DONE."""


def _norm(value):
    text = str(value).replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    text = re.sub(r"\s*[–—-]\s*", "-", text)
    return " ".join(text.lower().split()).rstrip(".").strip()


def _alnum(value):
    return re.sub(r"[^a-z0-9]", "", str(value).lower())


def _date(value):
    text = str(value).strip()
    m = re.fullmatch(r"(\d{4})[/.-](\d{1,2})[/.-](\d{1,2})", text)
    if m:
        return tuple(int(x) for x in m.groups())
    m = re.fullmatch(r"(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})", text)
    if m:
        return int(m.group(3)), int(m.group(1)), int(m.group(2))
    return None


def _number(value):
    text = re.sub(r"[$,\s%]", "", str(value))
    return float(text) if re.fullmatch(r"-?\d*\.?\d+", text) else None


def _same(gold, got):
    if _norm(gold) == _norm(got):
        return True
    if _date(gold) and _date(gold) == _date(got):
        return True
    if _number(gold) is not None and _number(gold) == _number(got):
        return True
    digits = re.sub(r"\D", "", str(gold))
    return len(digits) >= 7 and re.fullmatch(r"[\d\s()+.x-]+", str(gold).strip()) is not None \
        and digits == re.sub(r"\D", "", str(got))


def _covered(gold, got):
    """Whether got holds at least 80% of gold's words of 4 or more letters: long text, whose gold is often a
    summary of what the document says at length."""
    words = set(re.findall(r"[a-z0-9]{4,}", _norm(" ".join(gold) if isinstance(gold, list) else gold)))
    have = set(re.findall(r"[a-z0-9]{4,}", _norm(got)))
    return bool(words) and len(words & have) >= 0.8 * len(words)


def _empty(gold):
    return gold is None or _norm(gold) in ("", "none", "n/a", "na", "no attachments")


def _truthy(gold):
    if isinstance(gold, bool) or isinstance(gold, (int, float)):
        return bool(gold)
    return _norm(gold) not in ("", "no", "false", "n", "none", "0", "unchecked", "not checked", "disagree")


class _Template(HTMLParser):
    """A template's fields, in page order: type, name, id, a select's options (value, text), and labels."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.fields, self.for_labels = [], {}
        self._label = self._option = self._select = self._pending = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "label":
            self._label = [a.get("for"), "", len(self.fields)]
        elif tag in ("input", "select", "textarea") and a.get("type") not in ("submit", "button", "hidden", "reset"):
            field = {"type": a.get("type") or tag, "name": a.get("name"), "id": a.get("id"),
                     "value": a.get("value"), "options": [], "multiple": "multiple" in a}
            if self._pending:
                field["group_label"], self._pending = self._pending, None
            self.fields.append(field)
            if tag == "select":
                self._select = field
        elif tag == "option" and self._select is not None:
            self._option = [a.get("value"), ""]

    def handle_endtag(self, tag):
        if tag == "label" and self._label:
            key, text, at = self._label
            text = " ".join(text.split()).rstrip("*").strip()
            if key:
                self.for_labels[key] = text
            elif at < len(self.fields):
                self.fields[at].setdefault("group_label", text)
            else:
                self._pending = text
            self._label = None
        elif tag == "option" and self._option:
            value, text = self._option
            text = " ".join(text.split())
            self._select["options"].append((text if value is None else value, text))
            self._option = None
        elif tag == "select":
            self._select = None

    def handle_data(self, data):
        if self._label:
            self._label[1] += data
        if self._option:
            self._option[1] += data


def fields(template):
    """The form's fields grouped by name: [{type, name, labels, options, multiple}]."""
    if template == "A12":
        return [dict(f, labels=[f["label"]], options=f.get("options", []), multiple=False, members=1) for f in A12_FIELDS]
    parser = _Template()
    parser.feed((FF / "templates" / ("%s.html" % template)).read_text())
    groups = {}
    for f in parser.fields:
        if not f["name"]:
            continue
        own = parser.for_labels.get(f["id"])
        group = groups.setdefault(f["name"], {"type": f["type"], "name": f["name"], "labels": [], "options": [],
                                              "multiple": f["multiple"], "members": 0})
        group["members"] += 1
        group["labels"] += [x for x in (f.get("group_label"),) if x]
        if f["type"] in ("radio", "checkbox"):
            group["options"].append((f["value"] or "on", own or f["value"] or ""))
            group["own_label"] = own
        else:
            group["labels"] += [x for x in (own,) if x]
            group["options"] += f["options"]
    for group in groups.values():
        if group["type"] == "checkbox" and group["members"] == 1 and group.get("own_label"):
            group["labels"].append(group["own_label"])
    return list(groups.values())


# Gold keys that name their field in other words than its label.
ALIASES = {"Selected Courses": "courses[]"}
# Gold values no option says in the same words, as (key, value): the option a person would choose. "Paper Category"
# is not scored: its gold values ("Artificial Intelligence", "Robotics") fit more than one option.
EQUIVALENT = {("Annual Family Income", "80303"): "$60,001 - $90,000",
              ("Annual Family Income", "60273"): "$60,001 - $90,000",
              ("Preferred Time Slot", "6PM"): "Evening (6:00 PM - 9:00 PM)",
              ("Preferred Time Slot", "8PM"): "Evening (6:00 PM - 9:00 PM)",
              ("Highest Education Level", "PhD"): "Doctorate",
              ("Account Type", "Saving Account"): "Savings Account"}
UNSCORED = {"Paper Category"}


def _match(key, groups):
    """The field group a gold key names: its label exactly, then its name, then a label one starts with."""
    for group in groups:
        if group["name"] == ALIASES.get(key):
            return group
    k = _alnum(key)
    for group in groups:
        if any(_alnum(label) == k for label in group["labels"]):
            return group
    for group in groups:
        if _alnum(group["name"]) == k:
            return group
    near = [(abs(len(_alnum(label)) - len(k)), group) for group in groups for label in group["labels"]
            if len(k) >= 5 and len(_alnum(label)) >= 5
            and (_alnum(label).startswith(k) or k.startswith(_alnum(label)))]
    return min(near, key=lambda x: x[0])[1] if near else None


def _gold_fields(record):
    """A record's (key, value) pairs, a nested section's included; a repeatable group (a list of dicts) is not."""
    for key, value in record.items():
        if isinstance(value, dict):
            yield from _gold_fields(value)
        elif not (isinstance(value, list) and any(isinstance(x, dict) for x in value)):
            yield key, value


def _option_matches(gold, value, text):
    return (_same(gold, value) or _same(gold, text) or _norm(text).startswith(_norm(gold))
            or (len(_alnum(value)) >= 3 and _alnum(gold).startswith(_alnum(value))))


def _field_right(group, gold, got):
    """Whether the submitted values (a list) of a field group hold its gold value."""
    kind = group["type"]
    if kind == "checkbox" and group["members"] == 1:
        return bool(got) == _truthy(gold)
    if _empty(gold) and not any(str(x).strip() for x in got):
        return True
    if kind in ("checkbox",) or (kind == "select" and group["multiple"]):
        wanted = gold if isinstance(gold, list) else [x for x in re.split(r"\s*[,;]\s*", str(gold)) if x]
        chosen = [(v, dict(group["options"]).get(v, v)) for v in got]
        return len(wanted) == len(chosen) and all(any(_option_matches(w, v, t) for v, t in chosen) for w in wanted)
    if kind in ("radio", "select"):
        if not got:
            return False
        text = dict(group["options"]).get(got[0], got[0])
        return _option_matches(gold, got[0], text) if not isinstance(gold, list) else False
    if isinstance(gold, list) or kind == "textarea":
        return any(_same(gold, x) for x in got) or _covered(gold, " ".join(got))
    return any(_same(gold, x) for x in got)


def _cut(text, count):
    """The first count records of a data2 file, cut at its numbered markers ("1.", "2)") taken in order."""
    starts, pos = [], 0
    for n in range(1, count + 2):
        m = re.compile(r"(?m)^[ \t]*%d[.)]" % n).search(text, pos)
        if not m:
            break
        starts.append((m.start(), m.end()))
        pos = m.end()
    return [text[end:(starts[i + 1][0] if i + 1 < len(starts) else len(text))].strip()
            for i, (_, end) in enumerate(starts[:count])]


def _covers(document, record):
    """The share of a record's scalar values of 4 or more characters found in its document."""
    values = [str(v) for _, v in _gold_fields(record) if not isinstance(v, (list, bool)) and len(str(v)) >= 4]
    flat = _alnum(document)
    return sum(_alnum(v) in flat for v in values) / len(values) if values else 0.0


def load():
    """INSTANCES tasks per form, as {"<template>-<record index>": task}: the first records whose cut document
    holds at least half their values."""
    tasks = {}
    for template, (route, stem) in FORMS.items():
        gold = json.loads((FF / "data" / "data1" / ("%s.json" % stem)).read_text())
        documents = _cut((FF / "data" / "data2" / ("%s.txt" % stem)).read_text(), 6)
        kept = 0
        for i, (record, document) in enumerate(zip(gold, documents)):
            if kept < INSTANCES and _covers(document, record) >= 0.5:
                tasks["%s-%d" % (template, i)] = {"template": template, "route": route, "gold": record,
                                                  "document": document}
                kept += 1
    return tasks


def url(task, token):
    return "%s%s?run=%s" % (BASE, task["route"], token)


def prompt(task, token):
    return PROMPT.format(url=url(task, token), upload=UPLOAD, document=task["document"])


def submission(task, token):
    """The run's last submission of its form, or None."""
    path = SUBMISSIONS / ("%s.jsonl" % token)
    if not path.exists():
        return None
    mine = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    mine = [s for s in mine if s["template"] == task["template"]]
    return mine[-1] if mine else None


def score(task, answer, token):
    """passed, and the fields: scored (gold keys naming a field that is not a file), right, and the wrong ones."""
    groups = fields(task["template"])
    sub = submission(task, token)
    got = sub["form"] if sub else {}
    scored, wrong = 0, []
    for key, gold in _gold_fields(task["gold"]):
        group = _match(key, groups)
        if group is None or group["type"] == "file" or key in UNSCORED:
            continue
        gold = EQUIVALENT.get((key, str(gold)), gold)
        scored += 1
        if not _field_right(group, gold, got.get(group["name"], [])):
            wrong.append(key)
    return {"passed": bool(sub) and scored > 0 and not wrong, "submitted": bool(sub), "fields": scored,
            "right": scored - len(wrong) if sub else 0, "wrong": wrong if sub else None}


def check():
    """Refuse to start runs while the server is down."""
    urllib.request.urlopen(BASE, timeout=5).read()
