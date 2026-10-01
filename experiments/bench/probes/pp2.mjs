// CHROME_PORT=<the Bench Chrome's port> node experiments/bench/probes/pp2.mjs
import { puppeteer } from '../../../node_modules/chrome-devtools-mcp/build/src/third_party/index.js';
const wait = Number(process.argv[2] || 0);
const browser = await puppeteer.connect({ browserURL: 'http://127.0.0.1:' + process.env.CHROME_PORT, defaultViewport: null, handleDevToolsAsPage: true,
  targetFilter: t => { const u = t.url(); if (!u) return true; return !u.startsWith('chrome:'); } });
const show = async label => console.log(label, 'targets', browser.targets().map(t => t.type() + ' ' + t.url().slice(0, 50)), 'pages', (await browser.pages()).map(p => p.url().slice(0, 50)));
await show('at connect');
await new Promise(r => setTimeout(r, wait));
await show('after ' + wait + 'ms');
const all = [...browser._targetManager().getAvailableTargets().values()].map(t => [t.type(), t._subtype?.(), t.url().slice(0, 50), t._initializedDeferred?.value?.()]);
console.log('all attached', all);
await browser.disconnect();
