// One Chrome tab's row: its id, title and URL, with Show and Close; extra holds more controls, like a hand-over. A
// tab with needs_action (not sent by the server yet; README.md) says what it needs you for, and since when.
function tabItem(tab, said, extra) {
  const item = el("li", "tab");
  item.append(el("span", "tid", tab.id), el("span", "title", tab.title || "(untitled)"), el("span", "url", tab.url),
    button("Show", "", said, () => ({path: "/show", send: {tab: tab.id}})),
    button("Close", "danger", said, () => ({path: "/close-tab", send: {tab: tab.id}})), ...(extra || []));
  if (tab.needs_action) {
    item.classList.add("needs");
    const ask = el("div", "ask");
    ask.append(el("span", "", "Needs you" + (tab.needs_action.note ? ": " + tab.needs_action.note : "")),
      aged("age", " · ", tab.needs_action.since));
    item.append(ask);
  }
  return item;
}
