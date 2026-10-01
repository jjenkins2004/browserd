// Every tab an agent marked as needing your input, from every profile, longest waiting first, over the strip: its
// profile (a click shows that profile), its session, the agent's note, how long, and Show. Hidden while there are none.
function needsList(pick) {
  const node = el("section", "needs-list");
  node.hidden = true;
  const list = el("ul");
  const said = el("div", "said");
  node.append(el("h2", "", "Needs you"), list, said);
  return {node, list, said, pick, key: null};
}

function showNeeds(shown, profiles) {
  const marked = profiles.flatMap((profile) => profile.sessions.flatMap((session) => session.tabs
    .filter((tab) => tab.needs_input).map((tab) => ({profile: profile.name, session, tab}))))
    .sort((a, b) => a.tab.needs_input.since - b.tab.needs_input.since);
  shown.node.hidden = !marked.length;
  if (!marked.length) shown.said.textContent = "";  // a refusal belongs to rows now gone
  // Rebuilt only on a change; README.md, "Agent Gotchas & Invariants", says why.
  const key = JSON.stringify(marked.map(({profile, session, tab}) =>
    [profile, session.label, tab.id, tab.title || tab.url, tab.needs_input]));
  if (key !== shown.key) {
    shown.key = key;
    shown.list.replaceChildren(...marked.map(({profile, session, tab}) => {
      const item = el("li");
      const where = el("button", "where", profile);
      where.type = "button";
      where.addEventListener("click", () => shown.pick(profile));
      const note = el("span", "note", tab.needs_input.note);
      note.title = tab.title || tab.url;
      item.append(where, el("span", "label", session.label), note, aged("age", "", tab.needs_input.since),
        button("Show", "", shown.said, () => ({path: "/show", send: {tab: tab.id}})));
      return item;
    }));
  }
  for (const node of shown.list.querySelectorAll("[data-when]")) {
    node.textContent = node.dataset.prefix + ago(Number(node.dataset.when));
  }
}
