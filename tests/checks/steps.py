"""The queue and its steps, offline, against a stand-in chrome-devtools-mcp: checked steps, dialogs, paste,
screenshots, the pointer, and what a queue refuses.
"""

import base64
import json
import os
import re
import shutil
import tempfile
import time

from browser.chrome import cdp
from browser.dashboard import page
from browser.steps import checked, dialogs, hit, pointer, screenshot, steps
from browser.tabs import devtools
from browser.tabs.worker import Workers
from harness import FakeDevtools, STAND_IN, check, failed, refusal, text_of, uid


SCHEMAS = {  # the queue's tools as chrome-devtools-mcp describes them, cut to what the checks use
    "click": {"inputSchema": {"required": ["pageId", "uid"], "properties": {
        "pageId": {"type": "number"}, "uid": {"type": "string"}, "includeSnapshot": {"type": "boolean"}}}},
    "take_screenshot": {"inputSchema": {"required": ["pageId"], "properties": {
        "pageId": {"type": "number"}, "format": {"type": "string", "enum": ["png", "jpeg", "webp"]},
        "quality": {"type": "number"}, "filePath": {"type": "string"}}}},
    "upload_file": {"inputSchema": {"required": ["pageId", "uid", "filePaths"], "properties": {
        "pageId": {"type": "number"}, "uid": {"type": "string"}, "filePaths": {"type": "array", "items": {"type": "string"}}}}},
    "take_snapshot": {"inputSchema": {"required": ["pageId"], "properties": {
        "pageId": {"type": "number"}, "verbose": {"type": "boolean"}, "filePath": {"type": "string"}}}},
    "fill_form": {"inputSchema": {"required": ["pageId", "elements"], "properties": {
        "pageId": {"type": "number"}, "elements": {"type": "array", "minItems": 1, "items": {
            "type": "object", "required": ["uid", "value"],
            "properties": {"uid": {"type": "string"}, "value": {"type": "string"}}}}}}},
    "wait_for": {"inputSchema": {"required": ["pageId", "text"], "properties": {
        "pageId": {"type": "number"}, "text": {"type": "array", "items": {"type": "string"}},
        "timeout": {"type": "integer"}}}},
}


class Blocked:
    """A page where every call finds a dialog open that the call itself raised."""

    def text(self, tool, arguments, wait=None):
        raise cdp.CdpError("# Open dialog\nalert: Heads up.\nCall handle_dialog to handle it before continuing.")


