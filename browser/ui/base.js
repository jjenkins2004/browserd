// What every part shares: the server's requests, making elements, messages, and times shown as how long ago.
async function api(path, body) {
  const options = {headers: {"X-Browserd-Token": TOKEN}};
  if (body !== undefined) {
    options.method = "POST";
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(body);
  }
  const response = await fetch(path, options);
  const answer = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(answer.error || "the server answered " + response.status);
  return answer;
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function tell(said, text, good) {
  said.textContent = text;
  said.className = "said " + (good ? "good" : "bad");
}

function ago(seconds) {
  const gone = Math.max(0, Date.now() / 1000 - seconds);
  if (gone < 60) return Math.round(gone) + " s ago";
  if (gone < 3600) return Math.round(gone / 60) + " min ago";
  if (gone < 86400) return Math.round(gone / 3600) + " h ago";
  return Math.round(gone / 86400) + " d ago";
}

// A time shown as how long ago, kept current by each poll.
function aged(className, prefix, when) {
  const node = el("span", className);
  node.dataset.when = when;
  node.dataset.prefix = prefix;
  node.textContent = prefix + ago(when);
  return node;
}

// A button that posts what act() returns, if anything, then shows the state it left; a refusal goes in said.
function button(text, className, said, act) {
  const node = el("button", "small " + className, text);
  node.type = "button";
  node.addEventListener("click", async () => {
    const asked = act();
    if (!asked) return;
    node.disabled = true;
    try {
      await api(asked.path, asked.send);
      said.textContent = "";
      await refresh();
    } catch (error) {
      tell(said, error.message, false);
    } finally {
      node.disabled = false;
    }
  });
  return node;
}
