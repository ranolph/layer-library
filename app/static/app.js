import { ModelViewer } from './viewer.bundle.js';

const $ = id => document.getElementById(id);
const MAX_FILE_BYTES = 250 * 1024 * 1024;
const ICONS = {
  grid:'<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>',
  box:'<path d="m12 3 9 5v9l-9 5-9-5V8Z"/><path d="m3 8 9 5 9-5M12 13v9M7.5 5.5l9 5"/>',
  layers:'<path d="m12 3 10 5-10 5L2 8Z"/><path d="m2 12 10 5 10-5M2 16l10 5 10-5"/>',
  printer:'<path d="M6 8V3h12v5M6 17H4a2 2 0 0 1-2-2v-5a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v5a2 2 0 0 1-2 2h-2"/><path d="M6 14h12v7H6Z"/><path d="M18 11h1"/>',
  trash:'<path d="M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7M14 10v7"/>',
  restore:'<path d="M3 8v5h5"/><path d="M3.7 12a8.3 8.3 0 1 1 1.8 6M12 7v5l3 2"/>',
  upload:'<path d="M12 16V3m-5 5 5-5 5 5M4 15v5h16v-5"/>',
  download:'<path d="M12 3v13m-5-5 5 5 5-5M4 16v5h16v-5"/>',
  plus:'<path d="M12 5v14M5 12h14"/>',
  x:'<path d="m6 6 12 12M18 6 6 18"/>',
  check:'<path d="m5 12 4 4L19 6"/>',
  search:'<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5"/>',
  'chevron-right':'<path d="m9 5 7 7-7 7"/>',
  'arrow-right':'<path d="M4 12h16m-6-6 6 6-6 6"/>',
  refresh:'<path d="M20 4v6h-6M4 20v-6h6"/><path d="M5.1 8a8 8 0 0 1 13.2-3L20 10M4 14l1.7 5A8 8 0 0 0 19 16"/>',
  settings:'<path d="m10 3-.5 2.5-2 .9L5 5.5 3 9l2 1.7v2.6L3 15l2 3.5 2.5-.9 2 .9L10 21h4l.5-2.5 2-.9 2.5.9 2-3.5-2-1.7v-2.6L21 9l-2-3.5-2.5.9-2-.9L14 3Z"/><circle cx="12" cy="12" r="3"/>',
  storage:'<ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v14c0 1.7 3.6 3 8 3s8-1.3 8-3V5M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3"/>',
  folder:'<path d="M3 6a2 2 0 0 1 2-2h5l2 3h7a2 2 0 0 1 2 2v10H3Z"/>',
  sort:'<path d="M8 4v16m-4-4 4 4 4-4M14 4h6M14 9h4M14 14h2"/>',
  menu:'<path d="M4 6h16M4 12h16M4 18h16"/>',
  eye:'<path d="M2 12s3-7 10-7 10 7 10 7-3 7-10 7S2 12 2 12Z"/><circle cx="12" cy="12" r="3"/>',
  edit:'<path d="m15 4 5 5M4 20l5-1L21 7l-5-5L4 14Z"/>',
  info:'<circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7v.3"/>',
  alert:'<path d="M12 3 2 21h20Z"/><path d="M12 9v5M12 17v.3"/>',
  connection:'<path d="M5 8a10 10 0 0 1 14 0M8 11a6 6 0 0 1 8 0M11 14a2 2 0 0 1 2 0"/><circle cx="12" cy="18" r="1"/>',
  rotate:'<path d="M3 3v6h6"/><path d="M3 9a9 9 0 1 1 0 7"/>',
  wireframe:'<path d="m12 2 10 6v8l-10 6-10-6V8Z"/><path d="m2 8 10 14L22 8 2 16 12 2l10 14L2 8M12 2v20"/>',
  file:'<path d="M5 2h9l5 5v15H5Z"/><path d="M14 2v6h5M8 13h8M8 17h6"/>',
  lock:'<rect x="5" y="10" width="14" height="11" rx="2"/><path d="M8 10V6a4 4 0 0 1 8 0v4M12 14v3"/>',
  send:'<path d="m22 2-7 20-4-9-9-4Z"/><path d="M22 2 11 13"/>',
  star:'<path d="m12 3 2.8 5.7 6.3.9-4.55 4.45 1.07 6.28L12 17.36l-5.62 2.97 1.07-6.28L2.9 9.6l6.3-.9Z"/>',
};

function icon(name) {
  const element = document.createElement('i');
  element.dataset.icon = name;
  element.setAttribute('aria-hidden', 'true');
  element.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round">${ICONS[name] || ICONS.box}</svg>`;
  return element;
}
function hydrateIcons(root = document) {
  root.querySelectorAll('i[data-icon]').forEach(element => element.replaceWith(icon(element.dataset.icon)));
}
function node(tag, className = '', text = '') {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== '') element.textContent = text;
  return element;
}
function button(text, className, handler, iconName) {
  const element = node('button', className);
  element.type = 'button';
  if (iconName) element.append(icon(iconName));
  if (text) element.append(document.createTextNode(text));
  if (handler) element.addEventListener('click', handler);
  return element;
}
function setError(id, message = '') {
  $(id).textContent = message;
  $(id).hidden = !message;
}
function setBusy(element, busy, label) {
  if (busy) {
    element.dataset.originalHtml = element.innerHTML;
    element.replaceChildren(node('span', 'spinner'), document.createTextNode(label ?? 'Working…'));
    element.disabled = true;
  } else {
    if (element.dataset.originalHtml) element.innerHTML = element.dataset.originalHtml;
    delete element.dataset.originalHtml;
    element.disabled = false;
  }
}
function humanSize(bytes = 0) {
  const value = Number(bytes) || 0;
  if (value < 1024) return `${value} B`;
  const powers = ['KiB', 'MiB', 'GiB', 'TiB'];
  const index = Math.min(Math.floor(Math.log(value) / Math.log(1024)) - 1, powers.length - 1);
  const scaled = value / 1024 ** (index + 1);
  return `${scaled.toFixed(scaled >= 100 ? 0 : scaled >= 10 ? 1 : 2).replace(/\.0+$/, '')} ${powers[index]}`;
}
function humanDate(value, full = false) {
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) return '—';
  return new Intl.DateTimeFormat(undefined, full ? { day:'numeric', month:'short', year:'numeric' } : { day:'numeric', month:'short' }).format(date);
}
function number(value, digits = 1) {
  return value != null && Number.isFinite(Number(value)) ? Number(value).toFixed(digits).replace(/\.0$/, '') : '—';
}
function dimensions(model) {
  const d = model?.dimensions;
  if (!d) return null;
  const result = Array.isArray(d) ? { x:d[0], y:d[1], z:d[2] } : d;
  return ['x','y','z'].every(key => result[key] != null && Number.isFinite(Number(result[key]))) ? result : null;
}
function fileKind(model) { return String(model.kind || model.filename?.split('.').pop() || 'stl').toLowerCase(); }
function modelPath(id, suffix = '') { return `/api/models/${encodeURIComponent(id)}${suffix}`; }
function jobPath(id, suffix = '') { return `/api/jobs/${encodeURIComponent(id)}${suffix}`; }

// Pure helpers for the desktop handoff. Protocol links are set only on an
// explicit second-click anchor after the server has finished preparing a file.
function studioDevice(platform = '', userAgent = '', maxTouchPoints = 0) {
  if (/Android|iPhone|iPad|iPod/i.test(userAgent) || (/Mac/i.test(platform) && maxTouchPoints > 1)) return 'mobile';
  return /Mac/i.test(platform) || /Macintosh/i.test(userAgent) ? 'mac' : 'desktop';
}
function studioDownloadURL(downloadPath, origin) {
  if (typeof downloadPath !== 'string' || !downloadPath.trim()) throw new Error('The server did not provide a Bambu Studio download. You can still download the original file.');
  const base = new URL(origin), url = new URL(downloadPath, base.origin);
  if (!['http:', 'https:'].includes(base.protocol) || url.protocol !== base.protocol || url.origin !== base.origin || url.username || url.password || url.search || url.hash || !/\.3mf$/i.test(url.pathname)) {
    throw new Error('The Bambu Studio download must be a 3MF file from this library. Download the original file instead.');
  }
  return url.href;
}
function studioProtocolURL(downloadURL, device) {
  if (device === 'mobile') throw new Error('Bambu Studio requires a Windows, Mac or Linux computer.');
  return `${device === 'mac' ? 'bambustudioopen://' : 'bambustudio://open?file='}${encodeURIComponent(downloadURL)}`;
}

function errorMessage(data, fallback = 'Something went wrong. Please try again.') {
  const detail = data?.detail ?? data?.message ?? data?.error ?? data;
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) return detail.map(value => typeof value === 'string' ? value : value.msg || value.message || 'Invalid value').join('\n');
  if (detail && typeof detail === 'object') return detail.message || detail.error || fallback;
  return fallback;
}

