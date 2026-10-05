// A story's frame: stories() takes the part's states, show() draws one. Nothing here reaches the browserd server or
// a Chrome: api answers from the story, and in a part's story refresh() does nothing.
let shelf = null;

function stories(part, about, list) {
  shelf = list;
}

// A part that sits on a profile's panel on the page is drawn on one here too.
function onPanel(...nodes) {
  const box = el("div", "panel");
  box.append(...nodes);
  return box;
}

function show(name) {
  const main = document.querySelector("main");
  try {
    const story = shelf && shelf[name];
    if (!story) throw new Error("this file has no story " + JSON.stringify(name));
    if (story.state) {
      const state = story.state();
      api = async (path) => {
        if (state === null) throw new TypeError("the story's server is not answering");
        return path === "/state" ? state : {};
      };
      start(main);
    } else {
      refresh = async () => {};
      api = () => new Promise((done) => setTimeout(() => done({}), 300));
      main.append(...[story.render()].flat());
    }
  } catch (error) {
    main.append(el("pre", "broken", String(error.stack || error)));
  }
}
