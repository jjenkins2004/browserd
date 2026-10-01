// The header, the same whichever profile is shown: browserd, New profile, and why the server could not be read,
// only when it could not.
function header(add) {
  const node = el("header");
  const status = el("span", "status");
  const make = el("button", "small", "+ New profile");
  make.type = "button";
  make.addEventListener("click", add);
  node.append(el("h1", "", "browserd"), status, el("span", "spacer"), make);
  return {node, status};
}

function showStatus(shown, text) {
  shown.status.textContent = text;
}