const state = {
  ready:false, csrf:'', page:'library', view:'featured', library:{ items:[], collections:[], tags:[], stats:{} }, trash:[],
  collection:'', tag:'', kind:'all', search:'', sort:'newest', loading:false, loadId:0,
  printer:{ mode:'cloud', configured:false, verified:false }, model:null, jobs:[], tab:'preview', viewer:null, previewOptInId:null,
  modelLoadId:0, uploadFiles:[], uploadBusy:false, jobUploadBusy:false, transfers:new Map(), statusTimer:null,
  thumbnailFailures:new Set(), thumbnailRebuilds:new Map(), studioLoadId:0,
  featuredPending:new Map(), featuredVersion:0, featuredChanges:new Map(),
};

async function api(path, { method = 'GET', body, headers = {} } = {}) {
  const requestHeaders = { Accept:'application/json', ...headers };
  const mutation = !['GET','HEAD'].includes(method.toUpperCase());
  if (mutation) {
    await ensureSession();
    requestHeaders['X-CSRF-Token'] = state.csrf;
  }
  let payload = body;
  if (body !== undefined && !(body instanceof FormData) && !(body instanceof Blob)) {
    requestHeaders['Content-Type'] = 'application/json';
    payload = JSON.stringify(body);
  }
  let response;
  try { response = await fetch(path, { method, body:payload, headers:requestHeaders, credentials:'same-origin' }); }
  catch { throw new Error('Cannot reach the library server. Check your connection and try again.'); }
  const text = await response.text();
  let data = null;
  if (text) { try { data = JSON.parse(text); } catch { data = { message:response.ok ? text : `The server returned an error (${response.status}).` }; } }
  if (!response.ok) {
    const error = new Error(errorMessage(data, `Request failed (${response.status}).`));
    error.status = response.status;
    throw error;
  }
  return data;
}

async function uploadRequest(path, form, onProgress) {
  await ensureSession();
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('POST', path);
    xhr.withCredentials = true;
    xhr.setRequestHeader('Accept', 'application/json');
    if (state.csrf) xhr.setRequestHeader('X-CSRF-Token', state.csrf);
    xhr.upload.addEventListener('progress', event => {
      if (event.lengthComputable) onProgress?.(event.loaded / event.total);
    });
    xhr.addEventListener('load', () => {
      let data;
      try { data = JSON.parse(xhr.responseText); } catch { data = {}; }
      if (xhr.status >= 200 && xhr.status < 300) resolve(data);
      else {
        reject(new Error(errorMessage(data, `Upload failed (${xhr.status}).`)));
      }
    });
    xhr.addEventListener('error', () => reject(new Error('Upload interrupted. Check your connection and try again.')));
    xhr.addEventListener('abort', () => reject(new Error('Upload cancelled.')));
    xhr.send(form);
  });
}

function toast(message, type = 'success') {
  const item = node('div', `toast ${type === 'error' ? 'error' : ''}`);
  item.setAttribute('role', type === 'error' ? 'alert' : 'status');
  const close = button('', 'icon-button', () => item.remove(), 'x');
  close.setAttribute('aria-label','Dismiss notification');
  item.append(icon(type === 'error' ? 'alert' : 'check'), node('span','',message), close);
  $('toastRegion').append(item);
  setTimeout(() => item.remove(), type === 'error' ? 12000 : 6500);
}

let pendingSession = null;

async function ensureSession() {
  // Coalesce simultaneous writes and renew expired cookies automatically.
  // This establishes CSRF protection; it never displays an account prompt.
  if (!pendingSession) {
    pendingSession = api('/api/session').then(session => {
      if (!session?.csrf_token) throw new Error('The browser session could not be prepared. Please try again.');
      state.csrf = session.csrf_token;
      document.querySelectorAll('[data-site-backup]').forEach(button => {
        button.hidden = !session.backup_service_origin;
        if (session.backup_service_origin) button.dataset.backupOrigin = session.backup_service_origin;
        else delete button.dataset.backupOrigin;
      });
      return session;
    }).finally(() => { pendingSession = null; });
  }
  return pendingSession;
}

async function startWorkspace(session) {
  state.ready = true;
  state.csrf = session.csrf_token;
  $('appVersion').textContent = session.version ? `v${String(session.version).replace(/^v/,'')}` : '';
  $('bootScreen').hidden = true;
  $('app').hidden = false;
  document.title = 'Layer Library';
  await Promise.allSettled([loadLibrary(), loadPrinter()]);
  clearInterval(state.statusTimer);
  state.statusTimer = setInterval(() => {
    if (state.ready && state.page === 'settings' && !document.hidden) refreshPrinterStatus(true);
  }, 30000);
}

async function bootstrap() {
  $('bootError').hidden = true; $('bootRetry').hidden = true;
  try {
    await startWorkspace(await ensureSession());
  } catch (error) {
    $('bootScreen').hidden = false;
    $('bootError').textContent = error.message; $('bootError').hidden = false;
    $('bootRetry').hidden = false;
  }
}

async function loadLibrary({ quiet = false } = {}) {
  const loadId = ++state.loadId;
  const featuredVersion = state.featuredVersion;
  if (!quiet && state.page === 'library') { state.loading = true; renderLibrary(); }
  setError('libraryError');
  try {
    const data = await api('/api/library');
    if (loadId !== state.loadId) return;
    state.library = { items:data.items || [], collections:data.collections || [], tags:data.tags || [], stats:data.stats || {} };
    // An older refresh must not undo a star clicked while its request was in
    // flight. The next fresh load still reconciles with the stored server state.
    let featuredChanged = false;
    for (const model of state.library.items) {
      const pending = state.featuredPending.get(model.id), changed = state.featuredChanges.get(model.id);
      if (pending || changed?.version > featuredVersion) {
        model.featured = pending ? pending.featured : changed.featured;
        featuredChanged = true;
      }
    }
    if (featuredChanged) state.library.stats.featured_count = state.library.items.filter(model => model.featured === true).length;
    state.loading = false;
    renderNavigation(); renderFilters(); renderLibrary();
  } catch (error) {
    if (loadId !== state.loadId) return;
    state.loading = false;
    renderLibrary(); setError('libraryError',error.message);
  }
}

async function loadTrash() {
  state.loading = true; renderLibrary(); setError('libraryError');
  try {
    const data = await api('/api/trash');
    state.trash = data.items || [];
    state.loading = false;
    $('navTrashCount').textContent = state.trash.length;
    $('navTrashCount').hidden = false;
    renderLibrary();
  } catch (error) { state.loading = false; renderLibrary(); setError('libraryError',error.message); }
}

function navigate(page, collection = '', view = 'all') {
  state.page = page;
  if (page === 'library') state.view = view === 'featured' ? 'featured' : 'all';
  if (page !== 'settings') {
    state.collection = collection; state.tag = ''; state.kind = 'all'; state.search = '';
    $('searchInput').value = '';
    renderFilters();
  }
  $('libraryView').hidden = page === 'settings';
  $('settingsView').hidden = page !== 'settings';
  document.body.classList.remove('sidebar-open');
  renderNavigation();
  if (page === 'settings') { loadPrinter(); }
  else if (page === 'trash') loadTrash();
  else renderLibrary();
}

function renderNavigation() {
  const models = Number(state.library.stats.models ?? state.library.items.length);
  $('navModelCount').textContent = models;
  $('navFeaturedCount').textContent = state.library.stats.featured_count ?? state.library.items.filter(model => model.featured === true).length;
  for (const [id, active] of [['navFeatured',state.page === 'library' && state.view === 'featured'],['navLibrary',state.page === 'library' && state.view === 'all' && !state.collection],['navTrash',state.page === 'trash'],['navSettings',state.page === 'settings']]) {
    $(id).classList.toggle('active',active);
    if (active) $(id).setAttribute('aria-current','page'); else $(id).removeAttribute('aria-current');
  }
  const collections = state.library.collections.filter(value => value.name);
  $('collectionCount').textContent = collections.length || '';
  const fragment = document.createDocumentFragment();
  if (!collections.length) fragment.append(node('p','collection-empty','Choose a collection when uploading, or add one in a model’s details.'));
  collections.forEach(collection => {
    const active = state.page === 'library' && state.view === 'all' && state.collection === collection.name;
    const item = button('',`collection-button${active ? ' active' : ''}`,() => navigate('library',collection.name));
    if (active) item.setAttribute('aria-current','page');
    item.append(icon('folder'),node('span','collection-name',collection.name),node('span','collection-number',String(collection.count)));
    fragment.append(item);
  });
  $('collectionNav').replaceChildren(fragment);
  $('breadcrumbTitle').textContent = state.page === 'settings' ? 'Printer settings' : state.page === 'trash' ? 'Trash' : state.view === 'featured' ? 'Featured' : state.collection || 'All models';
}

