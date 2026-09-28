// A profile's last closed sessions, newest first.
function closedBlock(profile) {
  const block = el("div");
  block.append(el("h3", "", "Closed sessions"));
  for (const session of profile.closed) {
    const line = el("div", "closed");
    line.append(el("span", "", session.label + " · " + session.id + " · "), aged("", "closed ", session.closed));
    block.append(line);
  }
  return block;
}