def queue_offline():
    workdir = tempfile.mkdtemp(prefix="browser-steps-")
    gap, steps.GAP = steps.GAP, 0  # a stand-in page has nothing to react to between steps
    try:
        def load(**arguments):
            return refusal(lambda: steps.load(arguments, workdir), steps.StepError)

        check("a queue with neither steps nor file is refused", "exactly one" in load())
        check("a queue with both steps and file is refused", "exactly one" in load(steps=[{"tool": "click"}], file="/x"))
        check("a missing file is refused, naming it", "/nope/steps.json" in load(file="/nope/steps.json"))
        path = os.path.join(workdir, "bad.json")
        with open(path, "w") as handle:
            handle.write("[{")
        check("a file that is not JSON is refused", "could not read" in load(file=path))
        check("steps that are not a list are refused, saying what they are", "a list of" in load(steps={"tool": "click"})
              and "not dict" in load(steps={"tool": "click"}), load(steps={"tool": "click"}))
        check("an empty queue is refused", "the steps list is empty" in load(steps=[]))
        check("a step with no tool name is refused, by number", "step 2" in load(steps=[{"tool": "click"}, {"uid": "1_1"}]))
        check("a step that gives pageId is refused", "pageId" in load(steps=[{"tool": "click", "pageId": 3}]))
        bare = steps.load({"steps": [{"tool": "click", "uid": "uid=1_2"}, {"tool": "take_snapshot", "under": "uid=1_3"},
                                     {"tool": "evaluate_script", "function": "(el) => 1", "args": ["uid=1_4"]},
                                     {"tool": "fill_form", "elements": [{"uid": "uid=1_5", "value": "x"}]}]}, workdir)
        check("a uid copied with its uid= from a view line is taken without it, wherever a step names one",
              [bare[0]["uid"], bare[1]["under"], bare[2]["args"], bare[3]["elements"][0]["uid"]] == ["1_2", "1_3", ["1_4"], "1_5"],
              repr(bare))
        path = os.path.join(workdir, "good.json")
        with open(path, "w") as handle:
            json.dump([{"tool": "take_snapshot"}], handle)
        check("steps load from an absolute file path, whatever folder is given", steps.load({"file": path}, "/elsewhere") == [{"tool": "take_snapshot"}])
        check("a relative file path is read from the folder given", steps.load({"file": "good.json"}, workdir) == [{"tool": "take_snapshot"}])
        said = refusal(lambda: steps.check([{"tool": "click"}, {"tool": "new_page"}], {"click": {}}), steps.StepError)
        check("a step naming a tool the queue leaves out is refused, by number, saying why", "step 2" in said and "left out" in said, said)
        said = refusal(lambda: steps.check([{"tool": "click"}, {"tool": "frobnicate"}], {"click": {}}), steps.StepError)
        check("a step naming a tool that does not exist is refused, by number, naming the tools a queue runs",
              "step 2" in said and "not a tool" in said and "it runs pick, expect, type, paste, wait, move_at, click_down, click_up, click" in said, said)
        check("a step whose tool name is empty is refused", "not an object with a tool name" in load(steps=[{"tool": ""}]))
        inside = os.path.join(workdir, "resume.pdf")
        # Absolute on this OS, /etc/... on macOS and C:\etc\... on Windows, and outside every folder the file tools may use.
        hosts, shot = (os.path.join(os.path.abspath(os.sep), "etc", name) for name in ("hosts", "shot.png"))
        check("the paths outside those folders are absolute here, so what refuses them is where they are",
              os.path.isabs(hosts) and os.path.isabs(shot) and not devtools.may_touch(hosts) and not devtools.may_touch(shot),
              repr((hosts, shot)))
        for label, step, words in (
                ("an argument its tool does not take", {"tool": "click", "uid": "1_1", "bogus": 1}, "does not take bogus"),
                ("an argument of the wrong type", {"tool": "click", "uid": 5}, "click's uid must be string"),
                ("a missing argument", {"tool": "click"}, "click needs uid"),
                ("a value its tool does not allow", {"tool": "take_screenshot", "format": "gif"}, "png|jpeg|webp"),
                ("true for a number", {"tool": "take_screenshot", "quality": True}, "quality"),
                ("a file path outside the folders file tools may use", {"tool": "upload_file", "uid": "1_1", "filePaths": [inside, hosts]}, "cannot use %s" % hosts),
                ("a screenshot path outside them", {"tool": "take_screenshot", "filePath": shot}, "cannot use %s" % shot),
                ("a take_snapshot filePath", {"tool": "take_snapshot", "filePath": inside}, "record folder"),
                ("a ~ path", {"tool": "upload_file", "uid": "1_1", "filePaths": ["~/Desktop/resume.pdf"]}, "absolute"),
                ("a relative path", {"tool": "take_screenshot", "filePath": "shot.png"}, "absolute"),
                ("a fill_form element missing its value", {"tool": "fill_form", "elements": [{"uid": "1_1"}]}, "[{uid, value}]"),
                ("a fill_form with no elements", {"tool": "fill_form", "elements": []}, "elements"),
                ("an argument its tool takes only elsewhere", {"tool": "take_snapshot", "bogus": 1}, "it takes verbose, under, full")):
            said = refusal(lambda: steps.check([{"tool": "take_snapshot"}, step], SCHEMAS), steps.StepError)
            check("a step with %s is refused before any step runs, by number" % label, "step 2: " in said and words in said, said)
        lone = {"tool": "upload_file", "uid": "1_1", "filePaths": inside}
        steps.check([lone], SCHEMAS)
        check("one string where a tool asks for a list of strings is made that list", lone["filePaths"] == [inside], repr(lone))
        check("steps that fit their tools' schemas pass, file paths inside the folders file tools may use included",
              not refusal(lambda: steps.check([{"tool": "click", "uid": "1_1", "includeSnapshot": True},
                                               {"tool": "take_screenshot", "format": "jpeg", "filePath": inside},
                                               {"tool": "upload_file", "uid": "1_1", "filePaths": [inside]},
                                               {"tool": "take_snapshot", "under": "1_1", "full": True, "verbose": True},
                                               {"tool": "fill_form", "elements": [{"uid": "1_1", "value": "x"}]},
                                               {"tool": "wait_for", "text": ["x"], "timeout": 5000.0}], SCHEMAS),
                          steps.StepError))

        described = steps.describe({"fill": {"description": "Type text into an input. More detail.", "inputSchema": {
            "properties": {"pageId": {"type": "number"}, "uid": {"type": "string"}, "includeSnapshot": {"type": "boolean"}},
            "required": ["pageId", "uid"]}}})
        check("a tool is described by its arguments without pageId, optional ones marked, and its first sentence",
              described.endswith("\n  fill(uid: string, includeSnapshot?: boolean) - Type text into an input"), described)
        check("the queue's own pick, expect, type and paste are described first",
              described.startswith("  pick(") and "\n  expect(" in described and "\n  type(" in described.split("\n  fill(")[0]
              and "\n  paste(text: string, uid?: string) - " in described.split("\n  fill(")[0], described[:1500])
        said = refusal(lambda: steps.check([{"tool": "pick", "uid": "1_1", "text": "x", "txt": "x"}], {}), steps.StepError)
        check("a pick with a key it does not take is refused before anything runs", "txt" in said, said)
        for label, step, words in (
                ("a pick without text", {"tool": "pick", "uid": "1_1"}, "needs text"),
                ("a pick whose wait is true", {"tool": "pick", "uid": "1_1", "text": "x", "wait": True}, "wait"),
                ("a pick with an empty search", {"tool": "pick", "uid": "1_1", "text": "x", "search": ""}, "search"),
                ("an expect whose value is not a string", {"tool": "expect", "uid": "1_1", "value": 3}, "needs value"),
                ("a type without text", {"tool": "type", "uid": "1_1"}, "needs text"),
                ("a type with a key it does not take", {"tool": "type", "uid": "1_1", "text": "x", "value": "x"}, "value"),
                ("a wait with two conditions", {"tool": "wait", "gone": "x", "still": 500}, "exactly one"),
                ("a wait with no condition", {"tool": "wait", "timeout": 500}, "exactly one"),
                ("a wait for a value with no uid", {"tool": "wait", "value": "x"}, "uid"),
                ("a wait whose timeout is 0", {"tool": "wait", "gone": "x", "timeout": 0}, "timeout"),
                ("a wait whose still is not under its timeout", {"tool": "wait", "still": 5000, "timeout": 5000}, "still"),
                ("a wait whose timeout reads as seconds", {"tool": "wait", "gone": "x", "timeout": 10}, "for 10 seconds give 10000"),
                ("a wait whose still reads as seconds", {"tool": "wait", "still": 2}, "for 2 seconds give 2000"),
                ("a paste without text", {"tool": "paste", "uid": "1_1"}, "paste needs text"),
                ("a paste whose uid is empty", {"tool": "paste", "text": "x", "uid": ""}, "paste's uid"),
                ("a paste with a key it does not take", {"tool": "paste", "text": "x", "value": "x"}, "value")):
            said = refusal(lambda: steps.check([{"tool": "take_snapshot"}, step], {"take_snapshot": {}}), steps.StepError)
            check("%s is refused before any step runs, by number" % label, "step 2" in said and words in said, said)
        check("pick and expect need no chrome-devtools-mcp tool of that name",
              not refusal(lambda: steps.check([{"tool": "pick", "uid": "1_1", "text": "x"}, {"tool": "expect", "uid": "1_1", "value": "y"}], {}),
                          steps.StepError))

        snapshot = "\n".join([
            'uid=1_0 RootWebArea "form"',
            '  uid=1_2 combobox "Auth" expandable haspopup="menu" value="Select"',
            '    uid=1_3 option "Select" selectable selected value="Select"',
            '  uid=2_0 listbox orientation="vertical"',
            '    uid=2_1 generic',
            '      uid=2_2 option "Los Angeles, California, United States" selectable value="Los Angeles, California, United States"',
            '      uid=2_3 option "Currently hold a "Secret" clearance" selectable value="Currently hold a "Secret" clearance"',
            '      uid=2_4 option "Level "3" or above" selectable selected value="Level "3" or above"',
            '      uid=2_5 option "Last"',
            '  uid=3_0 textbox "Name"',
        ])
        found = checked._options(snapshot)
        check("options are read from an option list, however deep, and not from a native select",
              [u for u, _ in found] == ["2_2", "2_3", "2_4", "2_5"], repr(found))
        check("an option's name ends where its value attribute says, quotes and words inside it kept",
              [t for _, t in found] == ["Los Angeles, California, United States", 'Currently hold a "Secret" clearance',
                                        'Level "3" or above', "Last"], repr(found))
        planned = [{"tool": "click", "uid": "1_1"}, {"tool": "take_screenshot", "fullPage": True},
                   {"tool": "take_screenshot", "format": "jpeg"}, {"tool": "take_screenshot", "filePath": "/tmp/mine.png"}]
        placed = steps.place_screenshots(planned, lambda name: "/job/run/004-" + name)
        check("a take_screenshot with no filePath is given one, named for its call and step",
              placed[1] == {"tool": "take_screenshot", "fullPage": True, "filePath": "/job/run/004-step2-screenshot.png"}, repr(placed))
        check("with the extension its format asks for", placed[2].get("filePath") == "/job/run/004-step3-screenshot.jpeg", repr(placed))
        check("a filePath the step gives, and every other step, is left as it is", placed[0] == planned[0] and placed[3] == planned[3])
        placed = steps.place_screenshots([{"tool": "take_screenshot"}], lambda name: "/job/run/004-" + name)
        check("a screenshot of the viewport with no format is saved as browserd takes it, a JPEG",
              placed[0].get("filePath") == "/job/run/004-step1-screenshot.jpeg", repr(placed))
        check("the tab-managing tools are left out of a queue",
              steps.LEFT_OUT >= {"new_page", "close_page", "select_page", "list_pages"})

        image = {"type": "image", "data": "AAAA", "mimeType": "image/png"}
        fake = FakeDevtools([([{"type": "text", "text": "Filled"}], False), ([{"type": "text", "text": "shot"}, image], False),
                             ([{"type": "text", "text": "Element uid 9_9 not found"}], True)])
        planned = [{"tool": "fill", "uid": "1_2", "value": "x"}, {"tool": "take_screenshot", "fullPage": True},
                   {"tool": "click", "uid": "9_9"}, {"tool": "fill", "uid": "1_3", "value": "y"}]
        called = lambda name: os.path.join(workdir, "004-" + name)
        result = steps.run(fake, 7, planned, called)
        content = result["content"]
        report = text_of(content)
        check("a queue that stopped at a failure is an error result", result["isError"] is True)
        check("every step is sent with the tab's page id", all(args.get("pageId") == 7 for _, args in fake.calls))
        check("a fill reads its element before it runs", fake.calls[0][0] == "evaluate_script"
              and fake.calls[0][1]["args"] == ["1_2"], repr(fake.calls[0]))
        check("a step's own arguments are passed through", fake.calls[1] == ("fill", {"uid": "1_2", "value": "x", "pageId": 7}))
        check("the queue stops at the first failure", [tool for tool, _ in fake.calls] == [
            "evaluate_script", "fill", "take_screenshot", "click", "take_snapshot"], repr(fake.calls))
        check("the report heads each step with its number, tool and outcome",
              "--- 1 fill ok" in report and "--- 3 click FAILED" in report, report)
        check("the report names the steps not run", "--- not run: 4 fill" in report, report)
        check("the report ends with the page as it is now, as a view", report.rstrip().endswith('uid=1_0 RootWebArea "Form"'), report)
        check("and saves that snapshot whole", open(called("page-now-snapshot.txt"), encoding="utf-8").read().endswith("  uid=1_1 generic\n"))
        check("an image a step returned comes back as an image", content[1:] == [image])

        class Form(FakeDevtools):
            """FakeDevtools whose evaluate_script answers as checked.FILL_JS would: reads maps a uid to its reads,
            given in turn with the last repeated (a plain text box for a uid not in it); one uid gets its read alone,
            several a list."""

            def __init__(self, answers, reads=None):
                super().__init__(answers)
                self.reads = reads or {}

            def text(self, tool, arguments, wait=None):
                if tool != "evaluate_script":
                    return super().text(tool, arguments, wait)
                self.calls.append((tool, arguments))
                turns = [self.reads.get(uid, [{"kind": "box"}]) for uid in arguments["args"]]
                found = [answers.pop(0) if answers[1:] else answers[0] for answers in turns]
                return "```json\n%s\n```" % json.dumps(found if found[1:] else found[0])

        ok = ([{"type": "text", "text": "Filled"}], False)
        run = [{"tool": "fill", "uid": "1_2", "value": "a"}, {"tool": "fill", "uid": "1_3", "value": "b"},
               {"tool": "fill", "uid": "1_4", "value": "c"}]
        fake = Form([ok, ok, ok, ok])
        report = text_of(steps.run(fake, 7, run + [{"tool": "click", "uid": "1_5"}],
                                   lambda name: os.path.join(workdir, "011-" + name))["content"])
        check("fills in a row are read in one call, and each text box is filled without a read of its own",
              [tool for tool, _ in fake.calls] == ["evaluate_script", "fill", "fill", "fill", "click"]
              and fake.calls[0][1]["args"] == ["1_2", "1_3", "1_4"]
              and all("--- %d fill ok" % n in report for n in (1, 2, 3)), repr(fake.calls) + report)
        fake = Form([ok], {"1_3": [{"kind": "box", "disabled": True}]})
        result = steps.run(fake, 7, run, lambda name: os.path.join(workdir, "012-" + name))
        report = text_of(result["content"])
        check("a fill the one read refuses is read again at its own step, and fails when its own read refuses too",
              [tool for tool, _ in fake.calls] == ["evaluate_script", "fill", "evaluate_script", "take_snapshot"]
              and fake.calls[2][1]["args"] == ["1_3"] and result["isError"] and "--- 2 fill FAILED" in report
              and "element 1_3 is disabled" in report and "--- not run: 3 fill" in report, repr(fake.calls) + report)
        fake = Form([ok, ok, ok], {"1_3": [{"kind": "box", "disabled": True}, {"kind": "box"}]})
        result = steps.run(fake, 7, run, lambda name: os.path.join(workdir, "013-" + name))
        check("and is filled when its own read, after the fills before it, passes",
              [tool for tool, _ in fake.calls] == ["evaluate_script", "fill", "evaluate_script", "fill", "fill"]
              and not result["isError"], repr(fake.calls))
        fake = Form([ok, ok, ok], {"1_3": [{"kind": "select", "options": ["b"]}]})
        steps.run(fake, 7, run, lambda name: os.path.join(workdir, "014-" + name))
        check("a fill that is not a text box, and each after it, reads its own element, as filling it may change the rest",
              [tool for tool, _ in fake.calls] == ["evaluate_script", "fill", "evaluate_script", "fill", "evaluate_script",
                                                    "fill"], repr(fake.calls))
        fake = Form([ok, ok, ok], {"1_2": [{"kind": "select", "options": ["a"]}]})
        steps.run(fake, 7, run, lambda name: os.path.join(workdir, "016-" + name))
        check("a run's first fill is judged by the one read whatever it reads, as that read is as fresh as its own",
              [tool for tool, _ in fake.calls] == ["evaluate_script", "fill", "evaluate_script", "fill", "evaluate_script",
                                                    "fill"], repr(fake.calls))
        fake = FakeDevtools([ok, ok])
        steps.run(fake, 7, run[:2], lambda name: os.path.join(workdir, "015-" + name))
        check("when the one read fails, each fill reads its own element", [tool for tool, _ in fake.calls] == [
            "evaluate_script", "evaluate_script", "fill", "evaluate_script", "fill"], repr(fake.calls))
        dialog = "# Open dialog\nalert: Heads up.\nCall handle_dialog to handle it before continuing."
        fake = FakeDevtools([([{"type": "text", "text": "Error: Failed to interact with the element with uid 1_1.\n" + dialog}], True),
                             ([{"type": "text", "text": "Successfully accepted the dialog\n## Pages\n1: Other tab (https://example.com) [selected]\n2: Mine\n"
                                                        "Note: the previously selected page was closed. Page 3 is now selected."}], False)])
        result = steps.run(fake, 7, [{"tool": "click", "uid": "1_1"}, {"tool": "handle_dialog", "action": "accept"}], called)
        report = text_of(result["content"])
        check("a click that opened a dialog counts as done, so the handle_dialog after it runs",
              not result["isError"] and "--- 1 click ok" in report and "--- 2 handle_dialog ok" in report, report)
        check("and chrome-devtools-mcp's list of every page, and its note on which it selected, are left out of a report",
              "Other tab" not in report and "previously selected" not in report, report)
        fake = FakeDevtools([([{"type": "text", "text": "Error: A dialog is open (alert: Heads up.).\n" + dialog}], True)])
        report = text_of(steps.run(fake, 7, [{"tool": "click", "uid": "1_1"}], called)["content"])
        check("a step refused because a dialog was already open still fails", "--- 1 click FAILED" in report, report)
        blocked = Blocked()
        report = text_of(steps.run(blocked, 7, [{"tool": "expect", "uid": "1_1", "value": "x"}], called)["content"])
        check("a checked step a dialog stopped still fails, since its read-back never ran", "--- 1 expect FAILED" in report, report)
        fake = FakeDevtools([cdp.CdpError("chrome-devtools-mcp exited during tools/call; see x.log")])
        report = text_of(steps.run(fake, 1, [{"tool": "click", "uid": "1_1"}], called, restarted=True)["content"])
        check("a process that dies mid-step is a failed step, not a crash", "--- 1 click FAILED" in report and "exited" in report, report)
        check("a queue on a restarted process says old uids are gone", report.startswith("note:") and "uids" in report, report)
        navigations = ["https://docs.example/d/1/edit?s=a#s=a", "https://docs.example/d/1/edit?s=b#s=b",
                       "https://docs.example/d/1/edit#only", "https://other.example/", "https://docs.example/d/1/edit?s=c"]
        fake = FakeDevtools([([{"type": "text", "text": "Pressed.\nPage navigated to %s." % url}], False) for url in navigations[:3]]
                            + [([{"type": "text", "text": "Successfully navigated to %s." % navigations[3]}], False),
                               ([{"type": "text", "text": "Clicked.\nPage navigated to %s." % navigations[4]}], False)])
        report = text_of(steps.run(fake, 7, [{"tool": "press_key", "key": "ArrowDown"}] * 3
                                   + [{"tool": "navigate_page", "url": navigations[3]}, {"tool": "click", "uid": "1_1"}],
                                   called)["content"])
        check("a navigation with a query, on the scheme, host and path the one before it named, is cut to its query and "
              "fragment; the first, one with no query, and one after a navigate_page stay whole",
              "Page navigated to %s." % navigations[0] in report and "Page navigated to ?s=b#s=b (scheme, host and path as "
              "before)." in report and "Page navigated to %s." % navigations[2] in report
              and "Page navigated to %s." % navigations[4] in report, report)
        report = text_of(steps.run(FakeDevtools([]), 7, [{"tool": "paste", "text": "x"}], called)["content"])
        check("a paste in a queue not given the tab's target fails before it presses a key",
              "--- 1 paste FAILED" in report and "not given" in report, report)

        views(workdir)
        waits()

        workers = Workers(workdir)
        first = workers.get("ab12", "T1", STAND_IN)
        check("a tab keeps one worker", workers.get("ab12", "T1", STAND_IN) is first)
        stopped = []
        setattr(first, "stop", lambda: stopped.append(True))
        workers.drop("ab12")
        check("dropping a tab stops its worker and forgets it",
              stopped == [True] and workers.get("ab12", "T1", STAND_IN) is not first)
        kept, gone = workers.get("keep", "T2", STAND_IN), workers.get("gone", "T3", STAND_IN)
        setattr(gone, "stop", lambda: stopped.append("gone"))
        setattr(kept, "stop", lambda: stopped.append("kept"))
        workers.drop_closed(lambda tab: tab == "gone")
        check("a listing stops the worker of every tab found closed, and only those", stopped[1:] == ["gone"], repr(stopped))

        class Process:
            def __init__(self, alive):
                self.running = alive

            def alive(self):
                return self.running

            def close(self):
                self.running = False

        carried = workers.get("cary", "T6", STAND_IN, workers.started - 1)
        fresh = workers.get("frsh", "T7", STAND_IN, workers.started + 1)
        check("a tab older than this server is carried over, so its first report says its uids are gone",
              carried._carried and not fresh._carried)
        paused, idle = workers.get("paus", "T4", STAND_IN), workers.get("idle", "T5", STAND_IN)
        setattr(paused, "_devtools", Process(True))
        setattr(idle, "_devtools", Process(False))
        check("pausing stops the process of each running tab named, counting them",
              workers.pause(["paus", "idle", "none"], lambda: True) == 1 and not paused._devtools.alive(), repr(stopped))
        check("and leaves the dead process, so the next queue's report says the uids are gone",
              paused._devtools is not None and workers.pause(["paus"], lambda: True) == 0)
        setattr(paused, "_devtools", Process(True))
        check("but stops none of a session a call has resumed since", workers.pause(["paus"], lambda: False) == 0
              and paused._devtools.alive())
    finally:
        steps.GAP = gap
        shutil.rmtree(workdir, ignore_errors=True)