function renderFilters() {
  const collectionSelect = $('collectionFilter');
  collectionSelect.replaceChildren(new Option('All collections',''));
  state.library.collections.filter(value => value.name).forEach(value => collectionSelect.append(new Option(value.name,value.name)));
  collectionSelect.value = state.collection;
  $('collectionOptions').replaceChildren(...state.library.collections.filter(value => value.name).map(value => new Option(value.name,value.name)));
  const tagSelect = $('tagFilter');
  tagSelect.replaceChildren(new Option('All tags',''));
  state.library.tags.forEach(value => { const name = typeof value === 'string' ? value : value.name; if (name) tagSelect.append(new Option(name,name)); });
  tagSelect.value = state.tag;
  $('sortSelect').value = state.sort;
  document.querySelectorAll('#kindFilters button').forEach(element => {
    const active = element.dataset.kind === state.kind;
    element.classList.toggle('active',active); element.setAttribute('aria-pressed',String(active));
  });
}

function selectedModels() {
  let models = [...(state.page === 'trash' ? state.trash : state.library.items)];
  const query = state.search.trim().toLocaleLowerCase();
  models = models.filter(model =>
    (state.page !== 'library' || state.view !== 'featured' || model.featured === true) &&
    (!state.collection || model.collection === state.collection) &&
    (!state.tag || (model.tags || []).includes(state.tag)) &&
    (state.kind === 'all' || fileKind(model) === state.kind) &&
    (!query || [model.title,model.filename,model.collection,model.notes,...(model.tags || [])].filter(Boolean).join(' ').toLocaleLowerCase().includes(query))
  );
  models.sort((a,b) => {
    if (state.sort === 'name') return String(a.title || a.filename).localeCompare(String(b.title || b.filename),undefined,{numeric:true});
    if (state.sort === 'size') return (b.size_bytes || 0) - (a.size_bytes || 0);
    const field = state.sort === 'updated' ? 'updated_at' : 'created_at';
    return (new Date(b[field]).getTime() || 0) - (new Date(a[field]).getTime() || 0);
  });
  return models;
}

function setModelFeatured(id, featured, serverModel) {
  state.featuredChanges.set(id, {featured, version:++state.featuredVersion});
  const responseTime = Date.parse(serverModel?.updated_at);
  for (const model of [...state.library.items, ...state.trash, ...(state.model ? [state.model] : [])]) {
    if (model.id !== id) continue;
    model.featured = featured;
    const currentTime = Date.parse(model.updated_at);
    // A delayed star response must not replace metadata or a preview that was
    // updated while the save was in flight. Only accept a newer timestamp.
    if (Number.isFinite(responseTime) && (!Number.isFinite(currentTime) || responseTime > currentTime)) {
      model.updated_at = serverModel.updated_at;
    }
  }
  state.library.stats.featured_count = state.library.items.filter(model => model.featured === true).length;
}

async function toggleFeatured(model, control) {
  if (state.featuredPending.has(model.id)) return;
  const current = state.library.items.find(item => item.id === model.id) || model;
  const previous = current.featured === true, featured = !previous;
  const context = [state.page, state.view, state.collection, state.tag, state.kind, state.search].join('\n');
  const restoreFocus = control && document.activeElement === control;
  const index = selectedModels().findIndex(item => item.id === model.id);
  state.featuredPending.set(model.id, {featured});
  setError('libraryError');
  setModelFeatured(model.id, featured);
  renderNavigation(); renderLibrary();
  try {
    const result = await api(modelPath(model.id, '/featured'), {method:'PUT', body:{featured}});
    const updated = result?.item || result;
    if (updated?.id !== model.id || typeof updated.featured !== 'boolean' || updated.featured !== featured) {
      throw new Error('The server did not confirm this change. Please refresh the library and try again.');
    }
    setModelFeatured(model.id, updated.featured, updated);
    toast(featured ? 'Added to Featured.' : 'Removed from Featured.');
  } catch (error) {
    setModelFeatured(model.id, previous);
    const message = `Could not ${featured ? 'add this model to' : 'remove this model from'} Featured. ${error.message || 'Please try again.'}`;
    setError('libraryError', message); toast(message, 'error');
  } finally {
    state.featuredPending.delete(model.id);
    renderNavigation(); renderLibrary();
    const sameContext = context === [state.page, state.view, state.collection, state.tag, state.kind, state.search].join('\n');
    if (restoreFocus && sameContext && (!document.activeElement || document.activeElement === document.body)) {
      const controls = [...document.querySelectorAll('.featured-toggle')];
      const next = controls.find(element => element.dataset.modelId === model.id) || controls[Math.min(Math.max(index, 0), controls.length - 1)];
      (next || (!$('emptyAction').hidden ? $('emptyAction') : $('navFeatured'))).focus();
    }
  }
}

function modelCard(model, inTrash = false) {
  const card = node('article','model-card');
  const thumb = button('','thumbnail-button',() => { if (!inTrash) openModel(model.id); });
  thumb.setAttribute('aria-label',`Preview ${model.title || model.filename}`);
  if (inTrash) { thumb.disabled = true; thumb.style.opacity = '1'; }
  const placeholder = node('div','thumbnail-placeholder');
  const previewLabel = node('span','thumbnail-caption',model.thumbnail_url ? 'Preparing preview…' : 'Preview unavailable');
  placeholder.append(icon('box'),previewLabel); thumb.append(placeholder);
  const repair = button('Rebuild preview','thumbnail-retry',async () => {
    setBusy(repair,true,'Rebuilding…');
    try { await rebuildThumbnail(model); }
    catch (error) { previewLabel.textContent='Preview unavailable'; toast(error.message,'error'); }
    finally { setBusy(repair,false); }
  },'refresh');
  repair.hidden = inTrash || Boolean(model.thumbnail_url && !state.thumbnailFailures.has(model.id));
  if (model.thumbnail_url && !state.thumbnailFailures.has(model.id)) {
    const img = node('img','model-thumbnail');
    img.src = model.thumbnail_url; img.alt = ''; img.loading = 'lazy'; img.decoding = 'async';
    img.addEventListener('load',() => {
      placeholder.hidden = true; model.thumbnail_ready = true;
      state.thumbnailFailures.delete(model.id);
      if (state.model?.id === model.id) { state.model.thumbnail_ready=true; renderThumbnailStatus(); }
    });
    img.addEventListener('error',() => {
      img.remove(); previewLabel.textContent='Preview unavailable';
      state.thumbnailFailures.add(model.id); repair.hidden=inTrash;
      if (state.model?.id === model.id) renderThumbnailStatus();
    });
    thumb.append(img);
  } else previewLabel.textContent='Preview unavailable';
  thumb.append(node('span','file-kind',fileKind(model).toUpperCase()));
  if (model.job_count) { const badge = node('span','card-job-badge'); badge.append(icon('printer'),document.createTextNode(String(model.job_count))); thumb.append(badge); }
  const body = node('div','card-body');
  const title = button(model.title || model.filename,'card-title',() => { if (!inTrash) openModel(model.id); });
  title.title = model.title || model.filename;
  if (inTrash) { title.disabled = true; title.style.opacity = '1'; }
  const d = dimensions(model);
  const dim = d ? `${number(d.x)} × ${number(d.y)} × ${number(d.z)} mm` : 'Dimensions available in preview';
  body.append(title,node('p','card-meta',`${dim} · ${humanSize(model.size_bytes)}`));
  const tags = node('div','card-tags');
  const tagValues = model.tags || [];
  tagValues.slice(0,2).forEach(value => { const tag = node('span','tag',value); tag.title = value; tags.append(tag); });
  if (tagValues.length > 2) tags.append(node('span','tag',`+${tagValues.length-2}`));
  if (!tagValues.length && model.collection) tags.append(node('span','tag',model.collection));
  const footer = node('div','card-footer');
  const date = node('span','card-date',humanDate(model.created_at)); date.title = `Added ${humanDate(model.created_at,true)}`;
  const actions = node('div','card-actions');
  if (inTrash) actions.append(button('Restore','button ghost restore-button',() => restoreModel(model), 'restore'));
  else {
    const download = node('a','icon-button'); download.href = model.download_url || modelPath(model.id,'/file'); download.download = model.filename || '';
    download.setAttribute('aria-label',`Download ${model.title || model.filename}`); download.title = 'Download original'; download.append(icon('download'));
    const edit = button('','icon-button',() => openModel(model.id,'details'),'edit'); edit.setAttribute('aria-label',`Edit ${model.title || model.filename}`); edit.title = 'Edit details';
    actions.append(download,edit);
  }
  footer.append(date,actions); body.append(tags,repair,footer); card.append(thumb,body);
  if (!inTrash) {
    const featured = model.featured === true, label = featured ? 'Remove from Featured' : 'Add to Featured';
    const star = button('', `featured-toggle${featured ? ' is-featured' : ''}`, event => {
      event.preventDefault(); event.stopPropagation(); toggleFeatured(model, event.currentTarget);
    }, 'star');
    star.title = label; star.dataset.modelId = model.id;
    star.setAttribute('aria-label', `${label}: ${model.title || model.filename}`);
    star.setAttribute('aria-pressed', String(featured));
    star.disabled = state.featuredPending.has(model.id);
    star.setAttribute('aria-busy', String(star.disabled));
    card.append(star);
  }
  return card;
}

