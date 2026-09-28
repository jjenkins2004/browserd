// Made-up tabs, sessions and profiles the stories draw from; none of it is anyone's real data.
function before(seconds) {
  return Date.now() / 1000 - seconds;
}

const TABS = {
  lever: {id: "a7qm", title: "Software Engineer, Backend", url: "https://jobs.lever.co/stripe/4f1c2a9e"},
  workday: {id: "b2xk", title: "Sign in · Workday", url: "https://stripe.wd5.myworkdayjobs.com/en-US/careers/login"},
  greenhouse: {id: "c9rt", title: "Apply · Greenhouse", url: "https://boards.greenhouse.io/figma/jobs/5123874"},
  blank: {id: "d4hn", title: "New Tab", url: "chrome://newtab/"},
  arxiv: {id: "e5mv", title: "Attention Is All You Need", url: "https://arxiv.org/abs/1706.03762"},
  linear: {id: "h3pn", title: "Careers · Linear", url: "https://linear.app/careers"},
};

// A tab its agent marked as needing you, seconds ago, with a note saying what for.
function needing(tab, note, seconds) {
  return {...tab, needs_action: {note, since: before(seconds)}};
}

function sampleSession(fields) {
  return {id: "k3f9x2", label: "stripe backend", state: "active", last_call: before(8),
    tabs: [TABS.lever, TABS.workday], ...fields};
}

function sampleProfile(fields) {
  return {name: "Jobs", folder: "/Users/you/Library/Application Support/Google/Chrome-Jobs", port: 9223, pid: 48211,
    error: null, sessions: [], by_hand: [], closed: [], ...fields};
}