SNAPSHOT = """uid=1_0 RootWebArea "Apply" url="https://example.com/apply"
  uid=1_1 generic
    uid=1_2 heading "Your details" level="2"
    uid=1_3 LabelText
      uid=1_4 StaticText "Country"
    uid=1_5 combobox "Country" expandable haspopup="menu" invalid="true" value="United States"
      uid=1_6 option "Canada" selectable value="Canada"
      uid=1_7 option "United States" selectable selected value="United States"
    uid=1_8 textbox "Why us?" multiline value="Line one
Line two"
    uid=1_9 textbox
  uid=2_0 combobox "Clearance" expandable haspopup="listbox"
  uid=2_1 listbox
    uid=2_2 option "Say "yes" here" selectable value="Say "yes" here"
  uid=2_3 image
  uid=2_4 LineBreak "
"
  uid=2_5 StaticText " "
  uid=3_0 combobox "Skills"
    uid=3_1 option "Python" selectable
      uid=3_2 button "Remove Python"
    uid=3_3 searchbox "Search skills"
  uid=3_4 textbox "Cover letter" multiline value="Dear team,
#LI-Remote
I build things."
  uid=3_5 generic"""


def views(workdir):
    """steps.view over a snapshot holding each kind of line it treats differently."""
    path = os.path.join(workdir, "001-step1-snapshot.txt")
    reply = "Clicked.\n## Latest page snapshot\n" + SNAPSHOT + "\n\n## Console messages\nnone"
    text, missing = steps.view(reply, path)
    lines = text.split("\n")
    check("a view keeps what came before and after the snapshot", lines[0] == "Clicked." and lines[-2:] == ["## Console messages", "none"], text)
    check("a view's header names where the whole snapshot is saved", lines[1] == "## Latest page snapshot (view; saved whole to %s)" % path, lines[1])
    check("the whole snapshot is saved there", open(path, encoding="utf-8").read() == SNAPSHOT + "\n")
    check("a view keeps lines with words and controls, drops the rest, and indents by the kept lines it sits under",
          lines[2:-3] == ['uid=1_0 RootWebArea "Apply" url="https://example.com/apply"',
                          '  uid=1_2 heading "Your details" level="2"',
                          '  uid=1_4 StaticText "Country"',
                          '  uid=1_5 combobox "Country" = "United States" invalid="true" (2 options)',
                          '  uid=1_8 textbox "Why us?" multiline value="Line one',
                          'Line two"',
                          '  uid=1_9 textbox',
                          '  uid=2_0 combobox "Clearance" expandable haspopup="listbox"',
                          '  uid=2_1 listbox',
                          '    uid=2_2 option "Say "yes" here" selectable value="Say "yes" here"',
                          '  uid=3_0 combobox "Skills"',
                          '    uid=3_1 option "Python" selectable',
                          '      uid=3_2 button "Remove Python"',
                          '    uid=3_3 searchbox "Search skills"',
                          '  uid=3_4 textbox "Cover letter" multiline value="Dear team,',
                          '#LI-Remote',
                          'I build things."'] and not missing,
          "\n".join(lines))
    text, missing = steps.view(reply, path, under="1_5")
    check("a view under a native select lists its options",
          text.split("\n")[2:5] == ['uid=1_5 combobox "Country" expandable haspopup="menu" invalid="true" value="United States"',
                                     '  uid=1_6 option "Canada" selectable value="Canada"',
                                     '  uid=1_7 option "United States" selectable selected value="United States"'], text)
    text, missing = steps.view(reply, path, under="2_1", full=True)
    check("full under a uid gives that element's lines as they were written",
          text.split("\n")[1:4] == ["## Latest page snapshot (full under 2_1; saved whole to %s)" % path, "  uid=2_1 listbox",
                                     '    uid=2_2 option "Say "yes" here" selectable value="Say "yes" here"'], text)
    text, missing = steps.view(reply, path, full=True)
    check("full gives the whole snapshot", SNAPSHOT + "\n\n## Console" in text and not missing, text)
    odd = "\n".join(['uid=1_0 RootWebArea "Odd" url="data:text/html,x"',
                     '  uid=1_1 combobox "Pick "one" required" expandable haspopup="menu" required value="foo" bar baz"',
                     '    uid=1_2 option "foo" bar baz" selectable selected value="foo" bar baz"',
                     '    uid=1_3 option "plain" selectable value="plain"',
                     '  uid=1_4 textbox "Letter" multiline value="hello', '', '## Fake heading', 'more"',
                     '  uid=1_5 StaticText "Last"', '    uid=1_6 InlineTextBox "Last"'])
    text, _ = steps.view("## Latest page snapshot\n" + odd, path)
    check("a collapsed select keeps a name and value holding quotes followed by words",
          'uid=1_1 combobox "Pick "one" required" = "foo" bar baz" required (2 options)' in text, text)
    check("a value's own blank line and ## line do not end the snapshot, in the view or the saved file",
          'uid=1_5 StaticText "Last"' in text and open(path, encoding="utf-8").read() == odd + "\n", text)
    check("a verbose snapshot's InlineTextBox copies of the text above them are left out", "InlineTextBox" not in text, text)
    marked = ('uid=1_0 RootWebArea "M"\n  uid=1_1 combobox "Country" expandable haspopup="menu" value="Canada" '
              '[selected in the DevTools Elements panel]\n    uid=1_2 option "Canada" selectable selected value="Canada"')
    text, _ = steps.view("## Latest page snapshot\n" + marked, path)
    check("a collapsed select keeps its value when DevTools has it selected", '= "Canada" (1 options)' in text, text)
    dated = "\n".join(['uid=1_0 RootWebArea "D"', '  uid=1_1 StaticText "Birthday"',
                       '  uid=1_2 Date "Birthday" value="1957-08-01"',
                       '    uid=1_3 spinbutton "Month Month" value="8" valuemax="12" valuemin="1" valuetext=""',
                       '    uid=1_4 StaticText "/"',
                       '    uid=1_5 button "Show date picker Show date picker" haspopup="menu"',
                       '  uid=1_6 InputTime "Start"', '    uid=1_7 spinbutton "Hours Hours" value="0"',
                       '  uid=1_8 DateTime "Month"', '    uid=1_9 spinbutton "Year Year" value="0"'])
    text, _ = steps.view("## Latest page snapshot\n" + dated, path)
    check("a date, time or month field is one line in a view, its parts and picker button left out",
          text.split("\n")[1:] == ['uid=1_0 RootWebArea "D"', '  uid=1_1 StaticText "Birthday"',
                                   '  uid=1_2 Date "Birthday" value="1957-08-01"', '  uid=1_6 InputTime "Start"',
                                   '  uid=1_8 DateTime "Month"'], text)
    text, _ = steps.view("## Latest page snapshot\n" + dated, path, under="1_2")
    check("and a view under its uid shows its parts", 'uid=1_3 spinbutton "Month Month"' in text, text)
    fake = FakeDevtools([([{"type": "text", "text": "## Latest page snapshot\n" + 'uid=1_0 RootWebArea "P"\n  uid=1_1 textbox "Notes" multiline value="a\n## Pages\nb"'}], False)])
    report = text_of(steps.run(fake, 3, [{"tool": "take_snapshot"}], lambda name: os.path.join(workdir, "006-" + name))["content"])
    check("a value's own ## Pages line inside a snapshot is kept", "## Pages" in report, report)
    text, missing = steps.view(reply, path, under="9_9")
    check("a view under a uid the snapshot lacks says so", missing and "(view under 9_9;" in text
          and "no element has uid=9_9" in text, text)
    os.remove(path)
    check("a reply with no snapshot is left alone and saves nothing",
          steps.view("Clicked.", path) == ("Clicked.", False) and not os.path.exists(path))

    fake = FakeDevtools([([{"type": "text", "text": "## Latest page snapshot\n" + SNAPSHOT}], False)])
    report = text_of(steps.run(fake, 3, [{"tool": "take_snapshot", "under": "2_1", "full": False, "verbose": True}],
                               lambda name: os.path.join(workdir, "002-" + name))["content"])
    check("take_snapshot's under and full are not sent on to chrome-devtools-mcp", fake.calls == [("take_snapshot", {"verbose": True, "pageId": 3})],
          repr(fake.calls))
    check("and its reply comes back as a view, the snapshot saved as 002-step1-snapshot.txt",
          "(view under 2_1; saved whole to %s)" % os.path.join(workdir, "002-step1-snapshot.txt") in report
          and "uid=2_0" not in report, report)
    fake = FakeDevtools([([{"type": "text", "text": "## Latest page snapshot\n" + SNAPSHOT}], False)])
    report = text_of(steps.run(fake, 3, [{"tool": "take_snapshot", "under": "9_9"}, {"tool": "click", "uid": "1_9"}],
                               lambda name: os.path.join(workdir, "003-" + name))["content"])
    check("a take_snapshot under a uid it does not find fails the queue", "--- 1 take_snapshot FAILED" in report
          and "--- not run: 2 click" in report, report)
    long = "x" * (steps.REPLY_MOST + 10)
    report = text_of(steps.run(FakeDevtools([([{"type": "text", "text": long}], False)]), 3, [{"tool": "evaluate_script"}],
                               lambda name: os.path.join(workdir, "005-" + name))["content"])
    saved = os.path.join(workdir, "005-step1-reply.txt")
    check("a step's reply longer than REPLY_MOST is cut, and the whole of it saved",
          "(10 characters more; the whole reply is saved to %s)" % saved in report and open(saved, encoding="utf-8").read() == long + "\n", report[-200:])
    lines = "\n".join("line %05d %s" % (n, "y" * 80) for n in range(1000))
    report = text_of(steps.run(FakeDevtools([([{"type": "text", "text": lines}], False)]), 3, [{"tool": "evaluate_script"}],
                               lambda name: os.path.join(workdir, "006-" + name))["content"])
    kept = report.split("\n--- (")[0].split("\n")[-1]
    check("a reply of many lines is cut after a whole line", re.fullmatch(r"line \d{5} y{80}", kept) is not None, kept)
    for label, step, words in (("an under that is not a string", {"tool": "take_snapshot", "under": 3}, "under"),
                               ("a full that is not true or false", {"tool": "take_snapshot", "full": "yes"}, "full"),
                               ("a find that is not a regex", {"tool": "take_snapshot", "find": "(x"}, "find")):
        said = refusal(lambda: steps.check([step], {"take_snapshot": {}}), steps.StepError)
        check("%s is refused before any step runs" % label, "step 1" in said and words in said, said)

    text, _ = steps.view(reply, path, find="country|WHY")
    check("find keeps only the view lines its regex matches, ignoring case, and says how many",
          text.split("\n")[1:6] == ['## Latest page snapshot (view, lines matching "country|WHY": 3 of 14; saved whole to %s)' % path,
                                     '  uid=1_4 StaticText "Country"',
                                     '  uid=1_5 combobox "Country" = "United States" invalid="true" (2 options)',
                                     '  uid=1_8 textbox "Why us?" multiline value="Line one', 'Line two"'], text)
    words = "\n".join(['uid=5_0 RootWebArea "Deck"', '  uid=5_1 StaticText "Project"', '  uid=5_2 StaticText "objective"',
                       '  uid=5_3 StaticText "Lorem"', '  uid=5_4 StaticText " "', '  uid=5_5 StaticText "ipsum"',
                       '  uid=5_9 StaticText "Size"', '  uid=5_10 StaticText "Width"', '  uid=5_11 StaticText "bold" description="b"',
                       '  uid=5_12 button "Next"', '  uid=5_13 StaticText "Two words"', '  uid=5_14 StaticText "a"',
                       '  uid=5_15 StaticText "b"', '  uid=5_16 StaticText "c"'])
    text, _ = steps.view("## Latest page snapshot\n" + words, path)
    check("three or more one-word lines whose uids count up are one line; a blank, a gap, two words, an attribute end one",
          text.split("\n")[2:] == ['  uid=5_1..3 StaticText "Project objective Lorem"', '  uid=5_5 StaticText "ipsum"',
                                    '  uid=5_9 StaticText "Size"', '  uid=5_10 StaticText "Width"',
                                    '  uid=5_11 StaticText "bold" description="b"', '  uid=5_12 button "Next"',
                                    '  uid=5_13 StaticText "Two words"', '  uid=5_14..16 StaticText "a b c"'], text)
    step = {"tool": "click", "uid": "uid=5_1..3"}
    steps._bare_uids(step)
    check("a uid copied from a run's line acts on the run's first word", step["uid"] == "5_1", step["uid"])

    class LongPage(FakeDevtools):
        def text(self, tool, arguments, wait=None):
            return "## Latest page snapshot\n" + "\n".join('uid=1_%d button "Button %d"' % (n, n) for n in range(2000))

    report = text_of(steps.run(LongPage([([{"type": "text", "text": "Error: Element uid 9_9 not found"}], True)]), 3,
                               [{"tool": "click", "uid": "9_9"}], lambda name: os.path.join(workdir, "008-" + name))["content"])
    check("a failed queue's report stays under ERROR_MOST, its view of the page now cut and saved whole",
          len(report) <= steps.ERROR_MOST and "008-page-now-reply.txt)" in report, "%d: %s" % (len(report), report[-200:]))
    report = text_of(steps.run(FakeDevtools([([{"type": "text", "text": "Timed out after waiting 5000ms"}], True)]), 3,
                               [{"tool": "wait_for", "text": ["Slideshow"]}], lambda name: os.path.join(workdir, "009-" + name))["content"])
    check("a failed wait_for says what it matches", "accessible name is exactly one" in report, report)

    # A handbook of 60 parts, each a heading and 12 lines of text, about 60,000 characters as a view.
    rows, n = [], 1
    for part in range(1, 61):
        rows.append('  uid=1_%d heading "Part %d" level="2"' % (n, part))
        rows.extend('  uid=1_%d StaticText "Row %d of part %d, with some words in it to fill a line"' % (n + k, k, part)
                    for k in range(1, 13))
        n += 13
    big = "## Latest page snapshot\n" + 'uid=1_0 RootWebArea "Handbook" url="https://example.com/"\n' + "\n".join(rows)
    path = os.path.join(workdir, "010-step1-snapshot.txt")
    text, missing = steps.view(big, path)
    shown = text.split("\n--- cut: ")[0]
    note = text.split("\n--- cut: ")[1] if "\n--- cut: " in text else ""
    last = re.findall(r"uid=(1_\d+)", shown)[-1]
    check("a view over VIEW_MOST is cut at a line, its first line not counted",
          not missing and len("\n".join(shown.split("\n")[2:])) <= steps.VIEW_MOST and shown.endswith('"'), len(shown))
    check("and its note says how many lines are left, the call to read on, and names the first HEADINGS_MOST headings "
          "below the cut, counting the rest",
          note.startswith("%d more lines below. Read on with take_snapshot {\"after\": \"%s\"}, or from a heading" % (
              len(rows) + 1 - len(shown.split("\n")) + 1, last))
          and [line for line in note.split("\n") if ' heading "Part ' in line][0] == 'uid=1_144 heading "Part 12" level="2"'
          and len([line for line in note.split("\n") if ' heading "Part ' in line]) == steps.HEADINGS_MOST
          and note.endswith("(and 9 more headings)"), note[:300])
    text, missing = steps.view(big, path, after=last)
    check("after reads on from the line after that uid, with its own cut",
          not missing and "after %s" % last in text.split("\n")[0]
          and text.split("\n")[1].startswith("  uid=1_%d " % (int(last.split("_")[1]) + 1)) and "--- cut: " in text,
          text[:300])
    runs = "## Latest page snapshot\n" + "\n".join(['uid=1_0 RootWebArea "Words"', '  uid=1_1 heading "Top" level="1"'] + [
        '  uid=1_%d StaticText "w%d"' % (k, k) for k in range(2, 7)] + ['  uid=1_7 StaticText "End of the words"'])
    text, missing = steps.view(runs, path, after="1_4")
    check("after takes any uid of the snapshot, one folded into a run of words included",
          not missing and text.split("\n")[1:] == ['  uid=1_7 StaticText "End of the words"'], text)
    text, missing = steps.view(runs, path, after="3_4")
    check("an after uid the snapshot lacks fails the step, saying so",
          missing and text.endswith("no element has uid=3_4 in this snapshot"), text)
    mixed = "## Latest page snapshot\n" + "\n".join(['uid=1_0 RootWebArea "Feed"', '  uid=1_1 StaticText "Old post"',
                                                     '  uid=2_7 StaticText "New post"', '  uid=1_2 StaticText "Footer"'])
    text, missing = steps.view(mixed, path, after="1_1")
    check("after goes by place, as a snapshot taken after the page changed mixes 1_x and 2_x uids",
          not missing and text.split("\n")[1:] == ['  uid=2_7 StaticText "New post"', '  uid=1_2 StaticText "Footer"'], text)
    text, _ = steps.view(big, path, find="Row")
    check("the call to read on keeps the view's own options", '{"find": "Row", "after": "1_' in text, text[-400:])
    rooted = big.replace('url="https://example.com/"', 'url="data:text/html,%s"' % ("x" * 12000))
    text, _ = steps.view(rooted, path)
    check("a long first line, like a data: page's url, does not eat the view",
          text.split("\n")[1].startswith('uid=1_0 RootWebArea') and len(text.split("\n--- cut: ")[0]) > 12000 + 9000, len(text))
    text, _ = steps.view(big, path, find=r"^\s*uid=\S+ heading")
    check("a view within VIEW_MOST, like a find's, is not cut", "--- cut: " not in text and text.count(" heading ") == 60, text[:200])
    role = "## Latest page snapshot\n" + "\n".join([
        'uid=1_0 RootWebArea "SYSTEM_PROMPT.txt"', '  uid=1_1 StaticText "You are a helpful agent."',
        '  uid=1_2 StaticText "<ROLE>"', '  uid=1_3 StaticText "Your primary role is to assist users."',
        '  uid=1_4 StaticText "* If the user asks a question, just answer it."', '  uid=1_5 StaticText "</ROLE>"',
        '  uid=1_6 StaticText "Take care."'])
    text, _ = steps.view(role, path, find="ROLE")
    check("between two lines find matches, a line says how many it left out there; none before the first or after "
          "the last", text.split("\n")[1:] == ['  uid=1_2 StaticText "<ROLE>"',
                                               '  uid=1_3 StaticText "Your primary role is to assist users."',
                                               '  (1 line left out by find)', '  uid=1_5 StaticText "</ROLE>"'], text)
    text, _ = steps.view(big, path, find=r"^\s*uid=\S+ heading", after="1_14")
    check("and after keeps those lines, still none before the first match", text.split("\n")[1:4] == [
        '  uid=1_27 heading "Part 3" level="2"', '  (12 lines left out by find)', '  uid=1_40 heading "Part 4" level="2"'],
          text[:300])
    text, _ = steps.view(big, path, find="heading|Row [123] of")
    shown, note = text.split("\n--- cut: ")
    check("a cut find view's note counts the lines it left below, not the lines saying what find left out", int(
        note.split(" ")[0]) + sum(1 for line in shown.split("\n")[1:] if " uid=" in line) == 240, note[:80])


