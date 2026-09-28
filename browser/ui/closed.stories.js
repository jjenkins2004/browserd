stories("Closed sessions", "A profile's last closed sessions, newest first, at most 10 (page.CLOSED_SHOWN): label, " +
  "id, and when it was closed.", {
  "One": {
    about: "A single session closed a few minutes ago.",
    render: () => onPanel(closedBlock(sampleProfile({closed: [{id: "fyefym", label: "smoke", closed: before(300)}]}))),
  },
  "Ten": {
    about: "The most shown; older ones are kept in state.db but not listed.",
    render: () => onPanel(closedBlock(sampleProfile({closed: [
      [40, "stripe backend"], [900, "cover letters"], [3600, "figma design eng"], [5 * 3600, "smoke"],
      [9 * 3600, "linear infra"], [86400, "attention papers"], [2 * 86400, "smoke"], [3 * 86400, "slides click_at test"],
      [6 * 86400, "recruiter outreach"], [12 * 86400, "first run"],
    ].map(([seconds, label], i) => ({id: "cl0se" + i, label, closed: before(seconds)}))}))),
  },
});
