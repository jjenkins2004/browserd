#!/usr/bin/env python3
"""The page-now experiment's numbers and decision (../../findings/page-now.md): Part 1's runs by arm, the oracle's
agreement with the runs' real next actions, Part 2's forks by kind of stop and reply, the cost model's difference per
stop against keep with intervals from resampling runs within each scenario, and the decision the plan fixed.

    python3 experiments/bench/tools/pnreport.py <exp> [--json out.json]
"""
import argparse
import json
import random
import re
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pagenow  # noqa: E402  # pyright: ignore[reportMissingImports]
import paths  # noqa: E402  # pyright: ignore[reportMissingImports]
import pnfork  # noqa: E402  # pyright: ignore[reportMissingImports]
import pnhook  # noqa: E402  # pyright: ignore[reportMissingImports]
import run  # noqa: E402  # pyright: ignore[reportMissingImports]

MODEL = "claude-sonnet-5-5"
DECISION_C, DECISION_T = 189_000, 60  # the trip deck's mean context, and turns left
MARGIN, SHARE = 0.01, 0.10  # a winner saves at least this many dollars a stop, and this share of keep's modelled cost
ELIGIBLE = 0.15  # the most a reply's share of failing first actions may exceed keep's, at the interval's top
MIN_RUNS = 8  # runs a kind needs before it is decided
ORDER = ("note", "short", "shot")  # among replies within the margin of the cheapest, the first of these wins
DRAWS = 4000
UID = re.compile(r"\buid=(\S+)")
ESCALATED = {str(number) for number in pagenow.ESCALATE.values()}
CHANGES = re.compile(r'combobox "(Status|Priority|Assignee) for #(\d{4})"')
REBUILDS = {  # what a change of each field rebuilds at checkout, by the names its view gives the fields
    "country": ("First name", "Last name", "Company", "Address", "Apartment", "City", "State", "Province", "ZIP code",
                "Postal code", "Postcode", "Phone", "Save this information"),
    "postal": ("Standard", "Express", "Overnight"),
    "shipping": ("Card number", "Expiration date", "Security code", "Name on card", "Use shipping address"),
}
NEUTRAL = {"take_snapshot", "take_screenshot", "click_up", "move_at"}
UID_STEPS = {"click", "fill", "hover", "drag", "type", "expect", "pick", "upload_file", "fill_form"}


def _saved_snapshot(report):
    saved = re.search(r"saved whole to (.+?-page-now-snapshot\.txt)", report)
    return Path(saved.group(1)).read_text(encoding="utf-8") if saved and Path(saved.group(1)).exists() else ""


def _line(snapshot, uid):
    return next((line for line in snapshot.splitlines() if re.search(r"\buid=%s\b" % re.escape(uid), line)), "")


def _layout_at(app_events, t):
    """The pixel apps' layout as logged last before epoch seconds t."""
    last = None
    for event in app_events:
        if event.get("kind") == "layout" and event.get("at", 0) <= t:
            last = event
    return last


def _under(layout, x, y):
    """The layout's items at a point, bottom to top; a frame's own buttons come from the frame's message."""
    items = [item for item in layout["items"] if item[1] <= x < item[1] + item[3] and item[2] <= y < item[2] + item[4]]
    frame = next((item for item in items if item[5] == "frame"), None)
    if frame and layout.get("buttons"):
        items += [[b[0], frame[1] + b[1], frame[2] + b[2], b[3], b[4], "frame-control"] for b in layout["buttons"]
                  if frame[1] + b[1] <= x < frame[1] + b[1] + b[3] and frame[2] + b[2] <= y < frame[2] + b[2] + b[4]]
    return items


def _uids(step):
    if step.get("tool") == "fill_form":
        return [str(element.get("uid", "")) for element in step.get("elements") or [] if isinstance(element, dict)]
    uid = step.get("uid") or step.get("from_uid")
    return [str(uid)] if uid else []


def _bare(uid):
    return uid.replace("uid=", "").split("..")[0]


def _start_pointer(stop):
    """Where the pointer was as the agent's next call began: the stopped call's last move_at before its failed step,
    or, when it had none, the run's last before it."""
    steps = (stop.get("call") or {}).get("steps") or []
    failed = re.search(r"(at|before) step (\d+)", stop["report"])
    # A stop before step N ran steps 1 to N-1; one at step N ran step N too.
    upto = int(failed.group(2)) - (failed.group(1) == "before") if failed else len(steps)
    moves = [s for s in steps[:upto] if isinstance(s, dict) and s.get("tool") == "move_at"]
    return [moves[-1].get("x"), moves[-1].get("y")] if moves else stop.get("pointer")


