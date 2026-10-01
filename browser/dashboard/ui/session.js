// One session: its state, label, id and last call, Close session, and its tabs.
function sessionBlock(session, said) {
  const block = el("div", "session");
  const head = el("div", "shead");
  const idle = session.state === "paused";  // the server's "paused"; README.md says why the page says idle
  head.append(el("span", "badge " + session.state, idle ? "idle" : "active"),
    el("span", "label", session.label), el("span", "sid", session.id), aged("age", "last call ", session.last_call),
    el("span", "spacer"),
    button("Close session", "danger", said, () => {
      const titles = session.tabs.map((tab) => "  " + (tab.title || tab.url)).join("\n");
      const asked = "Close session " + session.id + " (" + session.label + ") and its " + session.tabs.length +
        " tab(s)?" + (titles ? "\n\n" + titles : "");
      return confirm(asked) ? {path: "/close-session", send: {session: session.id}} : null;
    }));
  const list = el("ul", "tabs");
  list.append(...session.tabs.map((tab) => tabItem(tab, said)));
  block.append(head, session.tabs.length ? list : el("div", "quiet", "No open tabs."));
  return block;
}
