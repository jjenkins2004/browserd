function byHand(fields) {
  const said = el("div", "said");
  return onPanel(byHandBlock(sampleProfile(fields), said), said);
}

stories("Opened by hand", "Tabs in a profile's Chrome that no session owns: ones you opened yourself, or a " +
  "session's tab caught opening as the session closed. Each has Show and Close.", {
  "One tab": {
    about: "A tab you opened yourself.",
    render: () => byHand({by_hand: [TABS.blank]}),
  },
  "Several tabs": {
    about: "One row per tab.",
    render: () => byHand({by_hand: [TABS.blank, TABS.arxiv, TABS.linear]}),
  },
});