function renderLibrary() {
  if (state.page === 'settings') return;
  const trash = state.page === 'trash';
  const featured = !trash && state.view === 'featured';
  $('pageTitle').textContent = trash ? 'Trash' : featured ? 'Featured' : state.collection || 'Model library';
  $('pageEyebrow').textContent = trash ? 'A SECOND CHANCE FOR YOUR MODELS' : featured ? 'YOUR FEATURED MODELS' : state.collection ? 'YOUR COLLECTION' : 'MAKE ROOM FOR YOUR NEXT IDEA';
  $('libraryStats').hidden = trash; $('addModelsButton').hidden = trash; $('dropHint').hidden = trash;
  const stats = state.library.stats;
  $('statModels').textContent = stats.models ?? state.library.items.length;
  $('statJobs').textContent = stats.print_job_count ?? state.library.items.reduce((sum,item) => sum + (item.job_count || 0),0);
  $('statStorage').textContent = humanSize(stats.total_bytes ?? 0);
  $('librarySummary').textContent = trash ? 'Restore a model to return it to your library.' : featured ? 'Models you have added to Featured.' : 'Your models, organised and ready for the next project.';
  if (state.loading) {
    const skeletons = [];
    for (let i=0;i<8;i++) { const card = node('article','model-card skeleton'); const image = node('div','thumbnail-button'); const body = node('div','card-body'); body.append(node('div','skeleton-line'),node('div','skeleton-line short')); card.append(image,body); skeletons.push(card); }
    $('modelGrid').replaceChildren(...skeletons); $('emptyState').hidden = true; $('resultsCount').textContent = 'Loading…'; return;
  }
  const models = selectedModels();
  $('modelGrid').replaceChildren(...models.map(model => modelCard(model,trash)));
  $('resultsCount').textContent = `${models.length} ${models.length === 1 ? 'model' : 'models'}`;
  $('emptyState').hidden = models.length > 0;
  if (!models.length) {
    const filtered = Boolean(state.search || state.collection || state.tag || state.kind !== 'all');
    $('emptyTitle').textContent = filtered ? (featured ? 'No matching featured models' : 'No matching models') : trash ? 'Trash is empty' : featured ? 'No featured models yet' : 'No models yet';
    $('emptyText').textContent = filtered ? 'Try another search or clear your filters.' : trash ? 'Models you move to trash will appear here.' : featured ? 'Star models in All models to keep your favourites here. Click a gold star to remove a model from Featured.' : 'Upload your STL, OBJ and 3MF files to start your collection.';
    $('emptyAction').hidden = trash && !filtered;
    $('emptyAction').replaceChildren(icon(filtered ? 'x' : featured ? 'grid' : 'plus'),document.createTextNode(filtered ? 'Clear filters' : featured ? 'Browse All models' : 'Add your first models'));
    $('emptyAction').onclick = filtered ? () => { state.collection=''; state.tag=''; state.kind='all'; state.search=''; $('searchInput').value=''; renderFilters(); renderNavigation(); renderLibrary(); } : featured ? () => navigate('library') : () => openUpload();
  }
}

function validModelFiles(files) {
  const valid = [], errors = [];
  Array.from(files).forEach(file => {
    if (/\.gcode\.3mf$/i.test(file.name)) errors.push(`${file.name}: attach sliced files from a model’s Print jobs tab.`);
    else if (!/\.(stl|obj|3mf)$/i.test(file.name)) errors.push(`${file.name}: choose an STL, OBJ or 3MF file.`);
    else if (file.size > MAX_FILE_BYTES) errors.push(`${file.name}: the limit is 250 MiB per file.`);
    else if (!file.size) errors.push(`${file.name}: this file is empty.`);
    else valid.push(file);
  });
  if (errors.length) toast(errors.join('\n'),'error');
  return valid;
}

function openUpload(files = []) {
  if (state.uploadBusy) return;
  state.uploadFiles = validModelFiles(files);
  $('uploadCollection').value = state.collection;
  $('uploadProgress').hidden = true; $('uploadResultErrors').hidden = true;
  setError('uploadError'); renderUploadFiles();
  if (!$('uploadModal').open) $('uploadModal').showModal();
}

function addUploadFiles(files) {
  if (state.uploadBusy) return;
  const existing = new Set(state.uploadFiles.map(file => `${file.name}:${file.size}:${file.lastModified}`));
  validModelFiles(files).forEach(file => { const key = `${file.name}:${file.size}:${file.lastModified}`; if (!existing.has(key)) { state.uploadFiles.push(file); existing.add(key); } });
  renderUploadFiles();
}

function renderUploadFiles() {
  $('selectedFileList').replaceChildren(...state.uploadFiles.map((file,index) => {
    const row = node('div','selected-file'), information = node('div');
    information.append(node('strong','',file.name),node('small','',humanSize(file.size)));
    const remove = button('','icon-button',() => { state.uploadFiles.splice(index,1); renderUploadFiles(); },'x');
    remove.setAttribute('aria-label',`Remove ${file.name}`); remove.disabled = state.uploadBusy;
    row.append(icon('file'),information,remove); return row;
  }));
  $('uploadSubmit').disabled = !state.uploadFiles.length || state.uploadBusy;
}

async function uploadModels(event) {
  event.preventDefault();
  if (!state.uploadFiles.length || state.uploadBusy) return;
  state.uploadBusy = true; setError('uploadError'); $('uploadResultErrors').hidden = true;
  setBusy($('uploadSubmit'),true,'Uploading…');
  document.querySelectorAll('[data-close="uploadModal"]').forEach(element => element.disabled=true);
  $('uploadCollection').disabled = true;
  $('uploadProgress').hidden = false;
  const files = [...state.uploadFiles], total = files.reduce((sum,file) => sum+file.size,0);
  let finishedBytes=0, imported=0;
  const errors=[];
  try {
    for (let offset=0;offset<files.length;offset+=10) {
      const batch=files.slice(offset,offset+10), bytes=batch.reduce((sum,file)=>sum+file.size,0);
      const form=new FormData(); batch.forEach(file=>form.append('files',file));
      if ($('uploadCollection').value.trim()) form.append('collection',$('uploadCollection').value.trim());
      $('uploadProgressText').textContent = files.length>10 ? `Uploading batch ${Math.floor(offset/10)+1} of ${Math.ceil(files.length/10)}…` : 'Uploading models…';
      const result=await uploadRequest('/api/models/upload',form,ratio=>{
        const percent=Math.round((finishedBytes+bytes*ratio)/total*100);
        $('uploadPercent').textContent=`${percent}%`; $('uploadProgressBar').value=percent;
        if(ratio>=1) $('uploadProgressText').textContent='Processing meshes…';
      });
      finishedBytes+=bytes; imported+=(result.items||[]).length;
      (result.errors||[]).forEach(error=>errors.push(typeof error==='string'?error:`${error.filename ? `${error.filename}: `:''}${errorMessage(error,'Could not import this file.')}`));
    }
    await loadLibrary({quiet:true});
    if(imported) toast(`${imported} ${imported===1?'model added':'models added'} to your library.`);
    if(errors.length) {
      const box=$('uploadResultErrors'); box.replaceChildren(node('strong','',`${errors.length} ${errors.length===1?'file needs':'files need'} attention`),...errors.map(message=>node('div','',message))); box.hidden=false;
      state.uploadFiles=[]; renderUploadFiles();
      $('uploadProgressText').textContent='Finished';
    } else { $('uploadModal').close(); if(state.page!=='library'||state.view==='featured')navigate('library'); }
  } catch(error) { setError('uploadError',error.message); if(imported) await loadLibrary({quiet:true}); }
  finally {
    state.uploadBusy=false;
    setBusy($('uploadSubmit'),false);
    document.querySelectorAll('[data-close="uploadModal"]').forEach(element=>element.disabled=false);
    $('uploadCollection').disabled=false; renderUploadFiles();
  }
}

function resetViewerControls() {
  $('viewerGrid').classList.add('active'); $('viewerGrid').setAttribute('aria-pressed','true');
  $('viewerWireframe').classList.remove('active'); $('viewerWireframe').setAttribute('aria-pressed','false');
  $('viewerColor').value='#c2aa82';
}

async function openModel(id, tab = 'preview') {
  const loadId=++state.modelLoadId;
  state.viewer?.dispose(); state.viewer=null; state.model=null; state.jobs=[];
  state.previewOptInId=null;
  resetViewerControls();
  $('detailTitle').textContent='Loading model…'; $('detailFilename').textContent='';
  $('detailJobCount').textContent='0'; $('jobList').replaceChildren();
  setError('metadataError'); setError('jobsError'); setError('thumbnailError');
  $('thumbnailStatus').textContent='';
  setBusy($('rebuildThumbnailButton'),false);
  $('rebuildThumbnailButton').disabled=true;
  $('openStudioButton').disabled=true;
  showViewerStatus('loading');
  if(!$('modelModal').open) $('modelModal').showModal();
  switchTab(tab,false);
  try {
    const result=await api(modelPath(id));
    if(loadId!==state.modelLoadId || !$('modelModal').open)return;
    state.model=result.item||result;
    renderModelDetails();
    if(state.tab==='jobs')await loadJobs();
    if(state.tab==='preview')await ensureViewer();
  } catch(error) { if(loadId===state.modelLoadId){$('detailTitle').textContent='Could not open model';showViewerStatus('error',error.message);toast(error.message,'error');} }
}

