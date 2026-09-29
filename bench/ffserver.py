#!/usr/bin/env python3
"""FormFactory's own Flask app on 127.0.0.1:5055, with each submission saved under the run that made it.

    bench/sites.sh start

FormFactory's routes hand every POST to save_submission_to_json, which appends it to one file per form; runs of
the same form at once would land together, and request.form.to_dict() keeps only the first of a field's values.
This replaces that function: the whole POST (every value of every field, and the uploaded files' names) is
appended to results/formfactory-submissions/<token>.jsonl, the token read from the page's ?run= query, which a
form's POST carries in its own URL (action="") or else in its Referer.
"""
import json
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

if __name__ == "__main__":
    formfactory.app.run(host="127.0.0.1", port=PORT, threaded=True)