def _final(scenario, answer):
    """A final reply's class: on export, whether it states the net revenue, as pagenow scores it; else answer."""
    if scenario != "export":
        return "answer"
    return "ok" if pagenow.SCORERS["export"]([], answer)["passed"] else "fails"


def oracle(scenario, call, answer, stop, app_events, t):
    """Would the agent's first action after a stop work? ok; fails (it would stop again, or answer without what the
    task asked for); misclick (it lands on something it did not aim at, with no stop); answer (a final reply);
    look-cap (a look the fork could not serve: past the hook's LOOKS, or a screenshot at a stop that saved none);
    unjudged (an action the rules here do not cover)."""
    if call is None:
        return _final(scenario, answer)
    if not call.get("name", "").endswith(("__queue", "__tab_open")):
        return "unjudged"
    steps = [s for s in (call.get("input") or {}).get("steps") or [] if isinstance(s, dict)]
    if not steps:
        return "unjudged"
    if pnhook.look_only(call.get("input") or {}, shots=True):
        return "look-cap"
    snapshot = _saved_snapshot(stop["report"])
    held = set(UID.findall(snapshot))
    layout = _layout_at(app_events, t)
    overlay = [item for item in layout["items"] if item[5] in ("overlay", "frame")] if layout else []
    pointer, changed, verdict, unjudged = _start_pointer(stop), [], None, False
    dismissed = False  # a press on one of the deck popover's buttons closes it for the queue's later steps
    for step in steps:
        tool = step.get("tool")
        for uid in map(_bare, _uids(step)):
            if snapshot and uid not in held:
                return "fails"  # a uid the page no longer has
            name = _line(snapshot, uid)
            change = CHANGES.search(name)
            if scenario == "helpdesk" and tool in ("fill", "fill_form") and change:
                if changed:
                    return "fails"  # the first change's save re-renders every row
                changed.append(name)
                if change.group(2) not in ESCALATED:
                    verdict = verdict or "misclick"
            if scenario == "checkout":
                if any(trigger in changed and any(field in name for field in block) for trigger, block in REBUILDS.items()):
                    return "fails"
                value = str(step.get("value", ""))
                if "Country/region" in name and '= "%s"' % value not in name:
                    changed.append("country")
                elif ("Postal code" in name or "ZIP code" in name) and 'value="%s"' % value not in name:
                    changed.append("postal")
                elif re.search(r'radio "(Standard|Express|Overnight)', name) and " checked" not in name:
                    changed.append("shipping")
            if scenario == "deck" and tool == "click" and overlay and not dismissed:
                label = re.search(r'"(.*?)"', name)
                target = next((item for item in layout["items"] if label and item[0] == label.group(1)), None) if layout else None
                if target and target[5] == "overlay-control":
                    dismissed = True
                elif target:
                    cx, cy = target[1] + target[3] / 2, target[2] + target[4] / 2
                    if any(o[1] <= cx < o[1] + o[3] and o[2] <= cy < o[2] + o[4] for o in overlay):
                        verdict = verdict or "misclick"  # a uid click goes to the element's centre, under the popover
        if tool == "move_at":
            pointer = [step.get("x"), step.get("y")]
        elif tool == "click_down" and step.get("count", 1) == 1:
            if scenario not in ("deck", "consent") or not layout or not pointer:
                unjudged = True
                continue
            on = (step.get("on") or "").lower()
            items = [item for item in _under(layout, *pointer)
                     if not (dismissed and item[5] in ("overlay", "overlay-control"))]
            top = items[-1] if items else None
            if top is None:
                continue
            if top[5] == "frame":
                if on:
                    return "fails"  # browserd cannot read into a frame from another site
                verdict = verdict or "misclick"
            elif top[5] == "frame-control":
                if on:
                    return "fails"
            elif top[5] in ("overlay", "overlay-control"):
                if on and on not in top[0].lower():
                    return "fails"  # refused: what is at the point is the popover
                if not on and top[5] == "overlay":
                    verdict = verdict or "misclick"
                if top[5] == "overlay-control":
                    dismissed = True
        elif tool in ("wait", "wait_for"):
            continue  # waiting again is how a wait that ran out goes on
        elif tool not in NEUTRAL and tool not in UID_STEPS:
            unjudged = True
    return verdict or ("unjudged" if unjudged else "ok")


