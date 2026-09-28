function byHand(fields) {
  const said = el("div", "said");
  return onPanel(byHandBlock(sampleProfile(fields), said, new Map()), said);
}

const TWO_SESSIONS = () => [sampleSession(), sampleSession({id: "p8d1wz", label: "cover letters", state: "paused",
  last_call: before(42 * 60)})];

stories("Opened by hand", "Tabs in a profile's Chrome that no session owns: ones you opened yourself, or a " +
  "session's tab caught opening as the session closed. Each can be handed to an open session of the profile.", {
  "One tab, sessions to hand it to": {
    about: "The picker lists the profile's open sessions, active or idle; Hand over gives the tab to the one picked.",
    render: () => byHand({by_hand: [TABS.blank], sessions: TWO_SESSIONS()}),
  },
  "No open session": {
    about: "With no session to hand it to, there is no picker; Show and Close still work.",
    render: () => byHand({by_hand: [TABS.blank]}),
  },
  "Several tabs": {
    about: "Each tab keeps its own pick across polls.",
    render: () => byHand({by_hand: [TABS.blank, TABS.arxiv, TABS.linear], sessions: TWO_SESSIONS()}),
  },
});
