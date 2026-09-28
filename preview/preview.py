"""The preview: every state of the browserd page's parts, drawn by the page's own code from made-up data.

    python3 preview/preview.py [port]    then http://127.0.0.1:9320/

README.md says what each request answers and how to add a variant; ../browser/ui/README.md, how to add a state.
"""

import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
from browser import page  # noqa: E402

PORT = 9320  # README.md says why
VARIANTS = os.path.join(HERE, "variants")
STORIES = ".stories.js"


def read(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def story_files():
    """Each part with a stories file: the page first, then page.PARTS' order, then any other."""
    found = {name[:-len(STORIES)] for name in os.listdir(page.UI) if name.endswith(STORIES)}
    known = [part for part in dict.fromkeys(("page",) + page.PARTS) if part in found]
    return known + sorted(found - set(known))


def variants():
    if not os.path.isdir(VARIANTS):
        return []
    return sorted({os.path.splitext(name)[0] for name in os.listdir(VARIANTS) if name.endswith((".css", ".js"))})


def version():
    """The newest change under ../browser/ui/ and here, a file's or a folder's, so a deletion counts too."""
    newest = 0.0
    for top in (page.UI, HERE):
        for folder, _, names in os.walk(top):
            newest = max([newest, os.path.getmtime(folder)] + [os.path.getmtime(os.path.join(folder, n)) for n in names])
    return newest


def data():
    """The gallery's list: the variants, then every stories file in a function of its own, so their names never meet."""
    chunks = ["const VARIANTS = %s;" % json.dumps(variants()), read(os.path.join(page.UI, "samples.js"))]
    for file in story_files():
        chunks.append("current = %s;\n(() => {\n%s\n})();" % (json.dumps(file), read(os.path.join(page.UI, file + STORIES))))
    return "\n".join(chunks)


def _script(path):
    # One script per file, so one that fails leaves the rest running, and devtools names it.
    return "<script>\n%s\n//# sourceURL=%s\n</script>" % (read(path), os.path.relpath(path, ROOT))


def frame(file, story, variant):
    """One story, drawn alone; README.md, "Core Abstractions & Shared Pieces"."""
    styles = [read(os.path.join(page.UI, "page.css"))]
    scripts = [os.path.join(page.UI, part + ".js") for part in page.PARTS]
    if variant:
        base = os.path.join(VARIANTS, variant)
        if os.path.exists(base + ".css"):
            styles.append(read(base + ".css"))
        if os.path.exists(base + ".js"):
            scripts.append(base + ".js")
    styles.append(read(os.path.join(HERE, "frame.css")))
    scripts += [os.path.join(HERE, "harness.js"), os.path.join(page.UI, "samples.js"), os.path.join(page.UI, file + STORIES)]
    return "\n".join(["<!doctype html>", '<html lang="en">', '<head>', '<meta charset="utf-8">']
                     + ["<style>\n%s\n</style>" % css for css in styles]
                     + ["</head>", '<body class="%s">' % ("page" if file == "page" else "part"), "<main></main>",
                        '<script>const TOKEN = "preview";</script>']
                     + [_script(path) for path in scripts]
                     + ["<script>show(%s);</script>" % json.dumps(story).replace("</", "<\\/"), "</body>", "</html>"])


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format, *args):
        pass  # the gallery asks /version every second

    def _send(self, status, body, kind):
        data = body.encode()
        self.send_response(status)
        self.send_header("Content-Type", kind + "; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        url = urlsplit(self.path)
        asked = {key: values[0] for key, values in parse_qs(url.query).items()}
        try:
            if url.path == "/":
                return self._send(200, read(os.path.join(HERE, "gallery.html")), "text/html")
            if url.path == "/data.js":
                return self._send(200, data(), "text/javascript")
            if url.path == "/version":
                return self._send(200, repr(version()), "text/plain")
            if url.path == "/frame":
                file, variant = asked.get("file"), asked.get("variant", "")
                if file not in story_files() or (variant and variant not in variants()):
                    return self._send(404, "no such part or variant", "text/plain")
                return self._send(200, frame(file, asked.get("story", ""), variant), "text/html")
        except OSError as exc:
            return self._send(500, str(exc), "text/plain")
        return self._send(404, "no such page", "text/plain")


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else PORT
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    print("the preview is at http://127.0.0.1:%d/ (Ctrl-C stops it)" % port, flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