class Page:
    """A page whose snapshot is page(n) at its nth take_snapshot."""

    def __init__(self, page):
        self.page, self.taken = page, 0

    def text(self, tool, arguments, wait=None):
        self.taken += 1
        return "## Latest page snapshot\n" + self.page(self.taken)


class Clock:
    """A module's time, stood in for: sleep moves it on at once, so a wait of seconds takes none."""

    def __init__(self):
        self.now = 1000.0

    def time(self):
        return self.now

    monotonic = time

    def sleep(self, seconds):
        self.now += seconds


def waits():
    """checked.wait's snapshot conditions over pages whose text goes, stays, or always changes, on a Clock."""
    clock = Clock()
    saved, checked.time = checked.time, clock
    try:
        root = 'uid=1_0 RootWebArea "Form" url="https://example.com/Parsing"'
        going = Page(lambda n: root + ('\n  uid=1_1 StaticText "Parsing your resume"' if n < 3 else ""))
        said, failed = checked.run(going, 1, {"tool": "wait", "gone": "Parsing your resume"})
        check("a wait for text to go passes once a snapshot no longer holds it",
              not failed and "is off the page" in said and going.taken == 3, said)
        late = Page(lambda n: root + ('\n  uid=1_1 StaticText "Parsing your resume"' if 2 <= n < 4 else ""))
        said, failed = checked.run(late, 1, {"tool": "wait", "gone": "Parsing your resume"})
        check("a wait for text to go waits for it to show first, when the page starts its work late",
              not failed and "is off the page" in said and late.taken == 4, said)
        for text in ("Parsing", "RootWebArea"):
            said, failed = checked.run(Page(lambda n: root), 1, {"tool": "wait", "gone": text, "timeout": 1000})
            check("a wait for text found only in a url, uid or role fails once its time to show is up (%s)" % text,
                  failed and "did not show on the page in the wait's first 1s" in said, said)
        said, failed = checked.run(Page(lambda n: root), 1, {"tool": "wait", "gone": "Form", "timeout": 500})
        check("a wait for text that stays fails at its timeout, saying so", failed and "still on the page after 500ms" in said, said)
        began = clock.monotonic()
        said, failed = checked.run(Page(lambda n: root), 1, {"tool": "wait", "still": 500, "timeout": 3000})
        check("a wait for a page that does not change passes once still ms have gone by",
              not failed and "not changed for 500ms" in said and clock.monotonic() - began >= 0.5, said)
        said, failed = checked.run(Page(lambda n: 'uid=1_0 RootWebArea "Tick %d"' % n), 1, {"tool": "wait", "still": 500, "timeout": 1500})
        check("a wait for a page that keeps changing fails at its timeout", failed and "did not stay unchanged" in said, said)
        began = clock.monotonic()
        said, failed = checked.run(Page(lambda n: 'uid=1_0 RootWebArea "Tick %d"' % n), 1,
                                   {"tool": "wait", "still": 500, "timeout": 20000}, left=1.0)
        check("a wait longer than the queue has left is cut to it, and says so when it fails",
              failed and "its timeout was cut to the 1.0s the queue had left" in said and clock.monotonic() - began < 5, said)
    finally:
        checked.time = saved


class FakeDialogs:
    """The connection a dialogs.Answerer holds: a dialog opens at its nth wait, or never."""

    def __init__(self, opens=None):
        self.opens, self.waits, self.calls = opens, 0, []

    def call(self, method, session=None, **params):
        self.calls.append((method, params))
        return {"sessionId": "S1"} if method == "Target.attachToTarget" else {}

    def wait_for(self, event, session=None, timeout=20.0):
        self.waits += 1
        if self.opens is not None and self.waits >= self.opens:
            return {"type": "prompt", "message": "Your name?"}
        time.sleep(0.01)
        raise cdp.CdpError("%s never arrived" % event)

    def close(self):
        pass


def dialogs_offline():
    """dialogs.Answerer against a stand-in connection, and steps.run handing it a handle_dialog step."""
    connection = FakeDialogs(opens=3)
    answerer = dialogs.Answerer("T1", {"tool": "handle_dialog", "action": "accept", "promptText": "Joshua"}, lambda: connection)
    answerer.start_listening()
    said = answerer.answered(2)
    check("a dialog that opens while the answerer listens is answered as the handle_dialog step asks",
          said == 'the prompt "Your name?" was accepted with "Joshua" as it opened'
          and ("Page.handleJavaScriptDialog", {"accept": True, "promptText": "Joshua"}) in connection.calls, repr((said, connection.calls)))
    connection = FakeDialogs()
    answerer = dialogs.Answerer("T1", {"tool": "handle_dialog", "action": "dismiss"}, lambda: connection)
    answerer.start_listening()
    check("no dialog within the late wait leaves nothing answered", answerer.answered(0.02) is None
          and all(method != "Page.handleJavaScriptDialog" for method, _ in connection.calls), repr(connection.calls))

    def unreachable():
        raise cdp.CdpError("nothing is listening")

    answerer = dialogs.Answerer("T1", {"tool": "handle_dialog", "action": "accept"}, unreachable)
    answerer.start_listening()
    check("an answerer that cannot reach the tab answers nothing", answerer.answered(0.2) is None)

    saved, gap = dialogs.Answerer, steps.GAP
    workdir = tempfile.mkdtemp(prefix="browser-dialogs-")
    try:
        steps.GAP = 0  # a stand-in page has nothing to react to between steps
        called = lambda name: os.path.join(workdir, "001-" + name)
        dialogs.Answerer = lambda target, handle, connect: saved(target, handle, lambda: FakeDialogs(opens=1))
        fake = FakeDevtools([([{"type": "text", "text": "Successfully clicked on the element"}], False)])
        result = steps.run(fake, 7, [{"tool": "click", "uid": "1_1"}, {"tool": "handle_dialog", "action": "accept"}],
                           called, target="T1")
        report = text_of(result["content"])
        check("the dialog a handle_dialog step waits on is answered by the queue's own answerer, not chrome-devtools-mcp",
              not result["isError"] and "--- 2 handle_dialog ok" in report and "was accepted as it opened" in report
              and [tool for tool, _ in fake.calls] == ["click"], report)
        dialogs.Answerer = lambda target, handle, connect: saved(target, handle, lambda: FakeDialogs())
        saved_late, dialogs.LATE = dialogs.LATE, 0.02
        fake = FakeDevtools([([{"type": "text", "text": "Successfully clicked on the element"}], False),
                             ([{"type": "text", "text": "Error: No open dialog found"}], True)])
        result = steps.run(fake, 7, [{"tool": "click", "uid": "1_1"}, {"tool": "handle_dialog", "action": "accept"}],
                           called, target="T1")
        dialogs.LATE = saved_late
        check("when no dialog opens, the handle_dialog step goes to chrome-devtools-mcp and fails as it says",
              result["isError"] and "No open dialog found" in text_of(result["content"])
              and [tool for tool, _ in fake.calls][:2] == ["click", "handle_dialog"], text_of(result["content"]))
        made = []
        dialogs.Answerer = lambda target, handle, connect: made.append(handle) or saved(target, handle, lambda: FakeDialogs())
        fake = FakeDevtools([([{"type": "text", "text": "ran"}], False),
                             ([{"type": "text", "text": "Successfully accepted the dialog"}], False)])
        result = steps.run(fake, 7, [{"tool": "evaluate_script", "function": "() => confirm('x')"},
                                     {"tool": "handle_dialog", "action": "accept"}], called, target="T1")
        check("no answerer races a tool that answers its own dialogs",
              made == [] and not result["isError"] and [tool for tool, _ in fake.calls] == ["evaluate_script", "handle_dialog"],
              text_of(result["content"]))
    finally:
        dialogs.Answerer, steps.GAP = saved, gap
        shutil.rmtree(workdir, ignore_errors=True)


