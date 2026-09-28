// A profile's panel, under its tab: its Chrome, Open Chrome, Quit Chrome while it runs, and Delete profile, then its
// sessions and the tabs opened by hand.
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
  return {node, status, quit, said, body, key: null};
}

function showProfile(panel, profile) {
  panel.status.textContent = profile.pid ? "Chrome running" : "Chrome not running";
  panel.status.className = "state" + (profile.pid ? " up" : "");
  panel.quit.hidden = !profile.pid;
  // Rebuilt only on a change; README.md, "Agent Gotchas & Invariants", says why.
  const key = JSON.stringify([profile.error, profile.sessions, profile.by_hand]);
  if (key !== panel.key) {
    panel.key = key;
    const parts = [];
    if (profile.error) parts.push(el("div", "said bad", profile.error));
    parts.push(...profile.sessions.map((session) => sessionBlock(session, panel.said)));
    if (!profile.sessions.length) parts.push(el("div", "quiet", "No open sessions."));
    if (profile.by_hand.length) parts.push(byHandBlock(profile, panel.said));
    panel.body.replaceChildren(...parts);
  }
  for (const node of panel.body.querySelectorAll("[data-when]")) {
    node.textContent = node.dataset.prefix + ago(Number(node.dataset.when));
  }
}