def _next_action(transcript, stop):
    """The run's real next action after a stop that was not a look: (its call, its result's text, when it was made,
    when its result came), or (None, the final answer, None, None)."""
    calls, after, made = {}, False, {}
    for at, event in enumerate(transcript):
        if at == stop["at"]:
            after = True
            continue
        content = (event.get("message") or {}).get("content")
        if not after or not isinstance(content, list):
            continue
        for block in content:
            if event.get("type") == "assistant" and block.get("type") == "tool_use":
                calls[block["id"]], made[block["id"]] = block, event.get("_t")
            elif event.get("type") == "user" and block.get("type") == "tool_result" and block["tool_use_id"] in calls:
                use = calls[block["tool_use_id"]]
                if use.get("name", "").endswith("__queue") and pnhook.look_only(use.get("input") or {}):
                    continue
                return use, pnfork._text(block.get("content")), made[block["tool_use_id"]], event.get("_t")
    result = next((e for e in transcript if e.get("type") == "result"), {})
    return None, result.get("result"), None, None


def real_class(scenario, call, text, began, ended, app_events, predicted):
    """How the real next action went, from its result and the app's log over its time."""
    if call is None:
        return _final(scenario, text)
    steps = [s for s in (call.get("input") or {}).get("steps") or [] if isinstance(s, dict)]
    if scenario == "export" and all(s.get("tool") in ("wait", "wait_for") or s.get("tool") in NEUTRAL for s in steps):
        return "ok"  # waiting again is how a wait that ran out goes on, whether or not it runs out again
    if pnfork.STOP.search(text or ""):
        return "fails"
    window = [e for e in app_events if began and ended and began - 1 <= e.get("at", 0) <= ended + 1]
    if scenario == "helpdesk" and any(e.get("kind") == "change" and str(e.get("id")) not in ESCALATED for e in window):
        return "misclick"
    if scenario == "deck" and predicted == "misclick" and not any(
            e.get("kind") in ("tool", "style") or (e.get("kind") == "popover" and e.get("phase") == "closed") for e in window):
        return "misclick"
    if scenario == "consent" and predicted == "misclick" and not any(e.get("kind") == "consent" for e in window):
        return "misclick"
    return "ok"


def part1(exp):
    rows, tasks = [], pagenow.load()
    for path in sorted((paths.RESULTS / exp).glob("*/*.jsonl")):
        name, rep = path.stem.rsplit("-r", 1)
        transcript = pnfork.events(path)
        result = next((e for e in transcript if e.get("type") == "result"), {})
        made = pnfork.messages(transcript)
        token = run.token(exp, path.parent.name, name, int(rep))
        app_events = pagenow.events(token)
        found = pnfork.stops(transcript)
        for stop in found:
            stop["t"] = transcript[stop["at"]].get("_t")
            stop["kind"] = pnfork._refined(stop, app_events, stop["t"] or 0)
        row = {"arm": path.parent.name, "task": name, "rep": int(rep), "cost": result.get("total_cost_usd"),
               "sonnet": ((result.get("modelUsage") or {}).get(MODEL) or {}).get("costUSD"),
               "priced": sum(m["cost"] for m in made), "seconds": (result.get("duration_ms") or 0) / 1000,
               "turns": len(made), "ended": result.get("subtype"), "stops": len(found), "kinds": [s["kind"] for s in found]}
        row.update(pagenow.score(tasks[name], result.get("result"), token))
        if found:
            first = dict(found[0], pointer=pnfork._pointer(transcript, found[0]),
                         call=next((m["uses"][0]["input"] for m in made if m["id"] == found[0]["message"] and m["uses"]), None))
            before = [m for m in made if m["t"] and first["t"] and m["t"] < first["t"]]
            call, text, began, ended = _next_action(transcript, first)
            predicted = oracle(name, call, text if call is None else None, first, app_events, began or first["t"] or 0)
            row.update(first_kind=first["kind"], before_cost=sum(m["cost"] for m in before), before_turns=len(before),
                       stop_chars=len(found[0]["report"]), oracle_next=predicted,
                       real_next=real_class(name, call, text, began, ended, app_events, predicted))
        rows.append(row)
    return rows