class Keys:
    """A connection to a profile's Chrome that records what it is asked. Each Runtime.evaluate answers the next of
    values, an exception as a script error: by default the page handed the text, ready, one paste seen, and the text
    taken back."""

    def __init__(self, values=(None, True, [1, 1], None)):
        self.calls, self.values = [], list(values)

    def call(self, method, session=None, **params):
        self.calls.append((method, params))
        if method == "Target.attachToTarget":
            return {"sessionId": "S1"}
        if method != "Runtime.evaluate":
            return {}
        value = self.values.pop(0)
        if isinstance(value, Exception):
            return {"exceptionDetails": {"text": "Uncaught", "exception": {"description": str(value)}}}
        return {"result": {"value": value}}

    def close(self):
        pass


class Answers:
    """A tab's process whose evaluate_script answers come from a list, in turn."""

    def __init__(self, answers):
        self.answers = list(answers)

    def text(self, tool, arguments, wait=None):
        return "Script ran on page and returned:\n```json\n%s\n```" % json.dumps(self.answers.pop(0))


def paste_offline():
    """checked.paste with stand-ins for the tab, and the connection that hands the page its text and presses the key."""
    paste = lambda keys, answers=({"focused": "editable"},), step=None: checked.paste(
        Answers(list(answers)), 7, step or {"tool": "paste", "text": "x"}, "T1", lambda: keys)
    methods = lambda keys: [method for method, _ in keys.calls]
    keys = Keys()
    said = paste(keys, step={"tool": "paste", "text": 'It\'s "a"'})
    check("paste hands the page its text, checks the focus is where it was handed, presses %s+V once with Chrome's "
          "own paste command, sees the paste, and takes the text back" % checked.PASTE_KEY,
          methods(keys) == ["Target.attachToTarget", "Runtime.evaluate", "Runtime.evaluate", "Input.dispatchKeyEvent",
                            "Input.dispatchKeyEvent", "Runtime.evaluate", "Runtime.evaluate"]
          and keys.calls[0][1] == {"targetId": "T1", "flatten": True}
          and json.dumps('It\'s "a"') in keys.calls[1][1]["expression"]
          and json.dumps(checked.PASTE_PROPERTY) in keys.calls[1][1]["expression"]
          and keys.calls[3][1].get("commands") == ["paste"] and keys.calls[3][1].get("modifiers") == checked.PASTE_BIT
          and keys.calls[4][1].get("type") == "keyUp" and "undo()" in keys.calls[6][1]["expression"], repr(keys.calls))
    check("and without a uid, says nothing reads the text back", "where the focus is; nothing reads them back" in said, said)
    keys = Keys((None, True, [0, 0], None))
    said = refusal(lambda: paste(keys), checked.CheckFailed)
    check("a page that takes no paste key press fails the paste after its one press, and still has the text taken back",
          "took no paste key press" in said and methods(keys).count("Input.dispatchKeyEvent") == 2
          and "undo()" in keys.calls[-1][1].get("expression", ""), said)
    keys = Keys((None, True, [0, 1], None))
    said = refusal(lambda: paste(keys), checked.CheckFailed)
    check("a press the page took whose paste a script of its own had first fails the paste, saying the field "
          "may hold the clipboard", "may hold the clipboard" in said
          and methods(keys).count("Input.dispatchKeyEvent") == 2, said)
    keys = Keys((None, False, None))
    said = refusal(lambda: paste(keys), checked.CheckFailed)
    check("the focus moved where the text was not handed stops the paste before its key is pressed",
          "focus moved" in said and "Input.dispatchKeyEvent" not in methods(keys), said)
    keys = Keys(("the focus is in a frame from another site", None))
    said = refusal(lambda: paste(keys, ({"focused": "a frame from another site"},)), checked.CheckFailed)
    check("paste refuses the focus in a frame from another site, pressing no key, and still takes the text back",
          "frame from another site" in said and "so nothing was pasted" in said
          and "Input.dispatchKeyEvent" not in methods(keys) and "undo()" in keys.calls[-1][1].get("expression", ""), said)
    keys = Keys((None, True, [1, 1], RuntimeError("gone")))
    said = refusal(lambda: paste(keys), checked.CheckFailed)
    check("listeners that could not be taken off after a paste went in fail the step, saying the text was pasted and "
          "to reload the tab", "the text was pasted" in said and "reload the tab" in said and "gone" in said, said)
    keys = Keys()
    said = refusal(lambda: paste(keys, ({"refused": "button"},)), checked.CheckFailed)
    check("paste refuses when nothing that takes text has the focus, handing the page nothing and pressing no key",
          "(button has it), so nothing was pasted" in said and keys.calls == [], said)
    said = refusal(lambda: paste(keys, ({"refused": "readonly"},), {"tool": "paste", "uid": "1_1", "text": "x"}),
                   checked.CheckFailed)
    check("paste with a uid refuses what type refuses, saying so",
          "is a read-only text box, so nothing was pasted" in said and keys.calls == [], said)
    said = paste(Keys(), ({"focused": "textarea"}, {"kind": "value", "value": "a\nb"}), {"tool": "paste", "uid": "1_1", "text": "a\nb"})
    check("paste with a uid reads the text box back", said == 'pasted 3 characters; the field holds "a\\nb"', said)


class Widget:
    """A tab's chrome-devtools-mcp over a stand-in form, answering checked's scripts as they would read it, and
    recording each call as FakeDevtools does. Its dropdown, 1_1, lists the options holding what its text box holds once
    text is typed, as chrome-devtools-mcp lists them; a click on one closes the list and shows shows(text) beside the
    box, or with in_box puts the text in the box, unless takes is False. elsewhere are options already on the page, in
    a <select multiple>. Its other fields are in fields; a click on 1_12, Parse resume, shows a status for its next
    `parse` calls, then fills City, and one on 1_13, Warn later, opens nothing it can see."""

    def __init__(self, options=(), takes=True, shows=lambda text: text, in_box=False, elsewhere=(), parse=4):
        self.options, self.takes, self.shows, self.in_box, self.elsewhere = options, takes, shows, in_box, elsewhere
        self.parse, self.parsing, self.calls = parse, 0, []
        self.typed, self.shown, self.open, self.focus, self.selected = "", "", False, None, False

        def field(kind, value, select, fill, keeps=lambda text: text):
            read = dict({"kind": kind, "value": value}, **({"parts": [value]} if kind == "shown" else {}))
            return {"read": read, "select": select, "fill": fill, "keeps": keeps}

        box = {"kind": "box"}
        self.fields = {  # by uid: Name, I agree, Yes, Auth, Ethnicity, Zip, Phone, Locked, Off and City
            "1_2": field("value", "Joshua Jenkins", {"focused": "text"}, box),
            "1_3": field("checked", "true", {"refused": "checkbox"}, {"kind": "toggle"}),
            "1_4": field("aria-pressed", "true", {"refused": "none"}, {"kind": "other"}),
            "1_5": field("select", "Select", {"refused": "none"}, {"kind": "select", "options": ["Select", "US Citizen"]}),
            "1_6": field("shown", "White (Not Hispanic or Latino)", {"refused": "combobox"}, {"kind": "other"}),
            "1_7": field("value", "", {"focused": "text"}, box, lambda text: text[:5]),  # maxlength=5
            "1_8": field("value", "", {"focused": "tel"}, box, lambda text: "(%s) %s-%s" % (text[:3], text[3:6], text[6:])),
            "1_9": field("value", "fixed", {"refused": "readonly"}, {"kind": "box", "readonly": True}),
            "1_10": field("value", "off", {"refused": "disabled"}, {"kind": "box", "disabled": True}),
            "1_11": field("value", "", {"focused": "text"}, box),
        }

    def listed(self):
        return [text for text in self.options if self.open and self.typed.lower() in text.lower()]

    def call(self, tool, arguments, wait=None):
        return [{"type": "text", "text": self.text(tool, arguments)}], False

    def text(self, tool, arguments, wait=None):
        self.calls.append((tool, arguments))
        if self.parsing:
            self.parsing -= 1
            if not self.parsing:
                self.fields["1_11"]["read"]["value"] = "Los Angeles"
        if tool == "take_snapshot":
            lines = ['uid=1_0 RootWebArea "Widgets"', '  uid=1_1 combobox "Clearance"']
            if self.elsewhere:
                lines.append('  uid=9_0 listbox "Languages known" multiselectable')
                lines += ['    uid=9_%d option "%s" selectable value="%s"' % (n, text, text)
                          for n, text in enumerate(self.elsewhere, 1)]
            if self.parsing:
                lines.append('  uid=3_1 StaticText "Parsing your resume%s"' % ("." * (self.parsing % 4)))
            if self.listed():
                lines.append("  uid=2_0 listbox")
                lines += ['    uid=2_%d option "%s" selectable value="%s"' % (n, text, text)
                          for n, text in enumerate(self.listed(), 1)]
            return "## Latest page snapshot\n" + "\n".join(lines)
        if tool == "evaluate_script":
            return "Script ran on page and returned:\n```json\n%s\n```" % json.dumps(
                self.script(arguments["function"], arguments["args"]))
        if tool == "click":
            at = arguments["uid"]
            if at == "1_12":
                self.parsing, self.fields["1_11"]["read"]["value"] = self.parse, ""
            elif at.startswith("2_") and self.takes:
                chosen = self.listed()[int(at[2:]) - 1]
                self.open = False
                self.typed, self.shown = (chosen, self.shown) if self.in_box else ("", self.shows(chosen))
            elif at == "1_1" or at in self.fields:
                self.focus, self.selected = at, False
        elif tool == "type_text":
            self.put(lambda held: held + arguments["text"])
            self.open = self.open or self.focus == "1_1"
        elif tool == "press_key" and arguments["key"] == "Backspace":
            self.put(lambda held: held[:-1])
        elif tool == "press_key" and arguments["key"] == "Escape":
            self.open = False
        return "Successfully did %s" % tool

    def put(self, edit):
        """Edit what the focused text box holds, its selected text deleted first."""
        if self.focus == "1_1":
            self.typed = edit("" if self.selected else self.typed)
        elif self.focus in self.fields:
            field = self.fields[self.focus]
            field["read"]["value"] = field["keeps"](edit("" if self.selected else field["read"]["value"]))
        self.selected = False

    def script(self, function, uids):
        """What one of checked's scripts returns for the elements uids name."""
        at = uids[0]
        if function == checked.READ_JS:
            if at != "1_1":
                return self.fields[at]["read"]
            if self.typed:
                return {"kind": "value", "value": self.typed}
            return {"kind": "shown", "value": self.shown, "parts": [self.shown] if self.shown else []}
        if function in (checked.CLEAR_JS, checked.SELECT_JS):
            answer = {"refused": "combobox"} if at == "1_1" else self.fields[at]["select"]
            if function == checked.CLEAR_JS or "focused" in answer:
                self.focus, self.selected = at, True
            if function == checked.SELECT_JS:
                return answer
            return len(self.typed if at == "1_1" else self.fields[at]["read"]["value"])
        if checked.FILL_JS not in function:
            raise AssertionError("not one of checked's scripts: %s" % function[:80])
        found = [self.fields[element]["fill"] for element in uids]  # fill_refused's read, or read_fills'
        return found if function.startswith("(...els)") else found[0]