function showViewerStatus(status, message='') {
  const element=$('viewerStatus');
  element.hidden=status==='ready'; element.classList.toggle('error',status==='error');
  element.classList.toggle('large-model',status==='large');
  element.querySelector('strong').textContent=message||(status==='large'?'Large model':status==='loading'?'Preparing 3D preview…':'Preview unavailable. You can still download the original file.');
  $('largePreviewReason').hidden=status!=='large';
  $('loadLargePreviewButton').hidden=status!=='large';
  ['viewerReset','viewerGrid','viewerWireframe','viewerColor'].forEach(id=>$(id).disabled=status!=='ready');
}

function needsPreviewOptIn(model) {
  return model.large_preview===true && state.previewOptInId!==model.id;
}

function thumbnailSaved(model, url) {
  const base=url || modelPath(model.id,'/thumbnail');
  const values = {thumbnail_url:`${base}${base.includes('?')?'&':'?'}v=${Date.now()}`,thumbnail_ready:true};
  Object.assign(model,values);
  for (const item of [...state.library.items,...state.trash]) if(item.id===model.id)Object.assign(item,values);
  state.thumbnailFailures.delete(model.id);
  if(state.model?.id===model.id){Object.assign(state.model,values);renderThumbnailStatus();}
  renderLibrary();
}

function renderThumbnailStatus() {
  const model=state.model;if(!model)return;
  const rebuilding=state.thumbnailRebuilds.has(model.id);
  const unavailable=state.thumbnailFailures.has(model.id);
  $('thumbnailStatus').textContent=rebuilding?'Rebuilding preview image…':unavailable?'Preview image unavailable. Try rebuilding it.':model.thumbnail_ready?'Preview image saved.':'Preview image is being prepared.';
  $('rebuildThumbnailButton').disabled=rebuilding;
}

async function rebuildThumbnail(model) {
  if(state.thumbnailRebuilds.has(model.id))return state.thumbnailRebuilds.get(model.id);
  const pending=(async()=>{
    const result=await api(modelPath(model.id,'/thumbnail/rebuild'),{method:'POST'});
    if(!result?.thumbnail_url)throw new Error('The server did not return a preview image. Please try again.');
    thumbnailSaved(model,result.thumbnail_url);
    return result;
  })();
  state.thumbnailRebuilds.set(model.id,pending);
  if(state.model?.id===model.id)renderThumbnailStatus();
  try{return await pending;}
  finally{
    state.thumbnailRebuilds.delete(model.id);
    if(state.model?.id===model.id)renderThumbnailStatus();
  }
}

async function rebuildCurrentThumbnail() {
  const model=state.model;if(!model)return;
  const loadId=state.modelLoadId;
  setError('thumbnailError');
  const control=$('rebuildThumbnailButton');setBusy(control,true,'Rebuilding…');
  try{await rebuildThumbnail(model);}
  catch(error){if(loadId===state.modelLoadId)setError('thumbnailError',error.message);}
  finally{if(loadId===state.modelLoadId){setBusy(control,false);renderThumbnailStatus();}}
}

async function ensureViewer() {
  if(!state.model || state.viewer || !$('modelModal').open)return;
  const model=state.model, loadId=state.modelLoadId;
  if(needsPreviewOptIn(model)){showViewerStatus('large');return;}
  try {
    const viewer=new ModelViewer($('viewerCanvas'),{onStatus:(status,message)=>{if(loadId===state.modelLoadId)showViewerStatus(status,message);}});
    state.viewer=viewer;
    const measured=await viewer.load(model.preview_url||model.download_url||modelPath(model.id,'/file'),{kind:fileKind(model)});
    if(loadId!==state.modelLoadId || !measured)return;
    if(!dimensions(model)){state.model.dimensions=measured;renderDimensions(state.model);}
    if(model.thumbnail_ready!==true || state.thumbnailFailures.has(model.id)){
      try {
        const blob=await viewer.captureThumbnail();
        if(blob && loadId===state.modelLoadId){
          await api(modelPath(model.id,'/thumbnail'),{method:'POST',body:blob,headers:{'Content-Type':'image/png'}});
          // The server preserves a good image if its automatic render completed
          // while this browser was loading the full mesh.
          thumbnailSaved(model,modelPath(model.id,'/thumbnail'));
        }
      } catch (error) {
        if(loadId===state.modelLoadId)setError('thumbnailError',`The 3D model opened, but its preview image could not be saved. ${error.message || 'Use Rebuild preview image to try again.'}`);
      }
    }
  }catch(error){if(loadId===state.modelLoadId)showViewerStatus('error',error.message||'3D rendering is unavailable in this browser. Download the file to open it in your slicer.');}
}

function renderDimensions(model) {
  const d=dimensions(model);
  $('dimensionValues').replaceChildren(...[['x','W'],['y','D'],['z','H']].map(([key,label])=>{
    const value=node('div','dimension-value');value.append(node('strong','',d?number(d[key]):'—'),node('span','',`${label} · mm`));return value;
  }));
  let fit=model.fit_p2s;
  if(fit===null||fit===undefined)fit=d?['x','y','z'].every(key=>Number(d[key])<=256.001):null;
  const badge=$('fitBadge');badge.className=`fit-badge${fit===false?' warning':fit==null?' unknown':''}`;
  badge.replaceChildren(icon(fit===true?'check':fit===false?'alert':'info'),document.createTextNode(fit===true?'Within P2S build volume':fit===false?'Exceeds P2S build volume':'P2S fit not yet available'));
}

function renderModelDetails() {
  const model=state.model;if(!model)return;
  $('detailTitle').textContent=model.title||model.filename;
  $('detailFilename').textContent=model.filename||'';
  $('detailKind').textContent=fileKind(model).toUpperCase();
  $('detailJobCount').textContent=model.job_count||0;
  $('openStudioButton').disabled=false;
  renderThumbnailStatus();
  $('modelFormatNote').hidden=fileKind(model)!=='obj';
  renderDimensions(model);
  const facts=[['File size',humanSize(model.size_bytes)],['Triangles',model.triangle_count?Number(model.triangle_count).toLocaleString():'—'],['Added',humanDate(model.created_at,true)],['Collection',model.collection||'Unsorted']];
  $('modelFacts').replaceChildren(...facts.map(([label,text])=>{const row=node('div');const value=node('dd','',text);value.title=text;row.append(node('dt','',label),value);return row;}));
  $('detailTags').replaceChildren(...(model.tags||[]).map(value=>node('span','tag',value)));
  const download=$('downloadModelButton');download.href=model.download_url||modelPath(model.id,'/file');download.download=model.filename||'';
  download.replaceChildren(icon('download'),document.createTextNode(`Download original ${fileKind(model).toUpperCase()}`));
  $('editTitle').value=model.title||model.filename||'';
  $('editCollection').value=model.collection||'';
  $('editTags').value=(model.tags||[]).join(', ');
  $('editNotes').value=model.notes||'';
}

async function prepareStudio(item, kind = 'model') {
  if (!item || (kind === 'job' && item.compatible !== true)) return;
  const loadId = ++state.studioLoadId;
  const device = studioDevice(navigator.userAgentData?.platform || navigator.platform, navigator.userAgent, navigator.maxTouchPoints);
  const isJob = kind === 'job', dialog = $('studioModal');
  const download = $('studioDownloadLink'), launch = $('studioLaunchLink');
  $('studioFilename').textContent = item.filename || item.title || 'Model';
  $('studioStatus').textContent = '';
  $('studioInstructions').textContent = isJob
    ? 'This is an already sliced print file. Review the P2S, nozzle, filament and plate settings in Studio before sending it. Open the original model and re-slice if those settings need to change.'
    : 'Select your P2S, filament and print settings in Studio, then slice and send using your normal Bambu connection.';
  setError('studioError');
  launch.hidden = true; launch.removeAttribute('href');
  $('studioLaunchHelp').hidden = true;
  download.href = isJob ? jobPath(item.id, '/file') : modelPath(item.id, '/file');
  download.download = item.filename || '';
  download.replaceChildren(icon('download'), document.createTextNode('Download original file'));
  $('studioPreparing').hidden = device === 'mobile';
  if (!dialog.open) dialog.showModal();
  if (device === 'mobile') {
    $('studioStatus').textContent = 'Bambu Studio needs a Windows, Mac or Linux computer. Open this library on that computer to use the Studio button, or download the file here to use later.';
    return;
  }
  try {
    const result = await api(isJob ? jobPath(item.id, '/studio') : modelPath(item.id, '/studio'), {method:'POST'});
    if (loadId !== state.studioLoadId || !dialog.open) return;
    const url = studioDownloadURL(result?.download_url, window.location.origin);
    launch.href = studioProtocolURL(url, device);
    launch.hidden = false;
    download.href = url; download.download = result.filename || item.filename || '';
    download.replaceChildren(icon('download'), document.createTextNode(isJob ? 'Download print file' : 'Download 3MF copy'));
    $('studioStatus').textContent = result.converted
      ? 'A 3MF copy is ready. Your original file is unchanged. Click below to ask your browser to open Bambu Studio.'
      : 'The file is ready. Click below to ask your browser to open Bambu Studio.';
    $('studioLaunchHelp').hidden = false;
    // Do not navigate here: the async preparation may have consumed the
    // browser's user activation. The visible anchor needs a fresh user click.
    launch.focus();
  } catch (error) {
    if (loadId === state.studioLoadId && dialog.open) {
      $('studioStatus').textContent = 'The Studio link could not be prepared. You can download the original file and open it from Studio’s File menu.';
      setError('studioError', error.message);
    }
  } finally {
    if (loadId === state.studioLoadId && dialog.open) $('studioPreparing').hidden = true;
  }
}

