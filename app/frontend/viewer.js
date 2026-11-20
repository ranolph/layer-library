import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { STLLoader } from 'three/addons/loaders/STLLoader.js';
import { ThreeMFLoader } from 'three/addons/loaders/3MFLoader.js';
import { OBJLoader } from 'three/addons/loaders/OBJLoader.js';

/** Triangulate concave OBJ polygons before OBJLoader's triangle-fan fallback.
 * The uploaded file remains untouched. Only the in-memory preview is changed.
 * Materials and texture filenames are never resolved or fetched.
 */
export function prepareOBJPreview(text) {
  text = text.replace(/\\[ \t]*\r?\n/g, ' ').replace(/\r/g, '').replace(/^\uFEFF/, '');
  const lines = text.split('\n').map(line => line.split('#', 1)[0].trim());
  // Triangular OBJs do not need an extra vertex buffer. Strip drafting-only
  // primitives so they do not alter the printable mesh's camera bounds.
  if (!lines.some(line => /^f(?:\s+\S+){4}/.test(line))) {
    return lines.filter(line => !/^[lp]\s/.test(line)).join('\n');
  }
  const vertexCount = lines.reduce((n, line) => n + Number(/^\s*v\s/.test(line)), 0);
  if (vertexCount > 8_000_000) throw new Error('This OBJ is too large for an interactive preview.');
  const vertices = new Float64Array(vertexCount * 3);
  const output = [];
  let count = 0;
  for (const raw of lines) {
    const line = raw;
    const fields = line.split(/\s+/);
    if (fields[0] === 'v') {
      vertices[count * 3] = Number(fields[1]);
      vertices[count * 3 + 1] = Number(fields[2]);
      vertices[count * 3 + 2] = Number(fields[3]);
      count++;
      // Polygon XYZ coordinates are unchanged by the optional rational weight.
      // Ignore optional vertex colours to use the library's neutral material.
      output.push(`v ${fields[1]} ${fields[2]} ${fields[3]}`);
    } else if (fields[0] === 'f') {
      const references = fields.slice(1);
      if (references.length > 256) throw new Error('An OBJ face has too many corners to preview.');
      const corners = references.map(reference => {
        const value = Number(reference.split('/', 1)[0]);
        const index = value < 0 ? count + value : value - 1;
        if (!Number.isInteger(index) || index < 0 || index >= count) throw new Error('The OBJ contains an invalid face reference.');
        return { reference, point: new THREE.Vector3().fromArray(vertices, index * 3) };
      }).filter((corner, index, values) => !index || !corner.point.equals(values[index - 1].point));
      if (corners.length > 1 && corners[0].point.equals(corners[corners.length - 1].point)) corners.pop();
      if (corners.length < 3) throw new Error('The OBJ contains a face without three distinct corners.');
      if (corners.length === 3) {
        output.push(`f ${corners.map(corner => corner.reference).join(' ')}`);
        continue;
      }
      const origin = corners[0].point;
      const points = corners.map(corner => corner.point.clone().sub(origin));
      const normal = new THREE.Vector3();
      for (let i = 0; i < points.length; i++) {
        const a = points[i], b = points[(i + 1) % points.length];
        normal.x += (a.y - b.y) * (a.z + b.z);
        normal.y += (a.z - b.z) * (a.x + b.x);
        normal.z += (a.x - b.x) * (a.y + b.y);
      }
      const magnitude = [Math.abs(normal.x), Math.abs(normal.y), Math.abs(normal.z)];
      const omit = magnitude.indexOf(Math.max(...magnitude));
      const projected = points.map(point => omit === 0 ? new THREE.Vector2(point.y, point.z) : omit === 1 ? new THREE.Vector2(point.x, point.z) : new THREE.Vector2(point.x, point.y));
      const triangles = THREE.ShapeUtils.triangulateShape(projected, []);
      if (!triangles.length) throw new Error('An OBJ polygon could not be triangulated for the preview.');
      for (const triangle of triangles) output.push(`f ${triangle.map(index => corners[index].reference).join(' ')}`);
    } else if (fields[0] === 'l' || fields[0] === 'p') {
      // The printable mesh excludes drafting lines and standalone points.
      continue;
    } else {
      output.push(line);
    }
  }
  return output.join('\n');
}