def checked_offline():
    """checked's pick, expect, type and wait, and the fills and the dialog a queue judges or answers itself, against a
    Widget; live.checked_live has what only Chrome can say."""
    workdir = tempfile.mkdtemp(prefix="browser-checked-")
    saved = checked.POLL, checked.SETTLE, steps.GAP
    checked.POLL, checked.SETTLE, steps.GAP = 0.005, 0.02, 0  # what a stand-in needs; theirs are for real pages
    try:
        queue = lambda widget, *planned, **more: steps.run(widget, 7, list(planned), lambda name: os.path.join(
            workdir, "001-" + name), **more)
        tools = lambda widget: [tool for tool, _ in widget.calls]
        clicked = lambda widget: [arguments["uid"] for tool, arguments in widget.calls if tool == "click"]
        near = ["East Los Angeles, California, United States", "Los Angeles, California, United States",
                "Los Angeles County, California, United States"]
        widget = Widget(near, in_box=True)
        said, failed = checked.run(widget, 7, {"tool": "pick", "uid": "1_1", "text": near[1], "search": "Los Angeles"})
        check("pick types search and chooses the exact option among near matches",
              not failed and said == 'picked "%s"; the field holds "%s"' % (near[1], near[1])
              and clicked(widget) == ["1_1", "2_2"], said)
        widget = Widget(["United States +1", "United Kingdom +44"], shows=lambda text: text.split(" ")[-1])
        said, failed = checked.run(widget, 7, {"tool": "pick", "uid": "1_1", "text": "United States +1"})
        check("pick accepts a field that shows a short form of the choice, and says what it shows",
              not failed and said.endswith('the field now shows "+1"'), said)

        widget = Widget(near, in_box=True)
        result = queue(widget, {"tool": "pick", "uid": "1_1", "text": "Los Angeles, Chile", "search": "Los Angeles",
                                "wait": 0.02}, {"tool": "expect", "uid": "1_3", "value": "true"})
        report = text_of(result["content"])
        check("a pick with no exact option fails the queue and says what typing showed",
              result["isError"] and 'no option is exactly "Los Angeles, Chile"' in report
              and 'typing "Los Angeles" showed %s' % ", ".join(json.dumps(text) for text in near) in report, report)
        check("and the steps after it do not run", "--- not run: 2 expect" in report, report)
        check("a failed pick leaves no typed text behind to pass for an answer, and closes the list",
              widget.typed == "" and not widget.listed() and "the text box was emptied" in report, report)
        widget = Widget(["Python"], takes=False)
        said, failed = checked.run(widget, 7, {"tool": "pick", "uid": "1_1", "text": "Python", "wait": 0.02})
        check("a pick whose option click takes nothing fails, though the box holds the typed text",
              failed and 'but the field holds "Python" (read as value; that is only what was typed' in said, said)
        check("and the typed text is cleared", widget.typed == "" and said.endswith("the text box was emptied"), said)
        widget = Widget(["Python", "Rust"], elsewhere=["Python"])
        said, failed = checked.run(widget, 7, {"tool": "pick", "uid": "1_1", "text": "Python"})
        check("pick chooses in its own dropdown, not an option with the same words elsewhere on the page",
              not failed and clicked(widget) == ["1_1", "2_1"] and said == 'picked "Python"; the field holds "Python"',
              said)
        widget = Widget()
        said, failed = checked.run(widget, 7, {"tool": "pick", "uid": "1_7", "text": "90210", "wait": 0.02})
        check("pick on a field that lists nothing as you type says to fill or type it, and empties the field",
              failed and "fill or type it" in said and widget.fields["1_7"]["read"]["value"] == "", said)
        widget = Widget()
        said, failed = checked.run(widget, 7, {"tool": "pick", "uid": "1_5", "text": "US Citizen"})
        check("pick refuses a native select before clicking or typing into it, so its choice is left alone",
              failed and "element 1_5 is a native select, so nothing was typed" in said
              and tools(widget) == ["evaluate_script"], said)

        widget = Widget()
        held = [checked.run(widget, 7, {"tool": "expect", "uid": element, "value": value})
                for element, value in (("1_3", "true"), ("1_4", "true"), ("1_5", "Select"), ("1_2", "Joshua Jenkins"))]
        check("expect passes on what a checkbox, a pressed button, a select and a text box hold, as READ_JS reads them",
              not any(failed for _, failed in held), repr(held))
        said, failed = checked.run(widget, 7, {"tool": "expect", "uid": "1_6", "value": "Hispanic or Latino"})
        check("expect does not pass on a value that is only part of what a dropdown shows", failed, said)
        said, failed = checked.run(widget, 7, {"tool": "expect", "uid": "1_6", "value": "White (Not Hispanic or Latino)"})
        check("expect passes on the whole of what a dropdown shows", not failed, said)
        said, failed = checked.run(widget, 7, {"tool": "expect", "uid": "1_2", "value": "Someone Else"})
        check("expect fails on a value the field does not hold, naming what it holds",
              failed and 'expected "Someone Else", but the field holds "Joshua Jenkins"' in said, said)

        said, failed = checked.run(widget, 7, {"tool": "type", "uid": "1_7", "text": "902101234"})
        check("type fails when the field does not end up holding exactly the text, saying the field cut it",
              failed and 'expected "902101234", but the field holds "90210"' in said
              and "keeps only its first 5 characters" in said, said)
        said, failed = checked.run(widget, 7, {"tool": "type", "uid": "1_8", "text": "3105550100"})
        check("type into a masked field passes, naming what it shows, when only spacing and punctuation changed",
              not failed and 'the field shows them as "(310) 555-0100"' in said, said)
        for label, element, text, words in (
                ("a read-only box", "1_9", "x", "is a read-only text box, so nothing was typed"),
                ("something that is not a text box", "1_4", "x", "holds no text box and is not contenteditable"),
                ("a checkbox, saying why, so a space does not untick it", "1_3", " ", "is a checkbox input, not a text box"),
                ("a line break for a one-line box, where it would press Enter", "1_2", "a\nb", "is a one-line text box")):
            widget = Widget()
            said, failed = checked.run(widget, 7, {"tool": "type", "uid": element, "text": text})
            check("type refuses %s, typing nothing" % label, failed and words in said and "type_text" not in tools(widget),
                  said)

        widget = Widget()
        report = text_of(queue(widget, {"tool": "click", "uid": "1_12"},
                               {"tool": "wait", "uid": "1_11", "value": "Los Angeles", "timeout": 2000})["content"])
        reads = [arguments for tool, arguments in widget.calls if tool == "evaluate_script"]
        check("wait for a value waits until the field holds it, read again as the page changes it",
              "--- 2 wait ok" in report and 'the field holds "Los Angeles"' in report and len(reads) == widget.parse
              and all(read["function"] == checked.READ_JS for read in reads), report)
        widget = Widget(parse=8)
        report = text_of(queue(widget, {"tool": "click", "uid": "1_12"},
                               {"tool": "wait", "still": 20, "timeout": 2000})["content"])
        check("wait for the page to stop changing waits out the changes, then still ms more",
              "--- 2 wait ok" in report and "has not changed for 20ms" in report and not widget.parsing
              and tools(widget).count("take_snapshot") > widget.parse, report)

        widget = Widget()
        result = queue(widget, {"tool": "click", "uid": "1_13"}, {"tool": "handle_dialog", "action": "dismiss"},
                       target="T1", connect=lambda: FakeDialogs(opens=5))
        report = text_of(result["content"])
        check("a dialog that opens after its click is done is still answered by the handle_dialog step after it",
              not result["isError"] and '--- 2 handle_dialog ok' in report
              and 'the prompt "Your name?" was dismissed as it opened' in report and tools(widget) == ["click"], report)

        widget = Widget()
        reports = [text_of(queue(widget, {"tool": "fill", "uid": element, "value": "x"})["content"])
                   for element in ("1_9", "1_10")]
        check("fill refuses a read-only box, which it would empty, and a disabled one, from a read of each, filling neither",
              "element 1_9 is read-only, and fill would empty it, so nothing was filled" in reports[0]
              and "element 1_10 is disabled, so nothing was filled" in reports[1] and "fill" not in tools(widget),
              "\n".join(reports))
        result = queue(widget, {"tool": "fill_form", "elements": [{"uid": "1_2", "value": "Changed Name"},
                                                                  {"uid": "1_5", "value": "Atlantis"}]})
        report = text_of(result["content"])
        check("fill_form refuses a select given text none of its options has, before filling any element",
              result["isError"] and 'no option of the select 1_5 is exactly "Atlantis"' in report
              and "fill_form" not in tools(widget), report)
    finally:
        checked.POLL, checked.SETTLE, steps.GAP = saved
        shutil.rmtree(workdir, ignore_errors=True)


class Shots:
    """A connection to a profile's Chrome whose tab has a viewport of css CSS pixels at a device pixel ratio, scrolled
    down 300; it records what it is asked, and answers a screenshot with the bytes b"img"."""

    def __init__(self, css=(1200, 792), ratio=2, fails=False, undrawn=False):
        self.calls, self.css, self.ratio, self.fails, self.undrawn = [], css, ratio, fails, undrawn
        self.closed = False

    def call(self, method, session=None, wait=None, **params):
        self.calls.append((method, params))
        if self.fails:
            raise cdp.CdpError("Page.captureScreenshot did not answer in time")
        if self.undrawn and method == "Page.startScreencast":
            raise cdp.CdpError("Page.startScreencast: not supported")
        if method == "Target.attachToTarget":
            return {"sessionId": "S1"}
        if method == "Page.getLayoutMetrics":
            width, height = self.css
            return {"cssVisualViewport": {"pageX": 0, "pageY": 300, "clientWidth": width, "clientHeight": height},
                    "visualViewport": {"clientWidth": width * self.ratio, "clientHeight": height * self.ratio}}
        return {"data": base64.b64encode(b"img").decode()}

    def close(self):
        self.closed = True