def part2(exp):
    forks = []
    for summary in sorted((pnfork.FORKS / exp).glob("*/*/stop*/*/fork.json")):
        fork = json.loads(summary.read_text(encoding="utf-8"))
        folder = summary.parent
        stop = json.loads((folder.parent / "stop.json").read_text(encoding="utf-8"))
        transcript = pnfork.events(folder / "transcript.jsonl")
        made = pnfork.messages(transcript)
        result = next((e for e in transcript if e.get("type") == "result"), {})
        hooked = [json.loads(line) for line in (folder / "hook.jsonl").read_text(encoding="utf-8").splitlines()] \
            if (folder / "hook.jsonl").exists() else []
        looks = [h for h in hooked if h["allowed"] and h["id"] != stop["id"]]
        deferred = next((h for h in hooked if not h["allowed"]), None)
        call = {"name": deferred["tool"], "input": deferred["input"]} if deferred else None
        scenario, rep = fork["run"].rsplit("-r", 1)
        app_events = pagenow.events(run.token(exp, fork["arm"], scenario, int(rep)))
        # The page as it was when the agent would have acted: the stop, the source's own wait for its next turn, and
        # the time the fork spent looking.
        t = (stop["t"] or 0) + (stop["latency"] or 0) + ((made[-1]["t"] - made[0]["t"]) if len(made) > 1 else 0)
        cls = oracle(scenario, call, result.get("result") if call is None else None, stop, app_events, t)
        if cls == "look-cap":
            looks = looks + [deferred]
        # A cache miss alone is not void: a fork run more than an hour after its run writes the same prefix again.
        # Its content is checked apart: every sample of one reply at one stop must send the same first request.
        void = (fork["leaked"] or fork.get("stub_failed") or fork["timed_out"] or not made
                or fork["stop_reason"] not in ("tool_deferred", "end_turn"))
        reply_tokens = (made[0]["total_in"] - stop["prefix"] - stop["call_output"]) if made and stop.get("prefix") else None
        forks.append(dict(fork, scenario=scenario, kind=stop["kind"], looks=len(looks),
                          first_in=made[0]["total_in"] if made else None, last_in=made[-1]["total_in"] if made else None,
                          output=sum(m["usage"]["output_tokens"] for m in made), turns=len(made), reply_tokens=reply_tokens,
                          turn_seconds=[b["t"] - a["t"] for a, b in zip(made, made[1:])], first_action=cls,
                          void=bool(void), cost=sum(m["cost"] for m in made)))
    same_reply = {}
    for fork in forks:
        if fork["first_in"] is not None:
            same_reply.setdefault((fork["arm"], fork["run"], fork["stop"], fork["reply"]), []).append(fork)
    differing = [group for group in same_reply.values()
                 # 50: a reply with an image counts up to about 10 tokens apart from one sample to the next
                 if max(f["first_in"] for f in group) - min(f["first_in"] for f in group) > 50]
    for group in differing:
        for fork in group:
            fork["void"] = True  # one reply's samples sent different first requests: the fork did not replay the run
    return forks, len(differing)


def _stops(forks):
    grouped = {}
    for fork in forks:
        grouped.setdefault((fork["scenario"], fork["arm"], fork["run"], fork["stop"]), []).append(fork)
    return grouped


def _failing(forks):
    judged = [f for f in forks if f["first_action"] in ("ok", "fails", "misclick", "answer")]
    return sum(f["first_action"] in ("fails", "misclick") for f in judged) / len(judged) if judged else None


