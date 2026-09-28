// A profile's panel, under its tab: its Chrome and Open Chrome, then its sessions, the tabs opened by hand and its
// last closed sessions.
function profilePanel(name) {
  const node = el("section", "profile");
  const status = el("span", "state");
  const where = el("span", "where");
  const line = el("div", "line");
  line.append(status, where);
  const who = el("div", "who");
  who.append(el("div", "name", name), line);
  const form = el("form");
  const url = el("input");
  url.placeholder = "URL to open, or blank";
  url.autocomplete = "off";
  url.spellcheck = false;
  const open = el("button", "primary", "Open Chrome");
  open.type = "submit";
  form.append(url, open);
  const head = el("div", "head");
  head.append(who, form);
  const said = el("div", "said");
  const body = el("div");
  node.append(head, said, body);
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    open.disabled = true;
    try {
      await api("/open", {profile: name, url: url.value.trim()});
      url.value = "";
      said.textContent = "";
      await refresh();
    } catch (error) {
      tell(said, error.message, false);
    } finally {
      open.disabled = false;
    }
  });
  return {node, status, where, said, body, key: null, picks: new Map()};
}

function showProfile(panel, profile) {
  panel.status.textContent = profile.pid ? "Chrome running, pid " + profile.pid : "Chrome not running";
  panel.status.className = "state" + (profile.pid ? " up" : "");
  panel.where.textContent = " · port " + profile.port + " · " + profile.folder;
  // Rebuilt only when what it shows changed, so a hand-over choice held open is not reset every 2s.
  const key = JSON.stringify([profile.error, profile.sessions, profile.by_hand, profile.closed]);
  if (key !== panel.key) {
    panel.key = key;
    const parts = [];
    if (profile.error) parts.push(el("div", "said bad", profile.error));
    parts.push(...profile.sessions.map((session) => sessionBlock(session, panel.said)));
    if (!profile.sessions.length) parts.push(el("div", "quiet", "No open sessions."));
    if (profile.by_hand.length) parts.push(byHandBlock(profile, panel.said, panel.picks));
    if (profile.closed.length) parts.push(closedBlock(profile));
    panel.body.replaceChildren(...parts);
  }
  for (const node of panel.body.querySelectorAll("[data-when]")) {
    node.textContent = node.dataset.prefix + ago(Number(node.dataset.when));
  }
}
