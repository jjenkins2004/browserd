function drawnProfile(profile) {
  const shown = profilePanel(profile.name);
  showProfile(shown, profile);
  return onPanel(shown.node);
}

stories("Profile", "A profile's panel, under its tab: its Chrome and Open Chrome, then its sessions and the tabs " +
  "opened by hand. Each of those has its own states; these are the panel as a whole.", {
  "Chrome off, nothing open": {
    about: "A profile at rest: Open Chrome starts its Chrome, in a blank window.",
    render: () => drawnProfile(sampleProfile({pid: null})),
  },
  "A busy profile": {
    about: "One agent active, one idle, and a tab opened by hand.",
    render: () => drawnProfile(sampleProfile({
      sessions: [sampleSession(), sampleSession({id: "p8d1wz", label: "cover letters", state: "paused",
        last_call: before(42 * 60), tabs: [TABS.greenhouse]})],
      by_hand: [TABS.blank],
    })),
  },
  "Tabs could not be listed": {
    about: "Its Chrome did not answer: the reason leads the panel, and its open sessions show with no tabs.",
    render: () => drawnProfile(sampleProfile({error: "could not list the Jobs Chrome's tabs: connection refused",
      sessions: [sampleSession({tabs: []})]})),
  },
});
