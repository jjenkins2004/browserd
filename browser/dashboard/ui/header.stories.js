function headerSaying(text) {
  const shown = header(() => {});
  showStatus(shown, text);
  return shown.node;
}

stories("Header", "The top line, the same whichever profile is shown: browserd and New profile, and why the page " +
  "could not read the server, only when it could not.", {
  "At rest": {
    about: "The server answers: the name and New profile, nothing else.",
    render: () => headerSaying(""),
  },
  "Server not answering": {
    about: "The poll could not connect: browserd is stopped or restarting. The next poll, 2 s later, tries again.",
    render: () => headerSaying("the server is not answering"),
  },
  "Server refused": {
    about: "The server answered with an error, like a token from before a restart, which a reload fixes.",
    render: () => headerSaying("reload the page: this request's token is not the one this server gave"),
  },
});
