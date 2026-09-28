function card(fields, refused) {
  const said = el("div", "said");
  if (refused) tell(said, refused, false);
  return onPanel(sessionBlock(sampleSession(fields), said), said);
}

stories("Session", "One agent's task on a profile: its state, label, id and last call, and its tabs. An agent " +
  "never ends its own; Close session closes it and every tab of it.", {
  "Active": {
    about: "Its agent made a browserd call in the last 30 minutes: the green chip pulses.",
    render: () => card({}),
  },
  "Idle": {
    about: "No call for 30 minutes (the server's \"paused\"), so its tabs' DevTools processes are stopped. Its " +
      "agent's next call makes it active again.",
    render: () => card({state: "paused", last_call: before(42 * 60)}),
  },
  "Idle for days": {
    about: "An agent that finished long ago and was never closed.",
    render: () => card({state: "paused", last_call: before(3 * 86400), tabs: [TABS.greenhouse]}),
  },
  "A tab needs you": {
    about: "One of its tabs is marked as waiting on you; the rest of the card is as usual.",
    render: () => card({tabs: [TABS.lever, needing(TABS.workday, "log in to Workday, the password is not saved", 180)]}),
  },
  "No open tabs": {
    about: "Its tabs were all closed; the session stays open until it is closed.",
    render: () => card({tabs: []}),
  },
  "Many tabs": {
    about: "Every tab is listed; nothing is folded away.",
    render: () => card({tabs: [TABS.lever, TABS.workday, TABS.greenhouse, TABS.linear, TABS.arxiv, TABS.blank]}),
  },
  "Long label, untitled tab": {
    about: "A long label wraps; a tab with no title yet shows (untitled).",
    render: () => card({label: "apply to every backend and infra role at stripe, figma and linear this week",
      tabs: [{id: "f1zc", title: "", url: "https://jobs.lever.co/linear/loading"}]}),
  },
  "A button refused": {
    about: "A refusal shows in red in the profile's message line, which sits above its sessions (here, under the card).",
    render: () => card({}, "there is no open session 'k3f9x2'"),
  },
});
