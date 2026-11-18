// Exercise the handoff's URL boundary and async UI without opening an external
// application, contacting a printer, or requiring a browser/GPU.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const source = fs.readFileSync(new URL('../app/static/app.js', import.meta.url), 'utf8')
  .replace(/^import .*?;\n/, '')
  .replace(/hydrateIcons\(\);\s*bindEvents\(\);\s*bootstrap\(\);\s*$/, '');
const html = fs.readFileSync(new URL('../app/static/index.html', import.meta.url), 'utf8');
for (const [, id] of source.matchAll(/\$\('([A-Za-z][A-Za-z0-9_-]*)'\)/g)) {
  assert.ok(html.includes(`id="${id}"`), `Missing DOM hook: ${id}`);
}

class Element {
  constructor() { this.hidden = false; this.open = false; this.value = ''; this.children = []; this.dataset = {}; this.attributes = {}; }
  append(...values) { this.children.push(...values); }
  replaceChildren(...values) { this.children = values; }
  setAttribute(name, value) { this.attributes[name] = value; }
  removeAttribute(name) { delete this[name]; delete this.attributes[name]; }
  showModal() { this.open = true; }
  focus() { this.focused = true; }
  reportValidity() { return true; }
}
const elements = new Map();
function element(id) {
  if (!elements.has(id)) elements.set(id, new Element());
  return elements.get(id);
}
let requests = [], handler = async () => ({});
const location = {origin: 'http://10.20.30.200:8782'};
Object.defineProperty(location, 'href', {set() { throw new Error('Preparation must not navigate automatically.'); }});
const context = vm.createContext({
  URL, console, setTimeout, clearTimeout,
  window: {location},
  navigator: {platform: 'Win32', userAgent: 'Desktop test', maxTouchPoints: 0},
  document: {getElementById: element, createElement: () => new Element(), createTextNode: text => ({textContent: text})},
  testApi: async (path, options = {}) => { requests.push({path, ...options}); return handler(path, options); },
});
vm.runInContext(source + '\napi = testApi;', context);
const run = code => vm.runInContext(code, context);
let checks = 0;

for (const [platform, ua, touch, expected] of [
  ['Win32', 'Windows', 0, 'desktop'], ['Linux x86_64', 'Linux', 0, 'desktop'],
  ['macOS', 'Macintosh', 0, 'mac'], ['MacIntel', 'Macintosh', 5, 'mobile'],
  ['Linux', 'Android', 5, 'mobile'], ['iPhone', 'iPhone', 5, 'mobile'], ['iPad', 'iPad', 5, 'mobile'],
]) {
  assert.equal(context.studioDevice(platform, ua, touch), expected); checks++;
}
const origin = location.origin;
const download = context.studioDownloadURL('/api/models/example/studio/model.3mf', origin);
assert.equal(download, `${origin}/api/models/example/studio/model.3mf`);
assert.equal(context.studioDownloadURL('/api/jobs/example/studio/plate.gcode.3mf', origin), `${origin}/api/jobs/example/studio/plate.gcode.3mf`);
assert.equal(context.studioProtocolURL(download, 'desktop'), `bambustudio://open?file=${encodeURIComponent(download)}`);
assert.equal(context.studioProtocolURL(download, 'mac'), `bambustudioopen://${encodeURIComponent(download)}`);
assert.throws(() => context.studioProtocolURL(download, 'mobile'), /computer/); checks++;
for (const invalid of [
  '', undefined, 'https://example.com/model.3mf', '//evil.test/model.3mf',
  'https://10.20.30.200:8782/model.3mf', 'http://10.20.30.200:8783/model.3mf',
  'http://user:password@10.20.30.200:8782/model.3mf', 'javascript:alert(1)',
  'file:///model.3mf', '/api/models/example/file', '/model.stl',
  '/model.3mf?next=elsewhere', '/model.3mf#fragment',
]) { assert.throws(() => context.studioDownloadURL(invalid, origin)); checks++; }

// No deep link or navigation while an asynchronous conversion is outstanding.
let finish;
handler = () => new Promise(resolve => { finish = resolve; });
const pending = run("prepareStudio({id:'model-1',filename:'pot.obj'})");
assert.equal(element('studioModal').open, true);
assert.equal(element('studioLaunchLink').hidden, true);
assert.equal(element('studioLaunchLink').href, undefined);
assert.equal(element('studioDownloadLink').href, '/api/models/model-1/file');
finish({download_url:'/api/models/model-1/studio/pot.3mf',filename:'pot.3mf',converted:true});
await pending;
assert.equal(element('studioLaunchLink').hidden, false);
assert.equal(element('studioLaunchLink').href, `bambustudio://open?file=${encodeURIComponent(`${origin}/api/models/model-1/studio/pot.3mf`)}`);
assert.match(element('studioStatus').textContent, /original file is unchanged/);
assert.equal(element('studioPreparing').hidden, true); checks++;

