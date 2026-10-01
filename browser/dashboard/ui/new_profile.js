// The New profile form, opened by the header's New profile button: a name, and a new folder or one taken over
// with its logins.
function newProfile() {
  const node = el("section");
  const empty = el("p", "quiet", "No profiles yet.");
  const form = el("form");
  const name = el("input");
  name.placeholder = "Name, like Jobs";
  name.autocomplete = "off";
  name.spellcheck = false;
  name.required = true;
  const folder = el("select");
  const make = el("button", "primary", "New profile");
  make.type = "submit";
  form.append(name, folder, make);
  const said = el("div", "said");
  node.append(el("h2", "", "New profile"), empty, form, said);
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    make.disabled = true;
    try {
      const made = await api("/profiles", {name: name.value.trim(), folder: folder.value});
      tell(said, "Made " + made.name + ": folder " + made.folder + ", port " + made.port + ".", true);
      name.value = "";
      await refresh();
    } catch (error) {
      tell(said, error.message, false);
    } finally {
      make.disabled = false;
    }
  });
  return {node, empty, folder, said, key: null};
}

function showFolders(shown, folders) {
  const key = JSON.stringify(folders);
  if (key === shown.key) return;  // rebuilt only on a change, so a dropdown held open is not closed every 2s
  shown.key = key;
  const picked = shown.folder.value;
  const fresh = el("option", "", "in a new folder, Chrome-<name>");
  fresh.value = "";
  shown.folder.replaceChildren(fresh, ...folders.map((name) => {
    const option = el("option", "", "taking over " + name + ", logins and all");
    option.value = name;
    return option;
  }));
  if (folders.includes(picked)) shown.folder.value = picked;
}
