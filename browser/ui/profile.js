// A profile's panel, under its tab: its Chrome, Open Chrome, Quit Chrome while it runs, and Delete profile, then its
// sessions, the idle ones folded, and the tabs opened by hand.
function profilePanel(name) {
  const node = el("section", "profile");
  const status = el("span", "state");
  const line = el("div", "line");
  line.append(status);
  const who = el("div", "who");
  who.append(el("div", "name", name), line);
  const form = el("form");
  const open = el("button", "primary", "Open Chrome");
  open.type = "submit";
  form.append(open);
  const said = el("div", "said");
  const quit = button("Quit Chrome", "", said, () => ({path: "/quit-chrome", send: {profile: name}}));
  const remove = button("Delete profile", "danger", said, () => confirm("Delete the profile " + name + "? Its Chrome " +
    "quits, and its open sessions close with their tabs. Its folder, if its Chrome ever ran, is kept, logins and all, " +
    "for New profile to take over.") ? {path: "/delete-profile", send: {profile: name}} : null);
  const head = el("div", "head");
  head.append(who, form, quit, remove);
  const body = el("div");
  node.append(head, said, body);
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    open.disabled = true;
    try {
      await api("/open", {profile: name});
      said.textContent = "";
      await refresh();
    } catch (error) {
      tell(said, error.message, false);
    } finally {
      open.disabled = false;
    }
  });
  return {node, status, quit, said, body, key: null, idleOpen: false};
}

function showProfile(panel, profile) {
  panel.status.textContent = profile.pid ? "Chrome running" : "Chrome not running";
  panel.status.className = "state" + (profile.pid ? " up" : "");
  panel.quit.hidden = !profile.pid;
  // Rebuilt only on a change; README.md, "Agent Gotchas & Invariants", says why.
  const key = JSON.stringify([profile.error, profile.sessions, profile.by_hand]);
  if (key !== panel.key) {
    panel.key = key;
    const opened = panel.body.querySelector(".idle-fold");
    if (opened) panel.idleOpen = opened.open;
    // README.md, "Agent Gotchas & Invariants", says which sessions fold and why only those are sorted.
    const unfolded = profile.sessions.filter((session) => session.state === "active"
      || session.tabs.some((tab) => tab.needs_action));
    const idle = profile.sessions.filter((session) => !unfolded.includes(session))
      .sort((a, b) => b.last_call - a.last_call);
    const parts = [];
    if (profile.error) parts.push(el("div", "said bad", profile.error));
    parts.push(...unfolded.map((session) => sessionBlock(session, panel.said)));
    if (!unfolded.length) parts.push(el("div", "quiet", idle.length ? "No active sessions." : "No open sessions."));
    if (idle.length) parts.push(idleFold(panel, idle));
    if (profile.by_hand.length) parts.push(byHandBlock(profile, panel.said));
    panel.body.replaceChildren(...parts);
  }
  for (const node of panel.body.querySelectorAll("[data-when]")) {
    node.textContent = node.dataset.prefix + ago(Number(node.dataset.when));
  }
}

// The idle sessions, folded under one line: how many, their labels, and the newest one's last call.
function idleFold(panel, idle) {
  const fold = el("details", "idle-fold");
  fold.open = panel.idleOpen;
  const summary = el("summary");
  summary.append(el("span", "", idle.length + " idle session" + (idle.length === 1 ? "" : "s")),
    el("span", "names", idle.map((session) => session.label).join(", ")), aged("age", "last call ", idle[0].last_call));
  fold.append(summary, ...idle.map((session) => sessionBlock(session, panel.said)));
  return fold;
}