/** A self-contained, millimetre-scale viewer for library STL, OBJ and 3MF files. */
export class ModelViewer {
  constructor(container, { onStatus = () => {} } = {}) {
    this.container = container;
    this.onStatus = onStatus;
    this.disposed = false;
    this.object = null;
    this.color = '#c2aa82';
    this.wireframe = false;
    this.showGrid = true;
    this.loadId = 0;
    this.controller = null;
    this.scene = new THREE.Scene();
    this.scene.background = new THREE.Color('#eee7f5');
    this.camera = new THREE.PerspectiveCamera(40, 1, 0.01, 10000);
    this.renderer = new THREE.WebGLRenderer({ antialias: true, preserveDrawingBuffer: true });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    this.renderer.outputColorSpace = THREE.SRGBColorSpace;
    this.renderer.toneMapping = THREE.ACESFilmicToneMapping;
    this.renderer.toneMappingExposure = 1.25;
    this.renderer.setClearColor('#eee7f5', 1);
    this.renderer.domElement.setAttribute('aria-label', 'Interactive 3D model. Drag to rotate, right-drag to pan, scroll to zoom.');
    this.renderer.domElement.setAttribute('role', 'img');
    this.renderer.domElement.tabIndex = 0;
    container.replaceChildren(this.renderer.domElement);
    this.controls = new OrbitControls(this.camera, this.renderer.domElement);
    this.controls.enableDamping = true;
    this.controls.dampingFactor = 0.085;
    this.controls.screenSpacePanning = true;
    this.controls.addEventListener('change', () => this.render());
    this.scene.add(new THREE.HemisphereLight(0xffffff, 0xaaaaaa, 2.1));
    const key = new THREE.DirectionalLight(0xffffff, 2.7);
    key.position.set(-400, 700, 500);
    this.scene.add(key);
    const fill = new THREE.DirectionalLight(0xe9f2ff, 1.0);
    fill.position.set(500, 250, -300);
    this.scene.add(fill);
    this.grid = null;
    this.resizeObserver = new ResizeObserver(() => this.resize());
    this.resizeObserver.observe(container);
    this.animation = () => {
      if (this.disposed) return;
      this.frame = requestAnimationFrame(this.animation);
      this.controls.update();
    };
    this.animation();
    this.resize();
  }

  async load(url, { kind = 'stl' } = {}) {
    const loadId = ++this.loadId;
    this.controller?.abort();
    this.controller = new AbortController();
    this.onStatus('loading');
    try {
      const response = await fetch(url, { credentials: 'same-origin', signal: this.controller.signal });
      if (!response.ok) throw new Error(`The preview file could not be loaded (${response.status}).`);
      const format = String(kind).toLowerCase();
      const buffer = format === 'obj' ? await response.text() : await response.arrayBuffer();
      if (this.disposed || loadId !== this.loadId) return null;
      let object;
      if (format === '3mf') {
        object = new ThreeMFLoader().parse(buffer);
      } else if (format === 'obj') {
        object = new OBJLoader().parse(prepareOBJPreview(buffer));
      } else {
        const geometry = new STLLoader().parse(buffer);
        geometry.computeVertexNormals();
        object = new THREE.Mesh(geometry, this.makeMaterial());
      }
      const sourceBox = new THREE.Box3().setFromObject(object);
      if (sourceBox.isEmpty()) throw new Error('This file does not contain a visible mesh.');
      const sourceSize = sourceBox.getSize(new THREE.Vector3());
      if (![sourceSize.x, sourceSize.y, sourceSize.z].every(Number.isFinite)) {
        throw new Error('The model contains invalid coordinates.');
      }
      this.clearObject();
      object.traverse(child => {
        if (child.isMesh) {
          const old = Array.isArray(child.material) ? child.material : [child.material];
          old.forEach(material => material?.dispose());
          child.material = this.makeMaterial();
          child.frustumCulled = false;
        }
      });
      // Printing files use Z-up. Three.js navigation and its floor grid use Y-up.
      const wrapper = new THREE.Group();
      wrapper.add(object);
      object.rotation.x -= Math.PI / 2;
      object.updateMatrixWorld(true);
      const rotated = new THREE.Box3().setFromObject(wrapper);
      const center = rotated.getCenter(new THREE.Vector3());
      object.position.x -= center.x;
      object.position.y -= rotated.min.y;
      object.position.z -= center.z;
      wrapper.updateMatrixWorld(true);
      this.object = wrapper;
      this.scene.add(wrapper);
      this.bounds = new THREE.Box3().setFromObject(wrapper);
      this.dimensions = { x: sourceSize.x, y: sourceSize.y, z: sourceSize.z };
      this.createGrid();
      this.resetView();
      this.onStatus('ready');
      return this.dimensions;
    } catch (error) {
      if (error.name === 'AbortError' || this.disposed || loadId !== this.loadId) return null;
      this.onStatus('error', error.message);
      throw error;
    }
  }

  makeMaterial() {
    return new THREE.MeshStandardMaterial({
      color: this.color, metalness: 0.04, roughness: 0.66,
      wireframe: this.wireframe, side: THREE.DoubleSide,
    });
  }

  createGrid() {
    if (this.grid) {
      this.scene.remove(this.grid);
      this.grid.geometry.dispose();
      this.grid.material.dispose();
    }
    const size = this.bounds.getSize(new THREE.Vector3());
    const extent = Math.max(size.x, size.z, size.y * 0.7, 10);
    const gridSize = Math.ceil(extent * 1.8 / 10) * 10;
    this.grid = new THREE.GridHelper(gridSize, 20, 0xb0a3c0, 0xd3c9df);
    this.grid.position.y = -Math.max(extent / 2000, .02);
    this.grid.material.transparent = true;
    this.grid.material.opacity = .54;
    this.grid.visible = this.showGrid;
    this.scene.add(this.grid);
  }

