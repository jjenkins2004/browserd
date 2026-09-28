// The page: the header, the strip of profile tabs, one profile's panel at a time, and the poll that keeps them
// current. Only this part reads /state.
const NEW = "+new";  // the New profile form's hash; a profile's name never holds a "+"

// One profile is shown at a time, named in the URL's hash so a reload keeps it. A name is letters, digits and
// dashes, so the hash is never encoded.
let chosen = location.hash.slice(1);
let order = [];
// Panels and tabs are kept and changed in place, never rebuilt, so a URL being typed survives each poll.
const rows = new Map();
let heading, strip, box, newForm;  // drawn by start()

function choose(name) {
  chosen = name;
  history.replaceState(null, "", "#" + name);
  showChosen();
}

function showChosen() {
  // A profile gone, or none chosen yet, falls back to the first, and with no profiles to New profile.
  const shown = chosen === NEW || rows.has(chosen) ? chosen : (order[0] ?? NEW);
  for (const [name, row] of rows) {
    row.panel.node.hidden = name !== shown;
    row.tab.node.setAttribute("aria-selected", String(name === shown));
  }
  newForm.node.hidden = shown !== NEW;
}

function showProfiles(profiles) {
  const names = new Set(profiles.map((p) => p.name));
  for (const [name, row] of rows) {
    if (!names.has(name)) {
      row.panel.node.remove();
      row.tab.node.remove();
      rows.delete(name);
    }
  }
  let before = null;
  for (const p of profiles) {
    let row = rows.get(p.name);
    if (!row) {
      row = {panel: profilePanel(p.name), tab: profileTab(p.name, () => choose(p.name))};
      rows.set(p.name, row);
    }
    showProfileTab(row.tab, p);
    showProfile(row.panel, p);
    if (row.panel.node.previousElementSibling !== (before && before.panel.node) || row.panel.node.parentNode !== box) {
      if (before) before.panel.node.after(row.panel.node); else box.prepend(row.panel.node);
      if (before) before.tab.node.after(row.tab.node); else strip.prepend(row.tab.node);
    }
    before = row;
  }
  order = profiles.map((p) => p.name);
  showChosen();
  box.hidden = false;
  newForm.empty.hidden = profiles.length > 0;
}

async function refresh() {
  try {
    const state = await api("/state");
    showProfiles(state.profiles);
    showFolders(newForm, state.folders);
    showStatus(heading, "");
  } catch (error) {
    showStatus(heading, error instanceof TypeError ? "the server is not answering" : error.message);
  }
}

// Each poll waits for the one before, so a slow Chrome cannot pile requests up.
async function poll() {
  await refresh();
  setTimeout(poll, 2000);
}

function start(main) {
  heading = header(() => choose(NEW));
  strip = el("nav", "strip");
  strip.setAttribute("role", "tablist");
  strip.setAttribute("aria-label", "Profiles");
  newForm = newProfile();
  newForm.node.hidden = true;
  box = el("div", "panel");
  box.hidden = true;  // until the server first answers
  box.append(newForm.node);
  main.append(heading.node, strip, box);
  addEventListener("hashchange", () => {
    chosen = location.hash.slice(1);
    showChosen();
  });
  poll();
}