function switchTab(tab, load = true) {
  state.tab=tab;
  document.querySelectorAll('.model-tabs button').forEach(element=>{
    const active=element.dataset.tab===tab;element.classList.toggle('active',active);element.setAttribute('aria-selected',String(active));element.tabIndex=active?0:-1;
  });
  ['preview','details','jobs'].forEach(name=>$(name+'Panel').hidden=name!==tab);
  if(load&&state.model){
    if(tab==='preview'){ensureViewer();setTimeout(()=>state.viewer?.resize(),0);}
    if(tab==='jobs')loadJobs();
  }
}

async function saveMetadata(event) {
  event.preventDefault();if(!state.model)return;
  const modelId = state.model.id;
  setError('metadataError');setBusy($('saveMetadataButton'),true,'Saving…');
  try {
    const payload={title:$('editTitle').value.trim(),collection:$('editCollection').value.trim(),tags:[...new Set($('editTags').value.split(',').map(value=>value.trim()).filter(Boolean))],notes:$('editNotes').value};
    if(!payload.title)throw new Error('Please enter a title.');
    const result=await api(modelPath(modelId),{method:'PUT',body:payload});
    if(state.model?.id===modelId){state.model={...state.model,...payload,...(result?.item||result||{})};renderModelDetails();}
    await loadLibrary({quiet:true});toast('Model details saved.');
  }catch(error){setError('metadataError',error.message);}finally{setBusy($('saveMetadataButton'),false);}
}

function confirmAction({title,message,label='Confirm',danger=true}) {
  $('confirmTitle').textContent=title;$('confirmText').textContent=message;
  $('confirmAccept').textContent=label;$('confirmAccept').className=`button ${danger?'danger':'primary'}`;
  $('confirmModal').returnValue='';$('confirmModal').showModal();
  return new Promise(resolve=>$('confirmModal').addEventListener('close',()=>resolve($('confirmModal').returnValue==='confirm'),{once:true}));
}

async function deleteModel() {
  if(!state.model)return;
  const model=state.model;
  if(!await confirmAction({title:'Move this model to trash?',message:`“${model.title||model.filename}” will leave your library. You can restore it from Trash.`,label:'Move to trash'}))return;
  try{await api(modelPath(model.id),{method:'DELETE'});if(state.model?.id===model.id)$('modelModal').close();await loadLibrary({quiet:true});toast('Model moved to trash.');}
  catch(error){toast(error.message,'error');}
}

async function restoreModel(model) {
  try{await api(modelPath(model.id,'/restore'),{method:'POST'});await Promise.allSettled([loadLibrary({quiet:true}),loadTrash()]);toast(`${model.title||model.filename} restored.`);}
  catch(error){toast(error.message,'error');}
}

async function loadJobs() {
  if(!state.model)return;
  const modelId=state.model.id;
  setError('jobsError');
  $('jobList').replaceChildren(node('div','job-empty','Loading print files…'));
  try {
    const data=await api(modelPath(modelId,'/jobs'));
    if(state.model?.id!==modelId)return;
    state.jobs=data.items||[];state.model.job_count=state.jobs.length;
    $('detailJobCount').textContent=state.jobs.length;
    const item=state.library.items.find(value=>value.id===modelId);if(item)item.job_count=state.jobs.length;
    renderJobs();renderLibrary();
  }catch(error){if(state.model?.id===modelId){setError('jobsError',error.message);$('jobList').replaceChildren();}}
}

function printerMode() { return state.printer.mode === 'lan' ? 'lan' : 'cloud'; }
function printerVerified() { return printerMode() === 'lan' && Boolean(state.printer.lan_verified || state.printer.verified || state.printer.connection_verified); }
function transferDone(status) { return ['complete','completed','success','succeeded','done'].includes(String(status).toLowerCase()); }
function transferFailed(status) { return ['failed','error','cancelled','canceled','interrupted'].includes(String(status).toLowerCase()); }
function transferRunning(transfer) { return transfer&&!transferDone(transfer.status)&&!transferFailed(transfer.status); }

function transferElement(transfer) {
  const failed=transferFailed(transfer.status),done=transferDone(transfer.status);
  const box=node('div',`transfer-status${failed?' failed':''}`),row=node('div');
  const progress=done?100:Math.max(0,Math.min(100,Number(transfer.progress)||0));
  row.append(node('strong','',done?'File transferred':transfer.status==='interrupted'?'Transfer interrupted':failed?'Transfer failed':transfer.status==='queued'?'Waiting to transfer…':'Transferring to printer…'),node('span','',failed?'':`${Math.round(progress)}%`));
  const bar=node('progress');bar.max=100;bar.value=progress;
  box.append(row,bar,node('p','',transfer.message||(done?'The file is stored on the printer. Printing has not started.':'')));return box;
}

function renderJobs() {
  if(!state.model)return;
  const lan = printerMode() === 'lan';
  $('jobsIntroText').textContent = lan
    ? 'Attach a .gcode.3mf file sliced for your P2S. You can open it in Bambu Studio or transfer it over LAN when your printer and firmware allow it. A LAN transfer does not start printing.'
    : 'Slice for your P2S in Bambu Studio, then export and attach a .gcode.3mf file. Open it in Studio to review the prepared print and send using your normal connection.';
  if(!state.jobs.length){const empty=node('div','job-empty');empty.append(icon('printer'),node('strong','','No sliced files attached'),node('p','','Attach a Bambu Studio .gcode.3mf file to keep the prepared print file with this model.'));$('jobList').replaceChildren(empty);return;}
  $('jobList').replaceChildren(...state.jobs.map(job=>{
    const card=node('article','job-card'),top=node('div','job-top'),symbol=node('span','job-file-icon'),main=node('div','job-main');symbol.append(icon('file'));
    main.append(node('h4','',job.filename),node('p','',`${humanSize(job.size_bytes)} · Added ${humanDate(job.created_at,true)}`));
    const pills=node('div','job-pills');
    const printerIdentified=Boolean(job.printer_model)&&!/^unknown\b/i.test(String(job.printer_model).trim());
    pills.append(node('span',`job-pill${job.compatible===true?'':' incompatible'}`,job.compatible===true?'P2S recognised':job.compatible===false&&printerIdentified?'Not compatible with P2S':'P2S not identified'));
    if(job.printer_model)pills.append(node('span','job-pill',job.printer_model));
    if(Number.isFinite(Number(job.nozzle_diameter))&&Number(job.nozzle_diameter)>0)pills.append(node('span','job-pill',`${number(job.nozzle_diameter)} mm nozzle`));
    if(job.plate_names?.length)pills.append(node('span','job-pill',`${job.plate_names.length} ${job.plate_names.length===1?'plate':'plates'}`));
    main.append(pills);top.append(symbol,main);card.append(top);
    const actions=node('div','job-actions');
    const download=node('a','button secondary small-button');download.href=jobPath(job.id,'/file');download.download=job.filename;download.append(icon('download'),document.createTextNode('Download'));
    const transfer=state.transfers.get(job.id);
    const remove=button('','icon-button',()=>deleteJob(job),'trash');remove.title='Remove print file';remove.setAttribute('aria-label',`Remove ${job.filename}`);remove.disabled=Boolean(transferRunning(transfer));
    actions.append(remove,download);
    if(job.compatible === true) {
      const studio=button('Open in Bambu Studio','button primary small-button',()=>prepareStudio(job,'job'),'printer');
      studio.title='Review this sliced file and send it from Bambu Studio.'; actions.append(studio);
    }
    if(lan) {
      if(!state.printer.configured||!printerVerified()) {
        const connect=button(state.printer.configured?'Test LAN connection':'Set up LAN transfer','button secondary small-button',()=>{$('modelModal').close();navigate('settings');},'connection');
        connect.title='Save and test the optional LAN connection before transferring a file.';actions.append(connect);
      }else{
        const send=button('Transfer over LAN','button secondary small-button',()=>sendJob(job),'send');
        send.disabled=job.compatible!==true||Boolean(transferRunning(transfer));
        send.title=job.compatible===true?'Transfers this sliced file. Does not start a print.':'Re-slice for Bambu P2S before transferring.';
        actions.append(send);
      }
    }
    card.append(actions);
    if(job.compatible !== true) card.append(node('p','job-workflow-note','A P2S print profile was not confirmed in this file. Open the original model in Bambu Studio, select your P2S and re-slice before sending. The attached file can still be downloaded.'));
    if(transfer)card.append(transferElement(transfer));return card;
  }));
}

