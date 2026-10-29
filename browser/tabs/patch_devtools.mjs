// npm ci's postinstall: patches the chrome-devtools-mcp that package.json pins so that, run with
// BROWSERD_NO_RUNTIME_ENABLE=1, it never sends Runtime.enable to a page or its workers (an extension's service worker
// still gets it, from collectors/ServiceWorkerCollector.js, and no page sees that). A CDP client that has sent it makes
// Chrome serialize the arguments of every console call, which a page notices: bot checks (reCAPTCHA's, hCaptcha's) log
// an Error and see its stack formatted. The patch is exact text for the pinned version, so a version it does not fit
// fails the install, saying so; a file it already patched is left as it is.
import {readFileSync, writeFileSync} from "node:fs";

const BUILD = new URL("../../node_modules/chrome-devtools-mcp/build/src/", import.meta.url);

// Each file's replacements, before and after, each before found exactly once.
const PATCHES = {
  // The bundled Puppeteer: no session sends Runtime.enable, so no Runtime.executionContextCreated says what a frame's
  // contexts are, and its two worlds make their own; a worker gets none.
  "third_party/index.js": [
    [`const UTILITY_WORLD_NAME = '__puppeteer_utility_world__' + packageVersion;
`, `const UTILITY_WORLD_NAME = '__puppeteer_utility_world__' + packageVersion;
// browserd: BROWSERD_NO_RUNTIME_ENABLE, as browser/tabs/patch_devtools.mjs says. Without Runtime events no console
// messages or uncaught errors are heard, and nothing can be evaluated in a worker.
const NO_RUNTIME_ENABLE = process.env.BROWSERD_NO_RUNTIME_ENABLE === '1';
`],
    [`        this.#client.send('Runtime.enable').catch((err) => {
            return this.#logger?.(DEBUG_PREFIXES$1.error)?.(err);
        });
`, `        if (!NO_RUNTIME_ENABLE) {
            this.#client.send('Runtime.enable').catch((err) => {
                return this.#logger?.(DEBUG_PREFIXES$1.error)?.(err);
            });
        }
`],
    [`                client.send('Runtime.enable').then(() => {
                    return this.#createIsolatedWorld(client, UTILITY_WORLD_NAME);
                }),
`, `                (NO_RUNTIME_ENABLE ? Promise.resolve() : client.send('Runtime.enable')).then(() => {
                    return this.#createIsolatedWorld(client, UTILITY_WORLD_NAME);
                }),
`],
    [`    async #waitForExecutionContext() {
        const error = new Error('Execution context was destroyed');
`, `    async #waitForExecutionContext() {
        if (this.#makesContext()) {
            return await this.#makeContext();
        }
        const error = new Error('Execution context was destroyed');
`],
    [`    #onContextDisposed() {
        this.#context = undefined;
        if ('clearDocumentHandle' in this.#frameOrWorker) {
            this.#frameOrWorker.clearDocumentHandle();
        }
    }
`, `    #onContextDisposed() {
        this.#context = undefined;
        if ('clearDocumentHandle' in this.#frameOrWorker) {
            this.#frameOrWorker.clearDocumentHandle();
        }
    }
    #making;
    #makesContext() {
        return NO_RUNTIME_ENABLE && !(this.#frameOrWorker instanceof CdpWebWorker) &&
            (this.#worldId === MAIN_WORLD || this.#worldId === PUPPETEER_WORLD);
    }
    // browserd: a frame's context without Runtime.enable. The utility world's context id comes back from
    // Page.createIsolatedWorld; the main world's is read from the object id of the frame's document as the main world
    // sees it, which DOM.resolveNode gives when asked for no context, and which V8 writes as
    // <isolate>.<context>.<object>.
    #makeContext() {
        this.#making ??= (async () => {
            const client = this.client;
            const isolated = await client.send('Page.createIsolatedWorld', {
                frameId: this.#frameOrWorker._id,
                worldName: UTILITY_WORLD_NAME,
                grantUniveralAccess: true,
            });
            let id = isolated.executionContextId;
            if (this.#worldId === MAIN_WORLD) {
                const { result } = await client.send('Runtime.evaluate', { expression: 'document', contextId: id });
                const { node } = await client.send('DOM.describeNode', { objectId: result.objectId });
                const { object } = await client.send('DOM.resolveNode', { backendNodeId: node.backendNodeId });
                await Promise.all([result, object].map(found => {
                    return client.send('Runtime.releaseObject', { objectId: found.objectId });
                }));
                id = Number(object.objectId.split('.')[1]);
                if (!Number.isInteger(id)) {
                    throw new Error(\`browserd: cannot read the main world's context from object id \${object.objectId}\`);
                }
            }
            const context = new ExecutionContext$1(client, this.#worldId === PUPPETEER_WORLD ? { id, name: UTILITY_WORLD_NAME } : { id }, this, this.#logger);
            this.setContext(context);
            return context;
        })().finally(() => {
            this.#making = undefined;
        });
        return this.#making;
    }
    // browserd: without Runtime.executionContextDestroyed nothing says a document's contexts are gone, so the frame
    // calls this as it navigates or changes session. A world in use gets its new context at once, so a waitFor running
    // across the navigation goes on.
    clearContext() {
        if (!this.#makesContext() || !this.#context) {
            return;
        }
        this.#context[disposeSymbol]();
        void this.#makeContext().catch(error => {
            this.#logger?.(DEBUG_PREFIXES$1.error)?.(error);
        });
    }
`],
    [`        updateClient(client) {
            this.#client = client;
        }
        page() {
`, `        updateClient(client) {
            this.#client = client;
            this.worlds[MAIN_WORLD].clearContext();
            this.worlds[PUPPETEER_WORLD].clearContext();
        }
        page() {
`],
    [`        _navigated(framePayload) {
            this._name = framePayload.name;
            this.#url = \`\${framePayload.url}\${framePayload.urlFragment || ''}\`;
        }
`, `        _navigated(framePayload) {
            this._name = framePayload.name;
            this.#url = \`\${framePayload.url}\${framePayload.urlFragment || ''}\`;
            this.worlds[MAIN_WORLD].clearContext();
            this.worlds[PUPPETEER_WORLD].clearContext();
        }
`],
  ],
  // chrome-devtools-mcp's DevTools universe for each page (#devtoolsUniverse), whose RuntimeModel sends
  // Runtime.enable: none, so console messages and issues are not formatted from it.
  "McpPage.js": [
    [`    async #initDevToolsUniverseNoThrow() {
        if (this.#devtoolsUniverse) {
`, `    async #initDevToolsUniverseNoThrow() {
        // browserd: BROWSERD_NO_RUNTIME_ENABLE, as browser/tabs/patch_devtools.mjs says.
        if (this.#devtoolsUniverse || process.env.BROWSERD_NO_RUNTIME_ENABLE === '1') {
`],
  ],
};

for (const [file, patches] of Object.entries(PATCHES)) {
  const path = new URL(file, BUILD);
  let text = readFileSync(path, "utf8");
  if (patches.every(([, after]) => text.includes(after))) continue;
  for (const [before, after] of patches) {
    const found = text.split(before).length - 1;
    if (found !== 1) {
      console.error("browserd: chrome-devtools-mcp's %s holds %d copies of the text browser/tabs/patch_devtools.mjs " +
                    "replaces, not 1, so it is not the version that patch was written for:\n%s", file, found, before);
      process.exit(1);
    }
    text = text.replace(before, () => after);
  }
  writeFileSync(path, text);
}