  resize() {
    if (this.disposed) return;
    const width = Math.max(1, this.container.clientWidth);
    const height = Math.max(1, this.container.clientHeight);
    this.renderer.setSize(width, height, false);
    this.camera.aspect = width / height;
    this.camera.updateProjectionMatrix();
    this.render();
  }

  resetView() {
    if (!this.bounds) return;
    const size = this.bounds.getSize(new THREE.Vector3());
    const target = this.bounds.getCenter(new THREE.Vector3());
    const maxSize = Math.max(size.x, size.y, size.z, 1);
    const fitHeight = maxSize / (2 * Math.tan(THREE.MathUtils.degToRad(this.camera.fov / 2)));
    const distance = Math.max(fitHeight, fitHeight / this.camera.aspect) * 1.45;
    const direction = new THREE.Vector3(.85, .62, 1.1).normalize();
    this.camera.position.copy(target).addScaledVector(direction, distance);
    this.camera.near = Math.max(maxSize / 10000, .001);
    this.camera.far = Math.max(distance * 30, 1000);
    this.camera.updateProjectionMatrix();
    this.controls.target.copy(target);
    this.controls.minDistance = maxSize * .12;
    this.controls.maxDistance = distance * 12;
    this.controls.update();
    this.render();
  }

  setWireframe(value) {
    this.wireframe = Boolean(value);
    this.object?.traverse(child => { if (child.isMesh) child.material.wireframe = this.wireframe; });
    this.render();
  }

  setGridVisible(value) {
    this.showGrid = Boolean(value);
    if (this.grid) this.grid.visible = this.showGrid;
    this.render();
  }

  setColor(value) {
    this.color = value;
    this.object?.traverse(child => { if (child.isMesh) child.material.color.set(value); });
    this.render();
  }

  render() {
    if (!this.disposed) this.renderer.render(this.scene, this.camera);
  }

  async captureThumbnail(width = 640, height = 480) {
    if (!this.object || this.disposed) return null;
    if (this.renderer.getContext().isContextLost()) throw new Error('The 3D preview was interrupted. Rebuild its image from the model menu.');
    // Render at a fixed size, even if the modal has become hidden or its
    // container was resized while a large model was loading. Copy pixels before
    // restoring the visible canvas, so a tab change cannot save a blank image.
    const saved = {
      size: this.renderer.getSize(new THREE.Vector2()), pixelRatio: this.renderer.getPixelRatio(),
      aspect: this.camera.aspect, position: this.camera.position.clone(), target: this.controls.target.clone(),
      near: this.camera.near, far: this.camera.far, zoom: this.camera.zoom,
      minDistance: this.controls.minDistance, maxDistance: this.controls.maxDistance,
      grid: this.grid?.visible,
    };
    const canvas = document.createElement('canvas');
    canvas.width = width; canvas.height = height;
    const context = canvas.getContext('2d');
    if (!context) throw new Error('This browser could not capture a preview image. Use Rebuild preview image.');
    try {
      this.renderer.setPixelRatio(1);
      this.renderer.setSize(width, height, false);
      this.camera.aspect = width / height;
      this.camera.zoom = 1;
      if (this.grid) this.grid.visible = false;
      this.resetView();
      this.render();
      context.drawImage(this.renderer.domElement, 0, 0, width, height);
    } finally {
      this.renderer.setPixelRatio(saved.pixelRatio);
      this.renderer.setSize(saved.size.x, saved.size.y, false);
      this.camera.aspect = saved.aspect;
      this.camera.position.copy(saved.position);
      this.camera.near = saved.near; this.camera.far = saved.far; this.camera.zoom = saved.zoom;
      this.camera.updateProjectionMatrix();
      this.controls.target.copy(saved.target);
      this.controls.minDistance = saved.minDistance; this.controls.maxDistance = saved.maxDistance;
      this.controls.update();
      if (this.grid) this.grid.visible = saved.grid;
      this.render();
    }
    return new Promise(resolve => canvas.toBlob(resolve, 'image/png'));
  }

  clearObject() {
    if (!this.object) return;
    this.object.traverse(child => {
      if (child.isMesh) {
        child.geometry.dispose();
        const materials = Array.isArray(child.material) ? child.material : [child.material];
        materials.forEach(material => material?.dispose());
      }
    });
    this.scene.remove(this.object);
    this.object = null;
  }

  dispose() {
    this.disposed = true;
    this.controller?.abort();
    cancelAnimationFrame(this.frame);
    this.resizeObserver.disconnect();
    this.controls.dispose();
    this.clearObject();
    if (this.grid) { this.grid.geometry.dispose(); this.grid.material.dispose(); }
    this.renderer.dispose();
    this.renderer.forceContextLoss();
    this.renderer.domElement.remove();
  }
}