async function uploadJob(file) {
  if(!file||!state.model||state.jobUploadBusy)return;
  if(!/\.gcode\.3mf$/i.test(file.name)){setError('jobsError','Choose the .gcode.3mf file exported after slicing in Bambu Studio. An STL, OBJ or unsliced model 3MF needs slicing first.');return;}
  if(file.size>MAX_FILE_BYTES){setError('jobsError','The print-file limit is 250 MiB.');return;}
  state.jobUploadBusy=true;
  const modelId=state.model.id;setError('jobsError');setBusy($('attachJobButton'),true,'Uploading…');$('jobUploadProgress').hidden=false;
  try{
    const form=new FormData();form.append('file',file);
    await uploadRequest(modelPath(modelId,'/jobs'),form,ratio=>{$('jobUploadPercent').textContent=`${Math.round(ratio*100)}%`;$('jobUploadBar').value=ratio*100;});
    if(state.model?.id===modelId)await loadJobs();toast('Sliced print file attached.');await loadLibrary({quiet:true});
  }catch(error){if(state.model?.id===modelId)setError('jobsError',error.message);else toast(error.message,'error');}
  finally{state.jobUploadBusy=false;setBusy($('attachJobButton'),false);$('jobUploadProgress').hidden=true;$('jobFileInput').value='';}
}

async function deleteJob(job) {
  if(!await confirmAction({title:'Remove this print file?',message:`Remove “${job.filename}” from this model? The original model will stay in your library.`,label:'Remove print file'}))return;
  try{await api(jobPath(job.id),{method:'DELETE'});await loadJobs();await loadLibrary({quiet:true});toast('Print file removed.');}
  catch(error){setError('jobsError',error.message);}
}

async function sendJob(job) {
  if(printerMode()!=='lan'||job.compatible!==true||!printerVerified()||transferRunning(state.transfers.get(job.id)))return;
  const modelId=state.model?.id;
  setError('jobsError');
  const pending={id:null,modelId,status:'queued',progress:0,message:'Preparing the file transfer…'};state.transfers.set(job.id,pending);renderJobs();
  try{
    const result=await api(jobPath(job.id,'/send'),{method:'POST',body:{start_print:false}});
    const transfer={...pending,id:result.transfer_id,status:result.status||'queued',message:result.message||'Transfer queued.'};
    if(!transfer.id)throw new Error('The server did not return a transfer ID. Check printer status before trying again.');
    state.transfers.set(job.id,transfer);renderJobs();pollTransfer(job.id);
  }catch(error){pending.status='failed';pending.message=error.message;state.transfers.set(job.id,pending);renderJobs();toast(error.message,'error');}
}

async function pollTransfer(jobId) {
  const transfer=state.transfers.get(jobId);if(!transfer?.id||!state.ready)return;
  try{
    const result=await api(`/api/transfers/${encodeURIComponent(transfer.id)}`);
    Object.assign(transfer,result);transfer.pollErrors=0;
    if(state.model?.id===transfer.modelId)renderJobs();
    if(transferDone(transfer.status)){toast('Print file transferred. Printing has not started.');return;}
    if(transferFailed(transfer.status)){toast(transfer.message||'Printer transfer failed.','error');return;}
  }catch(error){
    transfer.pollErrors=(transfer.pollErrors||0)+1;
    transfer.message=transfer.pollErrors>2?'Cannot refresh transfer status. Reconnecting…':error.message;
    if(state.model?.id===transfer.modelId)renderJobs();
  }
  if(state.ready)transfer.timer=setTimeout(()=>pollTransfer(jobId),transfer.pollErrors>2?10000:2000);
}

async function loadPrinter() {
  try{
    state.printer=await api('/api/printer')||{mode:'cloud',configured:false};
    $('printerQuickName').textContent=state.printer.name||'Bambu P2S';
    $('printerQuickStatus').textContent=printerMode()==='cloud'?'Use Bambu Studio':state.printer.configured?(printerVerified()?'LAN connection verified':'LAN connection needs testing'):'Set up LAN transfer';
    $('printerMode').value=printerMode();
    $('printerName').value=state.printer.name||'My Bambu P2S';$('printerHost').value=state.printer.host||'';$('printerSerial').value=state.printer.serial||'';$('printerCode').value='';
    $('printerCode').placeholder=state.printer.access_code_saved?'Saved · leave blank to keep':'Enter LAN access code';
    $('accessCodeHelp').textContent=state.printer.access_code_saved?'Access code saved. Leave this field blank to keep it.':'The saved code is never displayed.';
    updatePrinterModeFields();
    if(state.page==='settings')refreshPrinterStatus(true);
    if(state.tab==='jobs'&&state.model)renderJobs();
  }catch(error){if(state.page==='settings')setError('printerFormError',error.message);}
}

function updatePrinterModeFields() {
  const lan = $('printerMode').value === 'lan';
  $('printerLanFields').hidden = !lan;
  $('printerLanFields').disabled = !lan;
  $('printerCloudHelp').hidden = lan;
  $('printerHost').required = lan;
  $('printerCode').required = lan && !state.printer.access_code_saved;
  $('testPrinterButton').hidden = !lan;
  $('refreshPrinterButton').hidden = !lan || printerMode() !== 'lan';
  $('resetTrustButton').hidden = !lan || !printerVerified();
  setError('printerFormError');
}

async function savePrinter({quiet=false}={}) {
  setError('printerFormError');
  if(!$('printerForm').reportValidity())return false;
  const payload={mode:$('printerMode').value === 'lan' ? 'lan' : 'cloud',name:$('printerName').value.trim()};
  if(payload.mode==='lan')Object.assign(payload,{host:$('printerHost').value.trim(),serial:$('printerSerial').value.trim(),access_code:$('printerCode').value});
  try{
    const result=await api('/api/printer',{method:'PUT',body:payload});
    if(result&&typeof result==='object')state.printer={...state.printer,...result};
    await loadPrinter();if(!quiet)toast('Printer settings saved.');return true;
  }catch(error){setError('printerFormError',error.message);return false;}
}

async function testPrinter() {
  if($('printerMode').value!=='lan')return;
  setBusy($('testPrinterButton'),true,'Testing connection…');$('savePrinterButton').disabled=true;
  try{
    if(!await savePrinter({quiet:true}))return;
    const result=await api('/api/printer/test',{method:'POST'});
    if(!result?.ok)throw new Error(result?.message||'The printer connection could not be verified.');
    state.printer.verified=true;state.printer.connection_verified=true;
    await loadPrinter();await refreshPrinterStatus(true);toast(result.message||'Printer connection verified.');
  }catch(error){setError('printerFormError',error.message);renderPrinterStatus({available:false,message:error.message});}
  finally{setBusy($('testPrinterButton'),false);$('savePrinterButton').disabled=false;}
}

async function resetPrinterTrust() {
  if(!printerVerified())return;
  const host=state.printer.host||'this IP address';
  const accepted=await confirmAction({title:'Reset the saved printer certificate?',message:`Only do this after confirming that ${host} belongs to your printer, for example after a legitimate firmware certificate change. Your printer settings and access code will be kept. You must test the connection again before sending files.`,label:'Reset certificate'});
  if(!accepted)return;
  setBusy($('resetTrustButton'),true,'Resetting…');
  try{await api('/api/printer/trust/reset',{method:'POST'});await loadPrinter();toast('Saved certificate reset. Test the printer connection again before sending files.');}
  catch(error){setError('printerFormError',error.message);}
  finally{setBusy($('resetTrustButton'),false);$('resetTrustButton').hidden=!printerVerified();}
}

function renderPrinterStatus(data) {
  const container=$('printerStatusContent');
  if(printerMode()==='cloud'){container.replaceChildren(node('span','status-dot muted-dot'),node('strong','','Managed in Bambu Studio'),node('p','','Open a model in Bambu Studio, select your P2S, then slice and send. View live printer status in Studio or Bambu Handy; this library does not read your cloud account.'));return;}
  if(!state.printer.configured){container.replaceChildren(node('span','status-dot muted-dot'),node('strong','','Not configured'),node('p','','Save your printer details to get started.'));return;}
  const unavailable=data?.available===false||data?.unavailable===true||data?.status==='unavailable'||data?.connected===false;
  const label=unavailable?'Status unavailable':data?.state||data?.printer_state||data?.gcode_state||data?.status||'Printer connected';
  const safeLabel=typeof label==='string'?label.replaceAll('_',' '):'Printer connected';
  container.replaceChildren(node('span',`status-dot${unavailable?' amber-dot':''}`),node('strong','',safeLabel),node('p','',data?.message||(printerVerified()?'LAN connection has been verified.':'Save and test the connection before sending a print file.')));
  const values=[['Progress',data?.progress!=null?`${number(data.progress,0)}%`:null],['Nozzle',data?.nozzle_temperature!=null?`${number(data.nozzle_temperature,0)} °C`:null],['Bed',data?.bed_temperature!=null?`${number(data.bed_temperature,0)} °C`:null]].filter(([,value])=>value!==null);
  if(values.length){const facts=node('div','printer-status-facts');values.forEach(([key,value])=>{const row=node('div');row.append(node('span','',key),document.createTextNode(value));facts.append(row);});container.append(facts);}
}