def screenshot_offline():
    """screenshot.viewport, and a queue's viewport take_screenshot, with a stand-in for the connection to the tab."""
    workdir = tempfile.mkdtemp(prefix="browser-shot-")
    try:
        path = os.path.join(workdir, "001-step1-screenshot.jpeg")
        shots = Shots()
        content, failed_ = screenshot.viewport({"tool": "take_screenshot", "filePath": path}, "T1", lambda: shots)
        captured = [params for method, params in shots.calls if method == "Page.captureScreenshot"]
        check("a viewport screenshot is clipped to the scrolled viewport at one pixel per CSS pixel, as a JPEG, asked "
              "once, of a tab Chrome draws", not failed_ and captured == [{
                  "nudge": None, "format": "jpeg", "quality": screenshot.QUALITY, "clip": {
                      "x": 0, "y": 300, "width": 1200, "height": 792, "scale": 0.5}}], repr(shots.calls))
        methods = [method for method, _ in shots.calls]
        check("Chrome draws the tab for the capture, a screencast begun before it that ends as its connection closes, "
              "so a tab in a minimized window keeps its size",
              methods[-2:] == ["Page.startScreencast", "Page.captureScreenshot"]
              and shots.calls[-2][1] == screenshot.DRAWN and shots.closed, repr(shots.calls))
        check("it is saved to the step's filePath", open(path, "rb").read() == b"img")
        nudged = []
        for ratio in (1, 2):
            shots = Shots(ratio=ratio, undrawn=True)
            screenshot.viewport({"tool": "take_screenshot", "filePath": path}, "T1", lambda: shots)
            nudged += [params["nudge"] for method, params in shots.calls if method == "Page.captureScreenshot"]
        check("where Chrome will not draw the tab, a capture at one image pixel per device pixel is asked again while "
              "Chrome holds it, but a scaled one only once, since one asked again can leave the tab laid out at its scale",
              nudged == [screenshot.NUDGE, None], repr(nudged))
        check("and sent back as an image after a line giving its size in CSS pixels and where it is saved",
              content[1:] == [{"type": "image", "data": base64.b64encode(b"img").decode(), "mimeType": "image/jpeg"}]
              and "1200x792 px, one pixel per CSS pixel" in content[0]["text"]
              and content[0]["text"].endswith("Saved screenshot to %s." % path), repr(content))
        shots = Shots(css=(2560, 1440), ratio=1)
        content, failed_ = screenshot.viewport({"tool": "take_screenshot", "format": "png", "filePath": path}, "T1",
                                               lambda: shots)
        captured = [params for method, params in shots.calls if method == "Page.captureScreenshot"]
        check("a viewport wider than LONGEST is shrunk to it, and the line gives the factor to multiply a point by",
              not failed_ and captured[0]["clip"]["scale"] == screenshot.LONGEST / 2560
              and "2000x1125 px, each pixel 1.28 CSS pixels" in content[0]["text"]
              and "multiply a point's pixel coordinates by 1.28" in content[0]["text"], content[0]["text"])
        check("a PNG is asked for with no quality", "quality" not in captured[0] and content[1]["mimeType"] == "image/png")
        shots = Shots()
        content, failed_ = screenshot.viewport({"tool": "take_screenshot", "scale": 0.5, "filePath": path}, "T1",
                                               lambda: shots)
        captured = [params for method, params in shots.calls if method == "Page.captureScreenshot"]
        check("a scale of 0.5 takes the viewport at half an image pixel per CSS pixel, and the line gives the factor of 2",
              not failed_ and captured[0]["clip"]["scale"] == 0.25 and "600x396 px, each pixel 2 CSS pixels" in content[0]["text"]
              and "multiply a point's pixel coordinates by 2 " in content[0]["text"], content[0]["text"])
        screenshot_tool = {"take_screenshot": {"inputSchema": {"properties": {
            "pageId": {"type": "number"}, "uid": {"type": "string"}, "fullPage": {"type": "boolean"},
            "format": {"type": "string"}, "filePath": {"type": "string"}}}}}
        for step, wrong in (({"tool": "take_screenshot", "scale": 0}, "take_screenshot's scale must be a number above 0"),
                            ({"tool": "take_screenshot", "scale": 2}, "take_screenshot's scale must be a number above 0"),
                            ({"tool": "take_screenshot", "scale": 0.5, "fullPage": True}, "take_screenshot's scale is for a screenshot of the viewport"),
                            ({"tool": "take_screenshot", "scale": 0.5, "uid": "1_1"}, "take_screenshot's scale is for a screenshot of the viewport")):
            check("a queue refuses %s" % json.dumps(step),
                  wrong in refusal(lambda: steps.check([dict(step)], screenshot_tool), steps.StepError))
        check("and passes a viewport screenshot's scale of 0.5, listed in the steps argument's description",
              refusal(lambda: steps.check([{"tool": "take_screenshot", "scale": 0.5}], screenshot_tool), steps.StepError) == ""
              and "take_screenshot(uid?: string, fullPage?: boolean, format?: string, filePath?: string, scale?: number)"
              in steps.describe(screenshot_tool), steps.describe(screenshot_tool))
        content, failed_ = screenshot.viewport({"tool": "take_screenshot", "filePath": path}, "T1", lambda: Shots(fails=True))
        check("a screenshot Chrome does not answer fails its step, saying why",
              failed_ and content == [{"type": "text", "text": "could not take the screenshot: Page.captureScreenshot did "
                                                               "not answer in time"}], repr(content))
        check("browserd takes only the viewport's screenshot, leaving an element's and the whole page's to "
              "chrome-devtools-mcp", screenshot.taken({"tool": "take_screenshot"})
              and not screenshot.taken({"tool": "take_screenshot", "uid": "1_1"})
              and not screenshot.taken({"tool": "take_screenshot", "fullPage": True}))
        fake, shots = FakeDevtools([]), Shots()
        result = steps.run(fake, 7, [{"tool": "take_screenshot", "filePath": path}], lambda name: os.path.join(workdir, name),
                           target="T1", connect=lambda: shots)
        check("a queue's viewport screenshot never reaches chrome-devtools-mcp, and its image comes back",
              fake.calls == [] and not result["isError"] and result["content"][1]["type"] == "image", repr(result))
        check("a queue has Chrome draw its tab while it runs, on a connection of its own, so a click waits out no 3s",
              shots.calls[:2] == [("Target.attachToTarget", {"targetId": "T1", "flatten": True}),
                                  ("Page.startScreencast", screenshot.DRAWN)], repr(shots.calls[:2]))
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def ax(role, name="", node=1, parent=None, backend=None, **more):
    """One node of Chrome's accessibility tree, as Accessibility.getPartialAXTree gives it."""
    return dict({"nodeId": str(node), "role": {"value": role}, "name": {"value": name}}, **(
        {"parentId": str(parent)} if parent else {}), **({"backendDOMNodeId": backend} if backend else {}), **more)


# A button "Go" in a page: what a stand-in Chrome has at every point unless a check gives it another tree.
GO = [ax("button", "Go", 2, 1, backend=7), ax("RootWebArea", "page", 1), ax("StaticText", "Go", 3, 2)]


class Hand:
    """A connection to a profile's Chrome that records each mouse event it is sent; with late or a dialog, each goes
    unanswered in time (cdp.Late), and with a dialog, that dialog's event is heard; with held, a dialog open already
    leaves Page.enable unanswered. Every point holds the element whose backend node id is hit, in the tree nodes (GO
    by default); with hit None, none, as off the page; the page is scrolled by scroll, and frame is a frame's src.
    With unread, the tree is never given. dom is the words the page's own DOM gives an element (hit.DOM_WORDS's), and
    resolved the backend node ids it was asked them for."""

    def __init__(self, dialog=None, late=False, held=False, nodes=GO, hit=7, scroll=(0, 0), frame=None, unread=False,
                 dom=()):
        self.events, self.dialog, self.late, self.held = [], dialog, late or dialog is not None, held
        self.nodes, self.hit, self.scroll, self.frame, self.asked = nodes, hit, scroll, frame, []
        self.unread, self.dom, self.resolved = unread, list(dom), []

    def call(self, method, session=None, wait=None, **params):
        if method == "Target.attachToTarget":
            return {"sessionId": "S1"}
        if method == "Page.enable" and self.held:
            raise cdp.Late("Page.enable did not answer in time")
        if method == "Page.getLayoutMetrics":
            return {"cssVisualViewport": {"pageX": self.scroll[0], "pageY": self.scroll[1]}}
        if method == "DOM.getNodeForLocation":
            self.asked.append((params["x"], params["y"]))
            if self.hit is None:
                raise cdp.CdpError("DOM.getNodeForLocation: No node found at given location")
            return {"backendNodeId": self.hit, "frameId": "F1"}
        if method == "Accessibility.getPartialAXTree":
            if self.unread:
                raise cdp.CdpError("Accessibility.getPartialAXTree: the page went away")
            return {"nodes": self.nodes}
        if method == "DOM.describeNode":
            return {"node": {"nodeName": "IFRAME", "attributes": ["src", self.frame] if self.frame else []}}
        if method == "DOM.resolveNode":
            self.resolved.append(params["backendNodeId"])
            return {"object": {"objectId": "O%d" % params["backendNodeId"]}}
        if method == "Runtime.callFunctionOn":
            return {"result": {"type": "object", "value": self.dom}}
        if method == "Input.dispatchMouseEvent":
            self.events.append(params)
            if self.late:
                raise cdp.Late("Input.dispatchMouseEvent did not answer in time")
        return {}

    def wait_for(self, event, session=None, timeout=20.0):
        if self.dialog is None:
            raise cdp.CdpError("%s never arrived" % event)
        return self.dialog

    def close(self):
        pass


def pointer_offline():
    """pointer's steps: what they refuse, and the mouse events they send over a stand-in connection."""
    for step, wrong in (({"tool": "move_at", "x": 10}, "move_at needs x and y"),
                        ({"tool": "move_at", "x": -1, "y": 5}, "move_at needs x and y"),
                        ({"tool": "move_at", "x": True, "y": 5}, "move_at needs x and y"),
                        ({"tool": "move_at", "x": 1, "y": 5, "uid": "1_1"}, "move_at does not take uid"),
                        ({"tool": "click_down", "button": "side"}, "click_down's button must be left, right or middle"),
                        ({"tool": "click_down"}, "click_down needs on"),
                        ({"tool": "click_down", "on": 3}, "click_down needs on"),
                        ({"tool": "click_down", "count": 2, "on": "Go"}, "click_down's on goes on the first press"),
                        ({"tool": "click_up", "count": 4}, "click_up's count must be 1, 2 or 3")):
        check("pointer refuses %s" % json.dumps(step), (pointer.problem(step) or "").startswith(wrong), pointer.problem(step))
    check("and passes a move_at of fractional pixels, a right double click, and presses naming what they press or "
          "nothing", pointer.problem({"tool": "move_at", "x": 10.5, "y": 0}) is None
          and pointer.problem({"tool": "click_down", "button": "right", "count": 2}) is None
          and pointer.problem({"tool": "click_down", "on": "Go"}) is None and pointer.problem({"tool": "click_down", "on": ""}) is None)
    hand = Hand()
    content, failed = pointer.run({"tool": "click_down"}, "P1", lambda: hand)
    check("a press before the pointer is placed on the tab is refused, and sends nothing",
          failed and "put a move_at before it" in content[0]["text"] and hand.events == [], repr(content))
    said = []
    for step in ({"tool": "move_at", "x": 10, "y": 20}, {"tool": "click_down"}, {"tool": "move_at", "x": 50.5, "y": 60},
                 {"tool": "click_up"}):
        content, failed = pointer.run(step, "P1", lambda: hand)
        said.append((content[0]["text"], failed))
    check("a drag is move_at, click_down, move_at, click_up: the move between carries the button held",
          hand.events == [{"type": "mouseMoved", "x": 10, "y": 20, "buttons": 0, "button": "none"},
                          {"type": "mousePressed", "x": 10, "y": 20, "buttons": 1, "button": "left", "clickCount": 1},
                          {"type": "mouseMoved", "x": 50.5, "y": 60, "buttons": 1, "button": "left"},
                          {"type": "mouseReleased", "x": 50.5, "y": 60, "buttons": 0, "button": "left", "clickCount": 1}],
          repr(hand.events))
    check("and each step says where the pointer is and what it holds",
          said == [("the pointer is at 10,20", False),
                   ('pressed the left button at 10,20 on button "Go"; it stays down until a click_up', False),
                   ("the pointer is at 50.5,60, the left button down", False),
                   ("let go of the left button at 50.5,60", False)], repr(said))
    content, failed = pointer.run({"tool": "click_up"}, "P1", lambda: hand)
    check("a let-go of a button not down is refused", failed and "the left button is not down" in content[0]["text"])
    pointer.run({"tool": "click_down", "button": "right", "count": 2}, "P1", lambda: hand)
    content, failed = pointer.run({"tool": "click_down", "button": "right"}, "P1", lambda: hand)
    check("a right press with count 2 is sent as one, and a press of a button already down is refused",
          failed and "the right button is down already" in content[0]["text"] and hand.events[-1]["clickCount"] == 2
          and hand.events[-1]["buttons"] == 2, repr(content))
    content, failed = pointer.run({"tool": "move_at", "x": 1, "y": 2}, "P2", lambda: Hand({"type": "alert", "message": "hi"}))
    check("input the page could not take for a dialog it opened counts as done, and says so",
          not failed and 'the alert "hi" it opened blocks the page, so the step counts as done' in content[0]["text"],
          repr(content))
    content, failed = pointer.run({"tool": "move_at", "x": 1, "y": 2}, "P3", lambda: Hand(late=True))
    check("input a busy page has not taken in time, with no dialog open, fails, saying it lands once the page is free",
          failed and content[0]["text"] == "sent the input, but the page had not taken it after 5s, and takes it once "
                                           "free", repr(content))
    content, failed = pointer.run({"tool": "click_down"}, "P3", lambda: Hand())
    check("and the pointer is where that input put it", not failed and "at 1,2" in content[0]["text"], repr(content))
    hand = Hand(held=True)
    content, failed = pointer.run({"tool": "move_at", "x": 3, "y": 4}, "P3", lambda: hand)
    check("a page that does not answer before any input is sent, as with a dialog open, fails the step and sends nothing",
          failed and "as when a dialog is open on it: answer it with a handle_dialog step first" in content[0]["text"]
          and hand.events == [], repr(content))
    try:
        steps.check([{"tool": "move_at", "x": 1, "y": 2}, {"tool": "click_down", "on": ""}, {"tool": "click_up", "count": 2}], {})
        passed_check = True
    except steps.StepError:
        passed_check = False
    check("a queue of pointer steps passes the queue's check", passed_check)
    check("and one with a bad pointer step is refused before any step runs",
          "step 1: move_at needs x and y" in refusal(lambda: steps.check([{"tool": "move_at"}], {}), steps.StepError))
    check("the steps argument's description lists them", all("  %s(" % name in steps.describe({}) for name in pointer.STEPS))
    fake = FakeDevtools([])
    result = steps.run(fake, 7, [{"tool": "move_at", "x": 5, "y": 6}], lambda name: name, target="P4", connect=lambda: Hand())
    check("a queue's pointer step never reaches chrome-devtools-mcp", fake.calls == [] and not result["isError"],
          repr(result))


