stories("Page", "The whole page on made-up data, as browserd serves it: a few cases for the look as a whole. Each " +
  "part's own states are in that part's file.", {
  "A normal day": {
    about: "Jobs with an agent active, one idle and a tab opened by hand; Research with one idle; Recruitment's " +
      "Chrome off.",
    state: () => ({
      profiles: [
        sampleProfile({
          sessions: [sampleSession(), sampleSession({id: "p8d1wz", label: "cover letters", state: "paused",
            last_call: before(42 * 60), tabs: [TABS.greenhouse]})],
          by_hand: [TABS.blank],
          closed: [{id: "fyefym", label: "smoke", closed: before(3 * 3600)}],
        }),
        sampleProfile({name: "Research", folder: "/Users/you/Library/Application Support/Google/Chrome-Research",
          port: 9225, pid: 48377, sessions: [sampleSession({id: "r7t1ka", label: "attention papers", state: "paused",
            last_call: before(5 * 3600), tabs: [TABS.arxiv]})]}),
        sampleProfile({name: "Recruitment", folder: "/Users/you/Library/Application Support/Google/Chrome-Recruitment",
          port: 9226, pid: null}),
      ],
      folders: ["Chrome-Old"],
    }),
  },
  "Waiting on you": {
    about: "An agent on Research is waiting on you: the Needs you list over the strip and Research's profile tab say " +
      "so.",
    state: () => ({
      profiles: [
        sampleProfile({sessions: [sampleSession()]}),
        sampleProfile({name: "Research", folder: "/Users/you/Library/Application Support/Google/Chrome-Research",
          port: 9225, pid: 48377, sessions: [sampleSession({id: "r7t1ka", label: "attention papers",
            tabs: [needing(TABS.arxiv, "sign in to your university library to open the PDF", 240)]})]}),
      ],
      folders: [],
    }),
  },
  "First run": {
    about: "No profiles: the New profile form shows.",
    state: () => ({profiles: [], folders: []}),
  },
  "Server not answering": {
    about: "browserd is stopped: the header says so, and nothing is drawn under it until the server answers.",
    state: () => null,
  },
});