async function refreshPrinterStatus(quiet=false) {
  if(printerMode()==='cloud'||!state.printer.configured){renderPrinterStatus(null);return;}
  if(!quiet)setBusy($('refreshPrinterButton'),true,'');
  try{renderPrinterStatus(await api('/api/printer/status'));}
  catch(error){renderPrinterStatus({available:false,message:error.message});}
  finally{if(!quiet)setBusy($('refreshPrinterButton'),false);}
}

function bindEvents() {
  $('bootRetry').addEventListener('click',bootstrap);
  $('navFeatured').addEventListener('click',()=>navigate('library','','featured'));
  $('navLibrary').addEventListener('click',()=>navigate('library'));
  $('navTrash').addEventListener('click',()=>navigate('trash'));
  $('navSettings').addEventListener('click',()=>navigate('settings'));
  $('printerQuick').addEventListener('click',()=>navigate('settings'));
  $('menuButton').addEventListener('click',()=>document.body.classList.add('sidebar-open'));
  $('sidebarScrim').addEventListener('click',()=>document.body.classList.remove('sidebar-open'));
  $('refreshButton').addEventListener('click',()=>state.page==='settings'?refreshPrinterStatus():state.page==='trash'?loadTrash():loadLibrary());
  $('searchInput').addEventListener('input',()=>{state.search=$('searchInput').value;renderLibrary();});
  $('collectionFilter').addEventListener('change',()=>{state.collection=$('collectionFilter').value;renderNavigation();renderLibrary();});
  $('tagFilter').addEventListener('change',()=>{state.tag=$('tagFilter').value;renderLibrary();});
  $('sortSelect').addEventListener('change',()=>{state.sort=$('sortSelect').value;renderLibrary();});
  document.querySelectorAll('#kindFilters button').forEach(element=>element.addEventListener('click',()=>{state.kind=element.dataset.kind;renderFilters();renderLibrary();}));
  $('addModelsButton').addEventListener('click',()=>openUpload());
  $('uploadDropzone').addEventListener('click',()=>{if(!state.uploadBusy)$('modelFilesInput').click();});
  $('uploadDropzone').addEventListener('keydown',event=>{if(['Enter',' '].includes(event.key)){event.preventDefault();if(!state.uploadBusy)$('modelFilesInput').click();}});
  $('modelFilesInput').addEventListener('change',event=>{addUploadFiles(event.target.files);event.target.value='';});
  $('uploadForm').addEventListener('submit',uploadModels);
  $('uploadModal').addEventListener('cancel',event=>{if(state.uploadBusy)event.preventDefault();});
  document.querySelectorAll('[data-close]').forEach(element=>element.addEventListener('click',()=>{if(element.dataset.close==='uploadModal'&&state.uploadBusy)return;$(element.dataset.close).close();}));
  $('uploadDropzone').addEventListener('dragover',event=>{event.preventDefault();event.stopPropagation();$('uploadDropzone').classList.add('drag-over');});
  $('uploadDropzone').addEventListener('dragleave',()=> $('uploadDropzone').classList.remove('drag-over'));
  $('uploadDropzone').addEventListener('drop',event=>{event.preventDefault();event.stopPropagation();$('uploadDropzone').classList.remove('drag-over');addUploadFiles(event.dataTransfer.files);});
  let dragDepth=0;
  const filesDragged=event=>Array.from(event.dataTransfer?.types||[]).includes('Files');
  document.addEventListener('dragenter',event=>{if(!filesDragged(event))return;event.preventDefault();dragDepth++;if(state.ready&&!document.querySelector('dialog[open]')&&!state.uploadBusy)$('globalDropOverlay').hidden=false;});
  document.addEventListener('dragover',event=>{if(filesDragged(event))event.preventDefault();});
  document.addEventListener('dragleave',event=>{if(!filesDragged(event))return;dragDepth=Math.max(0,dragDepth-1);if(!dragDepth)$('globalDropOverlay').hidden=true;});
  document.addEventListener('drop',event=>{
    if(!filesDragged(event))return;event.preventDefault();dragDepth=0;$('globalDropOverlay').hidden=true;
    if(!state.ready||state.uploadBusy)return;
    if($('modelModal').open){if(state.tab==='jobs'&&event.dataTransfer.files.length===1)uploadJob(event.dataTransfer.files[0]);else toast('To attach a sliced print file, open the Print jobs tab and drop one .gcode.3mf file.','error');return;}
    if($('uploadModal').open)addUploadFiles(event.dataTransfer.files);else openUpload(event.dataTransfer.files);
  });
  window.addEventListener('blur',()=>{dragDepth=0;$('globalDropOverlay').hidden=true;});
  $('closeModelButton').addEventListener('click',()=> $('modelModal').close());
  $('modelModal').addEventListener('close',()=>{state.modelLoadId++;state.viewer?.dispose();state.viewer=null;state.model=null;state.jobs=[];state.previewOptInId=null;if($('studioModal').open)$('studioModal').close();});
  $('studioModal').addEventListener('close',()=>{state.studioLoadId++;$('studioLaunchLink').hidden=true;$('studioLaunchLink').removeAttribute('href');});
  $('openStudioButton').addEventListener('click',()=>prepareStudio(state.model));
  document.querySelectorAll('.model-tabs button').forEach(element=>element.addEventListener('click',()=>switchTab(element.dataset.tab)));
  document.querySelector('.model-tabs').addEventListener('keydown',event=>{
    if(!['ArrowLeft','ArrowRight','Home','End'].includes(event.key))return;
    event.preventDefault();const tabs=['preview','details','jobs'];let index=tabs.indexOf(state.tab);
    index=event.key==='Home'?0:event.key==='End'?2:(index+(event.key==='ArrowRight'?1:2))%3;
    switchTab(tabs[index]);document.querySelector(`[data-tab="${tabs[index]}"]`).focus();
  });
  $('loadLargePreviewButton').addEventListener('click',()=>{
    if(!state.model || !needsPreviewOptIn(state.model))return;
    state.previewOptInId=state.model.id;
    showViewerStatus('loading');
    ensureViewer();
  });
  $('viewerReset').addEventListener('click',()=>state.viewer?.resetView());
  $('viewerGrid').addEventListener('click',()=>{const active=$('viewerGrid').getAttribute('aria-pressed')!=='true';$('viewerGrid').setAttribute('aria-pressed',String(active));$('viewerGrid').classList.toggle('active',active);state.viewer?.setGridVisible(active);});
  $('viewerWireframe').addEventListener('click',()=>{const active=$('viewerWireframe').getAttribute('aria-pressed')!=='true';$('viewerWireframe').setAttribute('aria-pressed',String(active));$('viewerWireframe').classList.toggle('active',active);state.viewer?.setWireframe(active);});
  $('viewerColor').addEventListener('input',()=>state.viewer?.setColor($('viewerColor').value));
  $('rebuildThumbnailButton').addEventListener('click',rebuildCurrentThumbnail);
  $('metadataForm').addEventListener('submit',saveMetadata);
  $('deleteModelButton').addEventListener('click',deleteModel);
  $('attachJobButton').addEventListener('click',()=>$('jobFileInput').click());
  $('jobFileInput').addEventListener('change',event=>uploadJob(event.target.files[0]));
  $('printerForm').addEventListener('submit',async event=>{event.preventDefault();setBusy($('savePrinterButton'),true,'Saving…');try{await savePrinter();}finally{setBusy($('savePrinterButton'),false);}});
  $('printerMode').addEventListener('change',updatePrinterModeFields);
  $('testPrinterButton').addEventListener('click',testPrinter);
  $('refreshPrinterButton').addEventListener('click',()=>refreshPrinterStatus());
  $('resetTrustButton').addEventListener('click',resetPrinterTrust);
  $('confirmCancel').addEventListener('click',()=>$('confirmModal').close('cancel'));
  $('confirmAccept').addEventListener('click',()=>$('confirmModal').close('confirm'));
  document.addEventListener('keydown',event=>{
    if(event.key==='/'&&state.ready&&state.page!=='settings'&&!document.querySelector('dialog[open]')&&!['INPUT','TEXTAREA','SELECT'].includes(document.activeElement?.tagName)){event.preventDefault();$('searchInput').focus();}
    if(event.key==='Escape')document.body.classList.remove('sidebar-open');
  });
}

hydrateIcons();
bindEvents();
bootstrap();
