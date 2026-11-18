/* Shared LAN backup button. Credentials and source paths remain on the NAS. */
(() => {
  'use strict';
  if (window.__launchpadBackupClient) return;
  window.__launchpadBackupClient = true;
  const script = document.currentScript;
  const style = document.createElement('link');
  style.rel = 'stylesheet';
  style.href = new URL('site-backup.css', script.src).href;
  document.head.append(style);
  let active = false;
  let timer = null;
  let dialog = null;
  let heading, detail, progress, locationLine, closeButton;
  const make = (tag, className, text) => {
    const el = document.createElement(tag);
    if (className) el.className = className;
    if (text) el.textContent = text;
    return el;
  };
  function panel() {
    if (dialog) return;
    dialog = make('dialog', 'lp-backup-dialog');
    dialog.setAttribute('aria-labelledby', 'lp-backup-heading');
    heading = make('h2', '', 'Google Drive backup');
    heading.id = 'lp-backup-heading';
    detail = make('p', 'lp-backup-detail');
    detail.setAttribute('role', 'status');
    progress = make('progress', 'lp-backup-progress');
    progress.max = 100;
    progress.setAttribute('aria-label', 'Backup progress');
    locationLine = make('p', 'lp-backup-location');
    closeButton = make('button', 'lp-backup-close', 'Close');
    closeButton.type = 'button';
    closeButton.addEventListener('click', () => dialog.close());
    dialog.append(heading, detail, progress, locationLine, closeButton);
    dialog.addEventListener('close', () => { if (timer) clearTimeout(timer); timer = null; active = false; });
    document.body.append(dialog);
  }
  function originFor(button) {
    const raw = button.dataset.backupOrigin || script.dataset.backupOrigin;
    if (!raw) throw new Error('No backup service has been configured.');
    const url = new URL(raw);
    if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password || url.pathname !== '/' || url.search || url.hash) throw new Error('The backup service address is invalid.');
    return url.origin;
  }
  async function request(base, path, options = {}) {
    const controller = new AbortController();
    const deadline = setTimeout(() => controller.abort(), 15000);
    try {
      const response = await fetch(base + path, {credentials: 'omit', cache: 'no-store', ...options, signal: controller.signal});
      const data = await response.json();
      if (!response.ok) {
        if (response.status === 409 && data.active_job) return {job: data.active_job};
        throw new Error(data.error || 'The backup request could not be completed.');
      }
      return data;
    } catch (error) {
      if (error.name === 'AbortError' || error instanceof TypeError) throw new Error('The backup service is not reachable. Check the configured service address and connection.');
      throw error;
    } finally { clearTimeout(deadline); }
  }
  function showJob(job) {
    const labels = {queued: 'Waiting for the current backup to finish…', scanning: 'Checking the files…', archiving: 'Preparing the backup and restore instructions…', uploading: 'Uploading to Google Drive…', verifying: 'Checking the uploaded backup…', done: 'Backup uploaded and verified.'};
    detail.textContent = labels[job.stage] || labels[job.status] || job.message || 'Backup in progress…';
    progress.hidden = false;
    const value = Number(job.progress && typeof job.progress === 'object' ? job.progress.percent : job.progress);
    if (Number.isFinite(value)) progress.value = Math.max(0, Math.min(100, value));
    else progress.removeAttribute('value');
    if (job.status === 'complete') {
      heading.textContent = 'Backup verified';
      progress.value = 100;
      locationLine.textContent = 'Saved in Google Drive → Launchpad_Backups. The archive includes restore instructions and a file manifest.';
      active = false;
      return true;
    }
    if (['failed', 'interrupted'].includes(job.status)) {
      heading.textContent = 'Backup needs attention';
      detail.textContent = job.message || job.error || 'The backup did not finish. Its local files have been retained for review.';
      progress.hidden = true;
      locationLine.textContent = 'No successful upload is being claimed. Try again when the issue is resolved.';
      active = false;
      return true;
    }
    locationLine.textContent = 'You can close this panel. An accepted backup continues on the NAS.';
    return false;
  }
  async function poll(base, id) {
    try {
      const {job} = await request(base, '/api/backups/' + encodeURIComponent(id));
      if (!job || !job.id) throw new Error('The backup service returned an incomplete status.');
      if (!showJob(job) && dialog.open) timer = setTimeout(() => poll(base, id), 2500);
    } catch (error) { failed(error.message); }
  }
  function failed(message) {
    heading.textContent = 'Backup needs attention';
    detail.textContent = message;
    progress.hidden = true;
    locationLine.textContent = 'If a backup was already accepted, it may still be running. Click the button again to check it.';
    active = false;
  }
  async function start(button) {
    panel();
    if (!dialog.open) dialog.showModal();
    if (active) return;
    active = true;
    heading.textContent = 'Google Drive backup';
    detail.textContent = 'Checking the backup service…';
    progress.hidden = false;
    progress.removeAttribute('value');
    locationLine.textContent = '';
    try {
      const base = originFor(button);
      const siteId = button.dataset.siteBackup;
      const status = await request(base, '/api/status');
      const site = (status.sites || []).find(item => item.id === siteId);
      if (!site || !site.available) throw new Error('Backup sources for this website have not been configured. Check the installation report.');
      if (!status.configured) throw new Error('Connect Google Drive in the separately installed backup service, then try again.');
      if (site.active_job) {
        const current = typeof site.active_job === 'string' ? site.active_job : site.active_job.id;
        if (current) { await poll(base, current); return; }
      }
      const session = await request(base, '/api/session');
      const result = await request(base, '/api/backups', {method:'POST', headers:{'Content-Type':'application/json','X-Backup-Token':session.token}, body:JSON.stringify({site_id:siteId})});
      if (!result.job || !result.job.id) throw new Error('The backup service did not return a job number.');
      showJob(result.job);
      await poll(base, result.job.id);
    } catch (error) { failed(error.message || 'The backup could not be started.'); }
  }
  function setup() {
    let buttons = [...document.querySelectorAll('[data-site-backup]')];
    if (!buttons.length && script.dataset.siteId) {
      const button = make('button', 'lp-backup-floating', 'Back up to Google Drive');
      button.type = 'button';
      button.dataset.siteBackup = script.dataset.siteId;
      document.body.append(button);
      buttons = [button];
    }
    for (const button of buttons) {
      button.disabled = false;
      button.addEventListener('click', () => start(button));
    }
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', setup, {once:true});
  else setup();
})();
