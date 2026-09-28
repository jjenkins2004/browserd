// A profile's tabs no session owns, each with Show and Close.
function byHandBlock(profile, said) {
  const block = el("div");
  const list = el("ul", "tabs");
  list.append(...profile.by_hand.map((tab) => tabItem(tab, said)));
  block.append(el("h3", "", "Opened by hand"), list);
  return block;
}