// Closing a dialog invalidates an in-flight preparation, including its href.
const stale = run("prepareStudio({id:'model-2',filename:'old.stl'})");
run('state.studioLoadId++'); element('studioModal').open = false;
finish({download_url:'/api/models/model-2/studio/old.3mf',filename:'old.3mf'});
await stale;
assert.equal(element('studioLaunchLink').href, undefined);
assert.equal(element('studioLaunchLink').hidden, true); checks++;

// Failed preparation keeps the download route usable and never shows a link.
handler = async () => { throw new Error('Conversion unavailable'); };
await run("prepareStudio({id:'model-3',filename:'still-downloadable.stl'})");
assert.equal(element('studioDownloadLink').href, '/api/models/model-3/file');
assert.equal(element('studioLaunchLink').hidden, true);
assert.equal(element('studioError').textContent, 'Conversion unavailable'); checks++;

// Mobile never invokes a desktop protocol or performs unnecessary conversion.
context.navigator = {platform:'iPhone',userAgent:'iPhone',maxTouchPoints:5};
requests = [];
await run("prepareStudio({id:'phone',filename:'model.stl'})");
assert.equal(requests.length, 0);
assert.equal(element('studioLaunchLink').hidden, true);
assert.match(element('studioStatus').textContent, /Windows, Mac or Linux computer/);
assert.equal(element('studioDownloadLink').href, '/api/models/phone/file'); checks++;

// Sliced jobs use the dedicated endpoint only for a recognised P2S profile.
context.navigator = {platform:'MacIntel',userAgent:'Macintosh',maxTouchPoints:0};
handler = async () => ({download_url:'/api/jobs/job-1/studio/plate.gcode.3mf',filename:'plate.gcode.3mf',converted:false});
requests = [];
await run("prepareStudio({id:'job-1',filename:'plate.gcode.3mf',compatible:true}, 'job')");
assert.equal(requests[0].path, '/api/jobs/job-1/studio');
assert.match(element('studioLaunchLink').href, /^bambustudioopen:\/\//);
assert.match(element('studioInstructions').textContent, /already sliced/);
requests = [];
await run("prepareStudio({id:'job-2',filename:'other.gcode.3mf',compatible:false}, 'job')");
assert.equal(requests.length, 0); checks++;

// Cloud mode ignores retained LAN verification and saves no access credentials.
run("state.printer = {mode:'cloud',configured:true,verified:true,lan_verified:true,access_code_saved:true}; state.model=null;");
assert.equal(run('printerVerified()'), false);
element('printerMode').value = 'cloud'; element('printerName').value = 'My P2S';
element('printerHost').value = '10.20.30.99'; element('printerCode').value = 'keep-private';
run('updatePrinterModeFields()');
assert.equal(element('printerLanFields').hidden, true);
assert.equal(element('printerLanFields').disabled, true);
assert.equal(element('printerHost').required, false);
assert.equal(element('printerCode').required, false);
assert.equal(element('testPrinterButton').hidden, true);
requests = [];
handler = async () => ({mode:'cloud',name:'My P2S',configured:false,verified:false,access_code_saved:true});
assert.equal(await run('savePrinter({quiet:true})'), true);
assert.deepEqual(JSON.parse(JSON.stringify(requests[0].body)), {mode:'cloud',name:'My P2S'});
requests = [];
await run('refreshPrinterStatus()');
assert.equal(requests.length, 0);
assert.equal(element('printerStatusContent').children[1].textContent, 'Managed in Bambu Studio'); checks++;

// Selecting LAN restores required fields without making it the cloud default.
element('printerMode').value = 'lan';
run('state.printer.access_code_saved=false; updatePrinterModeFields()');
assert.equal(element('printerLanFields').hidden, false);
assert.equal(element('printerLanFields').disabled, false);
assert.equal(element('printerHost').required, true);
assert.equal(element('printerCode').required, true); checks++;

console.log(JSON.stringify({status:'passed',checks,includes:['origin + scheme validation','desktop platform schemes','explicit launch after preparation','stale-response cancellation','error/mobile downloads','compatible sliced jobs','cloud-only payload + status','optional LAN fields','DOM hooks']}));
