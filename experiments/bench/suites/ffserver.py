"""FormFactory's own Flask app on 127.0.0.1:5055, with each submission saved under the run that made it.

    python experiments/bench/sites.py start

FormFactory's routes hand every POST to save_submission_to_json, which appends it to one file per form; runs of
the same form at once would land together, and request.form.to_dict() keeps only the first of a field's values.
This replaces that function: the whole POST (every value of every field, and the uploaded files' names) is
appended to results/formfactory-submissions/<token>.jsonl, the token read from the page's ?run= query, which a
form's POST carries in its own URL (action="") or else in its Referer.

Every page it serves has its file fields' required attribute taken out: formfactory.py never scores a file field, and
Claude in Chrome cannot upload a file from the data folder, so a required one (on F13, G13 and H11) would keep that
arm alone from submitting forms the others can.
"""
import json
import re
import sys
import time
import urllib.parse

from flask import request

import paths

SUBMISSIONS = paths.RESULTS / "formfactory-submissions"
PORT = 5055

sys.path.insert(0, str(paths.DATA / "formfactory"))
import app as formfactory  # noqa: E402


def _token():
    token = request.args.get("run")
    if not token:
        referer = urllib.parse.urlparse(request.headers.get("Referer", ""))
        token = urllib.parse.parse_qs(referer.query).get("run", [None])[0]
    return token or "unknown"


def save_submission(template_name, data):
    SUBMISSIONS.mkdir(parents=True, exist_ok=True)
    record = {"template": template_name.replace(".html", ""), "time": time.time(),
              "url": request.url, "referer": request.headers.get("Referer"),
              "form": request.form.to_dict(flat=False),
              "files": {name: [f.filename for f in request.files.getlist(name)] for name in request.files}}
    with (SUBMISSIONS / ("%s.jsonl" % _token())).open("a") as out:
        out.write(json.dumps(record) + "\n")


formfactory.save_submission_to_json = save_submission

FILE_REQUIRED = re.compile(r'(<input type="file"[^>]*) required\b')  # as the pinned templates write it


@formfactory.app.after_request
def optional_files(response):
    if response.mimetype == "text/html":
        response.set_data(FILE_REQUIRED.sub(r"\1", response.get_data(as_text=True)))
    return response


if __name__ == "__main__":
    formfactory.app.run(host="127.0.0.1", port=PORT, threaded=True)
