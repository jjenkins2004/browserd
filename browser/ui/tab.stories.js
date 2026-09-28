function row(tab) {
  const said = el("div", "said");
  const list = el("ul", "tabs");
  list.append(tabItem(tab, said));
  return onPanel(list, said);
}

stories("Tab", "One Chrome tab: its four-character id, title and URL. Show brings it to the front of its Chrome; " +
  "Close closes it.", {
  "A tab": {
    about: "The usual row.",
    render: () => row(TABS.lever),
  },
  "Untitled": {
    about: "A page with no title yet shows (untitled).",
    render: () => row({id: "f1zc", title: "", url: "https://jobs.lever.co/linear/loading"}),
  },
  "Long URL": {
    about: "A long URL wraps under the title rather than widening the page.",
    render: () => row({id: "g8wd", title: "Apply · Workday", url: "https://stripe.wd5.myworkdayjobs.com/en-US/careers/job/" +
      "San-Francisco-CA/Software-Engineer--Backend_JR-104233/apply/applyManually?source=LinkedIn&utm_campaign=q4"}),
  },
  "Needs you": {
    about: "Its agent marked it as waiting on you, and said what for. Show brings it to the front so you can do it.",
    render: () => row(needing(TABS.workday, "log in to Workday, the password is not saved", 180)),
  },
  "Needs you, long note": {
    about: "A long note wraps under the row.",
    render: () => row(needing(TABS.greenhouse, "solve the captcha, then check the salary field: the posting says " +
      "the range is required but gives none, so pick what you want to ask for and I will carry on from there", 45)),
  },
  "New Tab page": {
    about: "A blank tab, often one opened by hand.",
    render: () => row(TABS.blank),
  },
});
