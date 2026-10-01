"""The queue and its steps, offline, against a stand-in chrome-devtools-mcp: checked steps, dialogs, paste,
screenshots, the pointer and the click guard, and what a queue refuses.
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
from browser.steps import checked, dialogs, pointer, screenshot, steps
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


def waits():
    """checked.wait's snapshot conditions over pages whose text goes, stays, or always changes."""
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
    began = time.monotonic()
    said, failed = checked.run(Page(lambda n: root), 1, {"tool": "wait", "still": 500, "timeout": 3000})
    check("a wait for a page that does not change passes once still ms have gone by",
          not failed and "not changed for 500ms" in said and time.monotonic() - began >= 0.5, said)
    said, failed = checked.run(Page(lambda n: 'uid=1_0 RootWebArea "Tick %d"' % n), 1, {"tool": "wait", "still": 500, "timeout": 1500})
    check("a wait for a page that keeps changing fails at its timeout", failed and "did not stay unchanged" in said, said)
    began = time.monotonic()
    said, failed = checked.run(Page(lambda n: 'uid=1_0 RootWebArea "Tick %d"' % n), 1,
                               {"tool": "wait", "still": 500, "timeout": 20000}, left=1.0)
    check("a wait longer than the queue has left is cut to it, and says so when it fails",
          failed and "its timeout was cut to the 1.0s the queue had left" in said and time.monotonic() - began < 5, said)


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
    check("no dialog within the late wait leaves nothing answered", answerer.answered(0.2) is None
          and all(method != "Page.handleJavaScriptDialog" for method, _ in connection.calls), repr(connection.calls))

    def unreachable():
        raise cdp.CdpError("nothing is listening")

    answerer = dialogs.Answerer("T1", {"tool": "handle_dialog", "action": "accept"}, unreachable)
    answerer.start_listening()
    check("an answerer that cannot reach the tab answers nothing", answerer.answered(0.2) is None)

    saved = dialogs.Answerer
    workdir = tempfile.mkdtemp(prefix="browser-dialogs-")
    try:
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
        saved_late, dialogs.LATE = dialogs.LATE, 0.2
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
        dialogs.Answerer = saved
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


class Shots:
    """A connection to a profile's Chrome whose tab has a viewport of css CSS pixels at a device pixel ratio, scrolled
    down 300; it records what it is asked, and answers a screenshot with the bytes b"img"."""

    def __init__(self, css=(1200, 792), ratio=2, fails=False):
        self.calls, self.css, self.ratio, self.fails = [], css, ratio, fails

    def call(self, method, session=None, wait=None, **params):
        self.calls.append((method, params))
        if self.fails:
            raise cdp.CdpError("Page.captureScreenshot did not answer in time")
        if method == "Target.attachToTarget":
            return {"sessionId": "S1"}
        if method == "Page.getLayoutMetrics":
            width, height = self.css
            return {"cssVisualViewport": {"pageX": 0, "pageY": 300, "clientWidth": width, "clientHeight": height},
                    "visualViewport": {"clientWidth": width * self.ratio, "clientHeight": height * self.ratio}}
        return {"data": base64.b64encode(b"img").decode()}

    def close(self):
        pass


def screenshot_offline():
    """screenshot.viewport, and a queue's viewport take_screenshot, with a stand-in for the connection to the tab."""
    workdir = tempfile.mkdtemp(prefix="browser-shot-")
    try:
        path = os.path.join(workdir, "001-step1-screenshot.jpeg")
        shots = Shots()
        content, failed_ = screenshot.viewport({"tool": "take_screenshot", "filePath": path}, "T1", lambda: shots)
        captured = [params for method, params in shots.calls if method == "Page.captureScreenshot"]
        check("a viewport screenshot is clipped to the scrolled viewport at one pixel per CSS pixel, as a JPEG, asked "
              "again while Chrome holds it", not failed_ and captured == [{
                  "nudge": screenshot.NUDGE, "format": "jpeg", "quality": screenshot.QUALITY, "clip": {
                      "x": 0, "y": 300, "width": 1200, "height": 792, "scale": 0.5}}], repr(shots.calls))
        check("it is saved to the step's filePath", open(path, "rb").read() == b"img")
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
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


class Hand:
    """A connection to a profile's Chrome that records each mouse event it is sent; with late or a dialog, each goes
    unanswered in time (cdp.Late), and with a dialog, that dialog's event is heard; with held, a dialog open already
    leaves Page.enable unanswered."""

    def __init__(self, dialog=None, late=False, held=False):
        self.events, self.dialog, self.late, self.held = [], dialog, late or dialog is not None, held

    def call(self, method, session=None, wait=None, **params):
        if method == "Target.attachToTarget":
            return {"sessionId": "S1"}
        if method == "Page.enable" and self.held:
            raise cdp.Late("Page.enable did not answer in time")
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
                        ({"tool": "click_up", "count": 4}, "click_up's count must be 1, 2 or 3")):
        check("pointer refuses %s" % json.dumps(step), (pointer.problem(step) or "").startswith(wrong), pointer.problem(step))
    check("and passes a move_at of fractional pixels and a right double click",
          pointer.problem({"tool": "move_at", "x": 10.5, "y": 0}) is None
          and pointer.problem({"tool": "click_down", "button": "right", "count": 2}) is None)
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
                   ("pressed the left button at 10,20; it stays down until a click_up", False),
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
        steps.check([{"tool": "move_at", "x": 1, "y": 2}, {"tool": "click_down"}, {"tool": "click_up", "count": 2}], {})
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


def limits_offline():
    """What a queue refuses or stops for: its time, a fill that would do harm, a chrome-devtools-mcp timeout too long."""
    workdir = tempfile.mkdtemp(prefix="browser-limits-")
    saved = steps.QUEUE_MOST
    try:
        called = lambda name: os.path.join(workdir, "001-" + name)

        class Slow(FakeDevtools):
            def call(self, tool, arguments, wait=None):
                time.sleep(0.3)
                return super().call(tool, arguments, wait)

        steps.QUEUE_MOST = 0.2
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
        steps.QUEUE_MOST = saved
        shutil.rmtree(workdir, ignore_errors=True)
