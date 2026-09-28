// A profile's tab in the strip (not a Chrome tab): its name, dimmed when its Chrome is off, how many of its tabs need
// you, how many of its sessions are active, and a red light only when its tabs could not be listed.
function profileTab(name, pick) {
  const node = el("button", "ptab");
  node.type = "button";
  node.setAttribute("role", "tab");
  const alarm = el("span", "alarm");
  const needs = el("span", "count needs");
  const active = el("span", "count");
  node.append(alarm, el("span", "pname", name), needs, active);
  node.addEventListener("click", pick);
  return {node, alarm, needs, active};
}

function showProfileTab(tab, profile) {
  const needs = profile.sessions.flatMap((s) => s.tabs).filter((t) => t.needs_action).length;
  const active = profile.sessions.filter((s) => s.state === "active").length;
  tab.node.classList.toggle("off", !profile.pid);
  tab.node.title = profile.error ? "its tabs could not be listed" : profile.pid ? "" : "Chrome not running";
  tab.alarm.hidden = !profile.error;
  tab.needs.hidden = !needs;
  tab.needs.textContent = needs === 1 ? "needs you" : needs + " need you";
  tab.active.hidden = !active;
  tab.active.textContent = active + " active";
}
