function inStrip(...shown) {
  const bar = el("nav", "strip");
  for (const [profile, chosen] of shown) {
    const tab = profileTab(profile.name, () => {});
    showProfileTab(tab, profile);
    tab.node.setAttribute("aria-selected", String(chosen));
    bar.append(tab.node);
  }
  return bar;
}

function someSessions(active, idle) {
  const made = [];
  for (let i = 0; i < active; i++) made.push(sampleSession({id: "busy0" + i}));
  for (let i = 0; i < idle; i++) made.push(sampleSession({id: "idle0" + i, state: "paused", last_call: before(3600)}));
  return made;
}

stories("Profile tab", "A profile's tab in the strip across the top, not a Chrome tab: is its Chrome up, does a " +
  "tab need you, is an agent working. Everything else is in the profile's panel.", {
  "Chrome running, nothing active": {
    about: "Just the name.",
    render: () => inStrip([sampleProfile(), false]),
  },
  "Chrome off": {
    about: "The name is dimmed. Open Chrome, or an agent's first tab_open, starts it.",
    render: () => inStrip([sampleProfile({pid: null}), false]),
  },
  "Tabs could not be listed": {
    about: "The one red light: its Chrome did not answer, and the panel says why.",
    render: () => inStrip([sampleProfile({error: "could not list the Jobs Chrome's tabs: connection refused"}), false]),
  },
  "One active": {
    about: "One session's agent made a browserd call in the last 30 minutes.",
    render: () => inStrip([sampleProfile({sessions: someSessions(1, 0)}), false]),
  },
  "Several active": {
    about: "Counted, not listed.",
    render: () => inStrip([sampleProfile({sessions: someSessions(3, 0)}), false]),
  },
  "A tab needs you": {
    about: "One of its tabs is waiting on you: amber, before the active count.",
    render: () => inStrip([sampleProfile({sessions: [sampleSession({tabs: [needing(TABS.workday, "log in", 60)]})]}), false]),
  },
  "Only idle sessions": {
    about: "Idle sessions do not show on the tab; the panel lists them.",
    render: () => inStrip([sampleProfile({sessions: someSessions(0, 2)}), false]),
  },
  "Chosen, in the strip": {
    about: "The chosen tab takes the panel's color and joins the panel under it; the others sit on the ground.",
    render: () => [
      inStrip([sampleProfile({sessions: someSessions(1, 1)}), true],
        [sampleProfile({name: "Research", sessions: someSessions(0, 1)}), false],
        [sampleProfile({name: "Recruitment", pid: null}), false]),
      onPanel(el("div", "quiet", "The chosen profile's panel.")),
    ],
  },
  "Longest name": {
    about: "A name is at most 24 characters, so the strip never needs to cut one.",
    render: () => inStrip([sampleProfile({name: "Recruitment-Outreach-Q4x", sessions: someSessions(2, 0)}), false]),
  },
});