def model(stops, reply, c=DECISION_C, t=DECISION_T):
    """The cost model over a set of stops (each {reply: forks}): (mean Δ against keep, keep's modelled cost, the
    mean of each stop's difference in failing share), all per stop, or None when no stop has both replies."""
    pairs = [(group[reply], group["keep"]) for group in stops if group.get(reply) and group.get("keep")]
    if not pairs:
        return None
    mean = statistics.mean
    out = pnfork.OUTPUT * mean(f["output"] / max(1, f["turns"]) for mine, keep in pairs for f in mine + keep)
    carry = pnfork.WRITE + t * pnfork.READ  # a token added now: written once, then read on every later turn
    look = c * pnfork.READ + out  # a turn more: the whole context read again, and its own output
    per_stop, keep_cost = [], []
    for mine, keep in pairs:
        added = (mean(f["first_in"] for f in mine) - mean(f["first_in"] for f in keep)
                 + mean(f["last_in"] - f["first_in"] for f in mine) - mean(f["last_in"] - f["first_in"] for f in keep))
        per_stop.append(added * carry + (mean(f["looks"] for f in mine) - mean(f["looks"] for f in keep)) * look)
        keep_cost.append(mean((f["reply_tokens"] or 0) + f["last_in"] - f["first_in"] for f in keep) * carry
                         + mean(f["looks"] for f in keep) * look)
    shares = [(_failing(mine), _failing(keep)) for mine, keep in pairs]
    shares = [(a, b) for a, b in shares if a is not None and b is not None]
    p_mine = mean(a for a, _ in shares) if shares else 0
    p_keep = mean(b for _, b in shares) if shares else 0
    # A failed action costs a turn more, and the stop reply it gets back: under this reply, as big as this one.
    fail_mine = look + mean(f["reply_tokens"] or 0 for mine, _ in pairs for f in mine) * carry
    fail_keep = look + mean(f["reply_tokens"] or 0 for _, keep in pairs for f in keep) * carry
    delta = mean(per_stop) + p_mine * fail_mine - p_keep * fail_keep
    return delta, mean(keep_cost) + p_keep * fail_keep, mean(a - b for a, b in shares) if shares else 0.0


def _resampled(stops_by_run, rng):
    """One bootstrap draw: each scenario's runs drawn again with replacement, their stops with them."""
    drawn = []
    for runs in stops_by_run.values():
        keys = list(runs)
        for key in rng.choices(keys, k=len(keys)):
            drawn += runs[key]
    return drawn