def hit_offline():
    """What a press lands on, read off a stand-in Chrome's accessibility tree, and how its report names it."""
    def landed(nodes, hit_=7, **more):
        return hit.described(hit.read(Hand(nodes=nodes, hit=hit_, **more), "S1", 10.4, 20.6))

    check("a press on an icon in a button lands on the button, by its name",
          landed([ax("RootWebArea", "page", 1), ax("button", "Bold (Ctrl+B)", 2, 1), ax("image", "", 3, 2),
                  ax("none", "", 4, 3, backend=7, ignored=True)]) == 'button "Bold (Ctrl+B)"')
    menu = [ax("RootWebArea", "page", 1), ax("menu", "", 2, 1, backend=5), ax("menuitem", "Table", 3, 2, backend=7),
            ax("StaticText", "Table", 4, 3), ax("menuitem", "Image", 5, 2), ax("StaticText", "Image", 6, 5)]
    check("a press on a menu item lands on it", landed(menu) == 'menuitem "Table"')
    gap = hit.read(Hand(nodes=menu, hit=5), "S1", 1, 2)
    check("and one in the menu between its items lands on none of them, and carries none of their words",
          hit.described(gap) == "menu, which has no words" and gap.words == [], repr(gap))
    check("a press on plain text lands on that text, by its words",
          landed([ax("RootWebArea", "page", 1), ax("paragraph", "", 2, 1, backend=7),
                  ax("StaticText", "About Seattle weather.", 3, 2)]) == 'text "About Seattle weather."')
    check("a press on a canvas names it, which has no words",
          landed([ax("RootWebArea", "page", 1), ax("Canvas", "", 2, 1, backend=7)]) == "canvas, which has no words")
    check("a press on the page itself names no control or words of the page's",
          landed([ax("RootWebArea", "page", 1), ax("generic", "", 2, 1, backend=7), ax("button", "Go", 3, 2)])
          == "a part of the page, which has no words")
    check("a disabled control says so",
          landed([ax("RootWebArea", "page", 1), ax("button", "Send", 2, 1, backend=7, properties=[
              {"name": "disabled", "value": {"type": "boolean", "value": True}}])]) == 'button "Send" (disabled)')
    long = "word " * 30
    check("a long name is cut", landed([ax("RootWebArea", "page", 1), ax("link", long, 2, 1, backend=7)])
          == 'link "%s…"' % long[:hit.SHOWN - 1].rstrip())
    check("a frame from another site, which Chrome keeps in another process, is named by where it is from",
          landed([ax("RootWebArea", "page", 1), ax("Iframe", "", 2, 1, backend=7)], frame="https://consent.example/x")
          == "a frame from consent.example, which browserd cannot read into")
    check("a point off the page lands on nothing", landed(GO, None) == "nothing browserd could name")
    tile = [ax("RootWebArea", "page", 1), ax("generic", "", 2, 1), ax("none", "", 3, 2, backend=7, ignored=True)]
    hand = Hand(nodes=tile, dom=["Segment"])
    read = hit.read(hand, "S1", 1, 2)
    check("an element the tree names nothing, like a tool tile drawn as plain divs or a dialog under aria-hidden, is "
          "named by the page's own words for it", hit.described(read) == 'text "Segment"' and hit.carries(read, "Segment")
          and hand.resolved == [7], "%r %r" % (read, hand.resolved))
    thumb = [ax("RootWebArea", "page", 1), ax("link", "", 2, 1, backend=9), ax("none", "", 3, 2, backend=7, ignored=True)]
    hand = Hand(nodes=thumb, dom=["Rectangle"])
    read = hit.read(hand, "S1", 1, 2)
    check("and so is a control the tree names nothing, by its own element's words",
          hit.described(read) == 'link "Rectangle"' and hand.resolved == [9], "%r %r" % (read, hand.resolved))
    hand = Hand(dom=["Never asked"])
    hit.read(hand, "S1", 1, 2)
    check("a control the tree names is never asked of the DOM", hand.resolved == [], repr(hand.resolved))
    check("and a canvas, with no words in the DOM either, still has none",
          landed([ax("RootWebArea", "page", 1), ax("Canvas", "", 2, 1, backend=7)]) == "canvas, which has no words")
    hand = Hand(scroll=(0, 500))
    hit.read(hand, "S1", 10.4, 20.6)
    check("a scrolled page's point is read at its place in the document, in whole pixels", hand.asked == [(10, 521)],
          repr(hand.asked))

    def carries(said, on):
        return hit.carries(hit.What("text", said, [said], False, False, None), on)

    check("on is carried by a name holding its words, whole, in order, case aside",
          carries("Bold (Ctrl+B)", "bold") and carries("Clicks so far: 12", "so far") and carries("CAFÉ", "café"))
    check("but not by one holding them only inside other words, or out of order",
          not carries("Clicks so far: 12", "1") and not carries("Clicks so far: 12", "far so")
          and not carries("Insert link", "Ins"))
    check("an on of no words, a symbol, is carried by a name holding it", carries("+ New", "+") and not carries("Close", "x"))
    check("an on's word of 4 letters or more may begin a longer word of the name, a shorter one may not",
          carries("Closer", "Close") and carries("Accept all cookies", "Accept all") and not carries("Insert", "Ins")
          and not carries("12 items", "1"))


def on_offline():
    """click_down's on: a press is sent only when what is at the point carries its words."""
    hand = Hand()
    pointer.run({"tool": "move_at", "x": 5, "y": 6}, "P5", lambda: hand)
    content, failed = pointer.run({"tool": "click_down", "on": "go"}, "P5", lambda: hand)
    check("a press whose on names what is there goes out",
          not failed and hand.events[-1]["type"] == "mousePressed" and 'on button "Go"' in content[0]["text"], repr(content))
    pointer.run({"tool": "click_up"}, "P5", lambda: hand)
    sent = len(hand.events)
    content, failed = pointer.run({"tool": "click_down", "on": "Buy"}, "P5", lambda: hand)
    check("one whose on names something else is not sent, and says what is there",
          failed and len(hand.events) == sent and content[0]["text"].startswith(
              'Not pressed: at 5,6 is button "Go", not "Buy". The page may have changed since your screenshot'),
          repr(content))
    content, failed = pointer.run({"tool": "click_down", "on": ""}, "P5", lambda: hand)
    check("and leaves the button up, so a press with on \"\" goes out there after it",
          not failed and len(hand.events) == sent + 1, repr(content))
    pointer.run({"tool": "click_up"}, "P5", lambda: hand)
    content, failed = pointer.run({"tool": "click_down", "count": 2}, "P5", lambda: hand)
    check("a second press of a double click, which takes no on, goes out unchecked", not failed, repr(content))
    pointer.run({"tool": "click_up", "count": 2}, "P5", lambda: hand)
    frame = [ax("RootWebArea", "page", 1), ax("Iframe", "", 2, 1, backend=7)]
    content, failed = pointer.run({"tool": "click_down", "on": "Accept all"}, "P5",
                                  lambda: Hand(nodes=frame, frame="https://consent.example/"))
    check("one at a frame from another site is not sent for any on but \"\", naming where the frame is from",
          failed and "is a frame from consent.example, which browserd cannot read into" in content[0]["text"],
          repr(content))
    content, failed = pointer.run({"tool": "click_down", "on": "Go"}, "P5", lambda: Hand(unread=True))
    check("nor at a point browserd could not read",
          failed and content[0]["text"].startswith('Not pressed: browserd could not read what is at 5,6'), repr(content))
    hand = Hand(unread=True)
    content, failed = pointer.run({"tool": "click_down", "on": ""}, "P5", lambda: hand)
    check("but with on \"\" it goes out there, saying it could not read what is there",
          not failed and hand.events and "(browserd could not read what is there:" in content[0]["text"], repr(content))


def limits_offline():
    """What a queue refuses or stops for: its time, a fill that would do harm, a chrome-devtools-mcp timeout too long."""
    workdir = tempfile.mkdtemp(prefix="browser-limits-")
    saved, gap = steps.QUEUE_MOST, steps.GAP
    try:
        steps.GAP = 0  # a stand-in page has nothing to react to between steps
        called = lambda name: os.path.join(workdir, "001-" + name)

        class Slow(FakeDevtools):
            def call(self, tool, arguments, wait=None):
                time.sleep(0.03)
                return super().call(tool, arguments, wait)

        steps.QUEUE_MOST = 0.02
        fake = Slow([([{"type": "text", "text": "Successfully clicked on the element"}], False)] * 3)
        result = steps.run(fake, 7, [{"tool": "click", "uid": "1_1"}, {"tool": "click", "uid": "1_2"},
                                     {"tool": "click", "uid": "1_3"}], called)
        report = text_of(result["content"])
        check("a queue past QUEUE_MOST starts no more steps, names them, and is an error",
              result["isError"] and "--- stopped before step 2" in report and "--- not run: 2 click, 3 click" in report
              and [tool for tool, _ in fake.calls] == ["click", "take_snapshot"], report)
        steps.QUEUE_MOST = saved

        for label, found, value, words in (
                ("a disabled box", {"kind": "box", "disabled": True}, "x", "is disabled"),
                ("a read-only box", {"kind": "box", "readonly": True}, "x", "is read-only, and fill would empty it"),
                ("a checkbox given words", {"kind": "toggle"}, "yes", 'with "true" or "false"'),
                ("a select given text none of its options has", {"kind": "select", "options": ["Canada"]}, "Atlantis",
                 'no option of the select 1_4 is exactly "Atlantis"'),
                ("a part of a date field", {"kind": "datepart", "type": "date"}, "08",
                 "one part of the date field on the Date line above it (fill that line"),
                ("a part of a disabled date field, whose line the snapshot lacks",
                 {"kind": "datepart", "type": "date", "disabled": True}, "08", "is disabled"),
                ("a date Chrome would leave empty", {"kind": "date", "type": "date", "takes": False}, "08/01/1957",
                 'leaves empty given "08/01/1957"; it takes only a real one as YYYY-MM-DD, like 1957-08-01'),
                ("a time Chrome would leave empty", {"kind": "date", "type": "time", "takes": False}, "2:30 PM",
                 "HH:MM on a 24-hour clock"),
                ("a disabled date field", {"kind": "date", "type": "date", "disabled": True, "takes": True},
                 "1957-08-01", "is disabled")):
            said = checked._unfillable(found, "1_4", value)
            check("fill refuses %s" % label, said is not None and words in said, repr(said))
        check("fill takes a select's option exactly as labelled, a checkbox's true, and a plain box",
              checked._unfillable({"kind": "select", "options": ["United States"]}, "1_4", "United States") is None
              and checked._unfillable({"kind": "toggle"}, "1_4", "true") is None
              and checked._unfillable({"kind": "box"}, "1_4", "x") is None)
        check("fill takes a date or time field given a value Chrome takes",
              all(checked._unfillable({"kind": "date", "type": kind, "takes": True}, "1_4", "x") is None
                  for kind in checked.DATE_VALUES))
        check("fill refuses a select's option with spacing its label lacks, which chrome-devtools-mcp would not match",
              checked._unfillable({"kind": "select", "options": ["United States"]}, "1_4", " United  States ") is not None)
        said = refusal(lambda: steps.check([{"tool": "wait_for", "text": "x", "timeout": 60000}], SCHEMAS), steps.StepError)
        check("a chrome-devtools-mcp timeout longer than a queue can report within is refused",
              "timeout must be milliseconds, up to %d" % checked.WAIT_MOST in said, said)
        fake = FakeDevtools([([{"type": "text", "text": "Found"}], False)])
        steps.run(fake, 7, [{"tool": "wait_for", "text": ["x"], "timeout": 40000}], called)
        check("a chrome-devtools-mcp timeout is cut to the time the queue has left",
              fake.calls[0][1]["timeout"] <= steps.QUEUE_MOST * 1000, repr(fake.calls[0]))
        fake = FakeDevtools([([{"type": "text", "text": "Successfully navigated"}], False)] * 2)
        steps.run(fake, 7, [{"tool": "navigate_page", "url": "https://example.com/"},
                            {"tool": "navigate_page", "type": "reload", "timeout": 5000}], called)
        check("a navigate_page that names no timeout is given NAVIGATE_TIMEOUT, and one that names its own keeps it",
              [arguments.get("timeout") for tool, arguments in fake.calls if tool == "navigate_page"]
              == [steps.NAVIGATE_TIMEOUT, 5000], repr(fake.calls))
        long_json = "Script ran on page and returned:\n```json\n" + "x" * (steps.REPLY_MOST + 10) + "\n```"
        report = text_of(steps.run(FakeDevtools([([{"type": "text", "text": long_json}], False)]), 3,
                                   [{"tool": "evaluate_script"}], lambda name: os.path.join(workdir, "007-" + name))["content"])
        check("a long one-line script result is cut mid-line, not dropped", report.count("x") >= steps.REPLY_MOST - 100,
              repr(len(report)))
        saved_answerer = dialogs.Answerer
        dialogs.Answerer = lambda target, handle, connect: saved_answerer(target, handle, lambda: FakeDialogs(opens=1))
        try:
            fake = FakeDevtools([([{"type": "text", "text": "Error: Element uid 1_1 not found"}], True)])
            report = text_of(steps.run(fake, 7, [{"tool": "click", "uid": "1_1"}, {"tool": "handle_dialog", "action": "accept"},
                                                 {"tool": "handle_dialog", "action": "dismiss"}], called, target="T1")["content"])
        finally:
            dialogs.Answerer = saved_answerer
        check("a dialog answered for a step that then failed is still reported",
              "was accepted as it opened, though its handle_dialog step did not run" in report, report)
    finally:
        steps.QUEUE_MOST, steps.GAP = saved, gap
        shutil.rmtree(workdir, ignore_errors=True)
