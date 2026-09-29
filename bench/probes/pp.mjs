// CHROME_PORT=<the Bench Chrome's port> node bench/probes/pp.mjs
import { puppeteer } from '../../node_modules/chrome-devtools-mcp/build/src/third_party/index.js';
const browser = await puppeteer.connect({ browserURL: 'http://127.0.0.1:' + process.env.CHROME_PORT, defaultViewport: null, handleDevToolsAsPage: true, logger: p => (...a) => console.log(String(p), ...a.map(x => typeof x === 'string' ? x.slice(0, 300) : x)),
  targetFilter: t => { const u = t.url(); if (!u) return true; return !u.startsWith('chrome:'); } });
for (const t of browser.targets()) console.log('target', t.type(), t.url(), t._subtype?.() ?? '', t._getTargetInfo?.().subtype ?? '');
const pages = await browser.pages();
console.log('pages', pages.length, pages.map(p => p.url()));
await browser.disconnect();
