// Geometry and asset-integration checks; no browser or GPU is required.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import { Box3, Vector3 } from 'three';
import { OBJLoader } from 'three/addons/loaders/OBJLoader.js';
import { prepareOBJPreview } from '../app/frontend/viewer.js';
import { prepareOBJPreview as bundledPrepare } from '../app/static/viewer.bundle.js';

const vertices = ['0 0 0', '3 0 0', '3 1 0', '1 1 0', '1 3 0', '0 3 0'];
const source = vertices.map(v => `v ${v}`).join('\n');
function geometry(text, prepare = prepareOBJPreview) {
  const object = new OBJLoader().parse(prepare(text));
  let area = 0, triangles = 0;
  object.traverse(part => {
    if (!part.isMesh) return;
    const attr = part.geometry.attributes.position;
    for (let i = 0; i < attr.count; i += 3) {
      const a = new Vector3().fromBufferAttribute(attr, i);
      const b = new Vector3().fromBufferAttribute(attr, i + 1);
      const c = new Vector3().fromBufferAttribute(attr, i + 2);
      area += b.sub(a).cross(c.sub(a)).length() / 2;
      triangles++;
    }
  });
  return { area, triangles, bounds: new Box3().setFromObject(object).getSize(new Vector3()).toArray() };
}
let checks = 0;
for (const face of ['1 2 3 4 5 6', '6 5 4 3 2 1', '-6 -5 -4 -3 -2 -1', '1 2 3 4 5 6 1']) {
  const text = `${source}\nf ${face}\n`;
  const result = geometry(text);
  assert.equal(result.area, 5);
  assert.equal(result.triangles, 4);
  assert.deepEqual(result.bounds, [3, 3, 0]);
  assert.deepEqual(geometry(text, bundledPrepare), result);
  checks++;
}
const withAttributes = `${source}\n${vertices.map(() => 'vt 0 0').join('\n')}\nvn 0 0 1\nf 1/1/1 2/2/1 3/3/1 4/4/1 5/5/1 6/6/1\n`;
assert.equal(geometry(withAttributes).area, 5); checks++;
const comments = '\ufeff# triangle\r\nv 0 0 0 2 # ignored weight\r\nv 3 0 0 0.2 0.3 0.4\r\nv 0 2 0\r\nv 999 999 999\r\nf 1 2 \\\r\n3 # continued face\r\nl 1 4\r\np 4\r\n';
assert.deepEqual(geometry(comments).bounds, [3, 2, 0]);
assert.equal(geometry(comments).area, 3); checks++;
const many = Array.from({length: 257}, (_, i) => `v ${Math.cos(i)} ${Math.sin(i)} 0`).join('\n');
assert.throws(() => prepareOBJPreview(`${many}\nf ${Array.from({length:257},(_,i)=>i+1).join(' ')}\n`), /too many corners/); checks++;
const html = fs.readFileSync(new URL('../app/static/index.html', import.meta.url), 'utf8');
const app = fs.readFileSync(new URL('../app/static/app.js', import.meta.url), 'utf8');
for (const [, id] of app.matchAll(/\$\('([A-Za-z][A-Za-z0-9_-]*)'\)/g)) {
  assert.ok(html.includes(`id="${id}"`), `Missing DOM id: ${id}`);
}
assert.ok(html.includes('accept=".stl,.obj,.3mf"'));
assert.ok(html.includes('data-kind="obj"'));
assert.ok(html.includes('data-site-backup="layer-library"'));
assert.ok(!html.includes('id="loginScreen"'));
checks++;
console.log(JSON.stringify({status: 'passed', checks, includes: ['source + built-bundle OBJ geometry', 'concave polygons', 'negative/slashed references', 'comments/continuations', 'units/weights', 'nonmesh primitives', 'DOM hooks', 'anonymous access and backup UI']}));
