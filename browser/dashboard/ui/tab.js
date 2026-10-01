// One Chrome tab's row: its title and URL, with Show and Close. A tab with needs_input says what it needs you for,
// and since when.
function tabItem(tab, said) {
  const item = el("li", "tab");
  item.append(el("span", "title", tab.title || "(untitled)"), el("span", "url", tab.url),
    button("Show", "", said, () => ({path: "/show", send: {tab: tab.id}})),
    button("Close", "danger", said, () => ({path: "/close-tab", send: {tab: tab.id}})));
  if (tab.needs_input) {
    item.classList.add("needs");
    const ask = el("div", "ask");
    ask.append(el("span", "", "Needs you" + (tab.needs_input.note ? ": " + tab.needs_input.note : "")),
      aged("age", " · ", tab.needs_input.since));
    item.append(ask);
  }
  return item;
}
