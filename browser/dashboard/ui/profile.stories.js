function drawnProfile(profile) {
  const shown = profilePanel(profile.name);
  showProfile(shown, profile);
  return onPanel(shown.node);
}

stories("Profile", "A profile's panel, under its tab: its Chrome, Open Chrome, Quit Chrome while it runs, and Delete " +
  "profile, then its sessions, the idle ones folded, and the tabs opened by hand. Each of those has its own states; " +
  "these are the panel as a whole.", {
  "Chrome off, nothing open": {
    about: "A profile at rest: Open Chrome starts its Chrome, in a window on browserd's placeholder tab, and there is nothing to quit. Delete " +
      "profile asks first, then removes it, keeping its folder if its Chrome ever ran.",
    render: () => drawnProfile(sampleProfile({pid: null})),
  },
  "A busy profile": {
    about: "One agent active, one idle folded under it, and a tab opened by hand. Open Chrome brings its Chrome to " +
      "the front, and Quit Chrome quits it, closing every tab; the sessions stay open.",
    render: () => drawnProfile(sampleProfile({
      sessions: [sampleSession(), sampleSession({id: "p8d1wz", label: "cover letters", state: "paused",
        last_call: before(42 * 60), tabs: [TABS.greenhouse]})],
      by_hand: [TABS.blank],
    })),
  },
  "Several sessions, most idle": {
    about: "Two agents active and three gone idle, one of those with a tab that needs you. The idle ones fold into " +
      "one line that names them and opens on a click, newest call first, but the one whose tab needs you stays out.",
    render: () => drawnProfile(sampleProfile({sessions: [
      sampleSession({id: "p8d1wz", label: "cover letters", state: "paused", last_call: before(42 * 60),
        tabs: [TABS.greenhouse]}),
      sampleSession({id: "m4q7ra", label: "linear infra", state: "paused", last_call: before(3 * 3600),
        tabs: [needing(TABS.linear, "sign in with Google", 2 * 3600)]}),
      sampleSession({id: "t2v9cd", label: "attention papers", state: "paused", last_call: before(26 * 3600),
        tabs: [TABS.arxiv]}),
      sampleSession(),
      sampleSession({id: "w6h3ne", label: "figma design eng", last_call: before(3 * 60), tabs: [TABS.blank]}),
    ]})),
  },
  "Every session idle": {
    about: "No agent has made a call for 30 minutes: no session is active, and the idle ones stay folded.",
    render: () => drawnProfile(sampleProfile({sessions: [
      sampleSession({state: "paused", last_call: before(35 * 60)}),
      sampleSession({id: "p8d1wz", label: "cover letters", state: "paused", last_call: before(5 * 3600),
        tabs: [TABS.greenhouse]}),
    ]})),
  },
  "Tabs could not be listed": {
    about: "Its Chrome did not answer: the reason leads the panel, and its open sessions show with no tabs.",
    render: () => drawnProfile(sampleProfile({error: "could not list the Jobs Chrome's tabs: connection refused",
      sessions: [sampleSession({tabs: []})]})),
  },
});
