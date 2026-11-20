/* Keep the real GitHub footer after the complete workspace on small screens. */
(() => {
  'use strict';
  const shell = document.querySelector('.app-shell');
  const main = document.querySelector('.main');
  const footer = main?.querySelector('.studio-footer');
  if (!shell || !main || !footer) return;
  const originalSlot = document.createComment('Studio footer desktop position');
  footer.before(originalSlot);
  const mobile = window.matchMedia('(max-width: 860px)');
  const arrange = () => {
    if (mobile.matches) {
      footer.dataset.themePageFooter = '';
      shell.append(footer);
    } else {
      delete footer.dataset.themePageFooter;
      originalSlot.after(footer);
    }
  };
  arrange();
  mobile.addEventListener('change', arrange);
  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta) meta.content = '#f3eef8';
})();
