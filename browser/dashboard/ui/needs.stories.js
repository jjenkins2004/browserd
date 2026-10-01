function listed(profiles, refused) {
  const shown = needsList(() => {});
  showNeeds(shown, profiles);
  if (refused) tell(shown.said, refused, false);
  return shown.node;
}

stories("Needs you", "Every tab an agent marked as needing your input, from every profile, longest waiting first, " +
  "over the strip of profiles: the profile (a click shows it), the session, the agent's note, how long, and Show, " +
  "which brings the tab to the front. An agent clears its own mark once you tell it you are done; closing the tab " +
  "clears it too. While no tab needs you, the list is hidden.", {
  "One tab": {
    about: "An agent needs you to log in; hover the note for the tab's title.",
    render: () => listed([sampleProfile({sessions: [sampleSession({tabs: [TABS.lever,
      needing(TABS.workday, "log in to Workday, the password is not saved", 180)]})]})]),
  },
  "Several, across profiles": {
    about: "Three tabs on two profiles: the one waiting longest comes first, and a long note wraps rather than " +
      "widening the list.",
    render: () => listed([
      sampleProfile({sessions: [
        sampleSession({tabs: [needing(TABS.workday, "log in to Workday", 60)]}),
        sampleSession({id: "w6h3ne", label: "figma design eng", tabs: [needing(TABS.greenhouse, "solve the captcha, " +
          "then check the salary field: the posting gives a range and the form takes one number", 20 * 60)]}),
      ]}),
      sampleProfile({name: "Research", sessions: [sampleSession({id: "r7t1ka", label: "attention papers",
        tabs: [needing(TABS.arxiv, "sign in to your university library to open the PDF", 7 * 60)]})]}),
    ]),
  },
  "Show refused": {
    about: "Chrome did not come to the front: the refusal shows in red under the list, and the row stays.",
    render: () => listed([sampleProfile({sessions: [sampleSession({tabs: [needing(TABS.workday, "log in to Workday",
      60)]})]})], "tab b2xk is picked in the Jobs Chrome, but macOS did not bring that Chrome to the front"),
  },
});