def decide(kind_forks):
    """The plan's decision for one kind of stop: per reply its Δ, interval and failing difference; the winner."""
    good = [f for f in kind_forks if not f["void"]]
    stops_by_run = {}
    for (scenario, arm, name, number), forks in _stops(good).items():
        group = {}
        for fork in forks:
            group.setdefault(fork["reply"], []).append(fork)
        stops_by_run.setdefault(scenario, {}).setdefault((arm, name), []).append(group)
    all_stops = [g for runs in stops_by_run.values() for groups in runs.values() for g in groups]
    runs = sum(len(r) for r in stops_by_run.values())
    rng = random.Random(1)
    draws = [_resampled(stops_by_run, rng) for _ in range(DRAWS)]
    found = {}
    for reply in pnfork.REPLIES[1:]:
        point = model(all_stops, reply)
        own_c = model(all_stops, reply, c=statistics.mean(f["first_in"] for f in good))
        t15, t120 = model(all_stops, reply, t=15), model(all_stops, reply, t=120)
        if point is None or own_c is None or t15 is None or t120 is None:
            continue
        sampled = [s for s in (model(d, reply) for d in draws) if s is not None]
        deltas = sorted(s[0] for s in sampled)
        fails = sorted(s[2] for s in sampled)
        found[reply] = {"delta": point[0], "keep_cost": point[1], "fail_diff": point[2],
                        "low": deltas[int(0.05 * len(deltas))], "high": deltas[int(0.95 * len(deltas)) - 1],
                        "fail_high": fails[int(0.95 * len(fails)) - 1],
                        "own_c": own_c[0], "t15": t15[0], "t120": t120[0]}
    if runs < MIN_RUNS:
        return found, runs, "no decision: %d runs, fewer than %d" % (runs, MIN_RUNS)
    keep_cost = next(iter(found.values()))["keep_cost"] if found else 0
    needed = max(MARGIN, SHARE * keep_cost)
    candidates = {r: v for r, v in found.items()
                  if v["fail_high"] < ELIGIBLE and v["delta"] <= -needed and v["high"] < 0}
    if not candidates:
        return found, runs, ("keep (no reply saves $%.3f a stop with its interval below zero and its failing share "
                             "in bounds)" % needed)
    best = min(candidates.values(), key=lambda v: v["delta"])["delta"]
    near = [r for r in ORDER if r in candidates and candidates[r]["delta"] - best < needed]
    return found, runs, "%s (saves $%.4f a stop against keep; replies within $%.3f of the cheapest: %s)" % (
        near[0], -candidates[near[0]]["delta"], needed, ", ".join(near))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("exp")
    parser.add_argument("--json")
    args = parser.parse_args()
    rows, (forks, differing) = part1(args.exp), part2(args.exp)
    cold = sum(1 for f in forks if f["source_cache_read"] and (f["first_cache_read"] or 0) < 0.9 * f["source_cache_read"])
    print("first requests: %d forks missed the cache (run more than an hour after their run); %d reply groups sent "
          "differing first requests and are void" % (cold, differing))
    if args.json:
        Path(args.json).write_text(json.dumps({"runs": rows, "forks": forks}, indent=1), encoding="utf-8")
    arms = json.loads((paths.DATA / "arms.json").read_text(encoding="utf-8"))
    print("Part 1: runs")
    print("%-5s %-6s %-9s %3s %6s %5s %-30s %7s %7s %7s %5s %s" % ("arm", "reply", "scenario", "rep", "passed", "stops",
          "kinds", "cost", "priced", "seconds", "turns", "next: real/oracle"))
    for r in rows:
        print("%-5s %-6s %-9s %3d %6s %5d %-30s %7.3f %7.3f %7.0f %5d %s/%s" % (
            r["arm"], arms[r["arm"][-1]], r["task"], r["rep"], r["passed"], r["stops"], ",".join(r["kinds"])[:30],
            r["cost"] or 0, r["priced"], r["seconds"], r["turns"], r.get("real_next"), r.get("oracle_next")))
    print("\nby arm (reply): passed, mean cost, mean turns, mean seconds; and up to the first stop")
    for arm in sorted({r["arm"] for r in rows}):
        mine = [r for r in rows if r["arm"] == arm]
        stopped = [r for r in mine if "before_cost" in r]
        print("  %-5s %-6s %2d/%-2d  $%.3f  %4.1f turns  %4.0fs   before the stop: $%.3f  %4.1f turns  (%d stopped)" % (
            arm, arms[arm[-1]], sum(r["passed"] for r in mine), len(mine), statistics.mean(r["cost"] or 0 for r in mine),
            statistics.mean(r["turns"] for r in mine), statistics.mean(r["seconds"] for r in mine),
            statistics.mean(r["before_cost"] for r in stopped) if stopped else 0,
            statistics.mean(r["before_turns"] for r in stopped) if stopped else 0, len(stopped)))
    print("\nthe oracle against the real next action, over its classes (unjudged and looks left out)")
    for scenario in sorted({r["task"] for r in rows}):
        judged = [r for r in rows if r["task"] == scenario and r.get("oracle_next") not in (None, "unjudged", "look-cap")]
        print("  %-9s %d of %d agree  (%d unjudged)" % (scenario, sum(r["oracle_next"] == r["real_next"] for r in judged),
              len(judged), sum(r.get("oracle_next") in ("unjudged", "look-cap") for r in rows if r["task"] == scenario)))
    failed = pnfork.FORKS / args.exp / "forks-failed.jsonl"
    void = [f for f in forks if f["void"]]
    print("\nPart 2: %d forks, %d void, %d failures logged" % (
        len(forks), len(void), len(failed.read_text(encoding="utf-8").splitlines()) if failed.exists() else 0))
    for kind in sorted({f["kind"] for f in forks if not f["void"]}):
        mine = [f for f in forks if f["kind"] == kind and not f["void"]]
        print("\n  kind %s" % kind)
        for reply in pnfork.REPLIES:
            these = [f for f in mine if f["reply"] == reply]
            if not these:
                continue
            seconds = [s for f in these for s in f["turn_seconds"]]
            print("    %-5s forks %3d  looks %.2f  reply %6.0f tok  looks added %6.0f tok  turn %4.1fs  first action %s" % (
                reply, len(these), statistics.mean(f["looks"] for f in these),
                statistics.mean(f["reply_tokens"] or 0 for f in these),
                statistics.mean(f["last_in"] - f["first_in"] for f in these),
                statistics.median(seconds) if seconds else 0,
                {c: sum(f["first_action"] == c for f in these)
                 for c in ("ok", "fails", "misclick", "answer", "look-cap", "unjudged") if any(f["first_action"] == c for f in these)}))
        found, runs, decision = decide(mine)
        for reply, v in found.items():
            print("    %-5s vs keep at C=189k, T=60: %+.4f $/stop [%+.4f, %+.4f]; failing share %+.2f (top %+.2f); "
                  "at own C %+.4f; T=15 %+.4f; T=120 %+.4f" % (reply, v["delta"], v["low"], v["high"], v["fail_diff"],
                                                              v["fail_high"], v["own_c"], v["t15"], v["t120"]))
        print("    decision (%d runs): %s" % (runs, decision))


if __name__ == "__main__":
    main()
