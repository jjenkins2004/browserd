function form(folders, first, told, good) {
  const shown = newProfile();
  showFolders(shown, folders);
  if (folders.length) shown.folder.value = folders[0];
  shown.empty.hidden = !first;
  if (told) tell(shown.said, told, good);
  return onPanel(shown.node);
}

stories("New profile", "Opened by the header's New profile button: make a profile, in a new Chrome folder or one " +
  "taken over with its logins.", {
  "First run": {
    about: "No profiles yet: the page opens here.",
    render: () => form([], true),
  },
  "A folder to take over": {
    about: "Chrome-* folders no profile uses are offered, logins and all; one is picked here.",
    render: () => form(["Chrome-Old", "Chrome-Work"], false),
  },
  "Made": {
    about: "The server made it: its folder and port, in green. Its tab joins the strip at once.",
    render: () => form([], false, "Made Jobs: folder /Users/you/Library/Application Support/Google/Chrome-Jobs, " +
      "port 9224.", true),
  },
  "Refused": {
    about: "A name the server refuses, with the rule it broke.",
    render: () => form([], false, "a profile's name is a letter, then up to 23 letters, digits or dashes", false),
  },
});
