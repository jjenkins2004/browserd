// A profile's tabs no session owns, each with a picker to hand it to one of the profile's open sessions.
function byHandBlock(profile, said, picks) {
  const block = el("div");
  const list = el("ul", "tabs");
  list.append(...profile.by_hand.map((tab) => {
    if (!profile.sessions.length) return tabItem(tab, said);
    const pick = el("select");
    pick.append(...profile.sessions.map((session) => {
      const option = el("option", "", session.label + " · " + session.id);
      option.value = session.id;
      return option;
    }));
    // A rebuild keeps the choice; a session gone since leaves none, and the server refuses it.
    if (picks.has(tab.id)) pick.value = picks.get(tab.id);
    pick.addEventListener("change", () => picks.set(tab.id, pick.value));
    const hand = el("span", "inline");
    hand.append(pick, button("Hand over", "", said, () => ({path: "/handover", send: {tab: tab.id, session: pick.value}})));
    return tabItem(tab, said, [hand]);
  }));
  block.append(el("h3", "", "Opened by hand"), list);
  return block;
}
