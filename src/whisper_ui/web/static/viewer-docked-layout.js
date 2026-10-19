/* Transcript Studio: fit the player and text tools above one scrollable pane.
 * Never lock the page unless enough viewport space remains for the transcript.
 * This prevents clipping controls on phones and short browser windows. */
(function () {
  'use strict';
  function init() {
    const page = document.getElementById('page-content');
    const workspace = page?.querySelector('.viewer-workspace:has(.viewer-transcript-frame)');
    const dock = workspace?.querySelector('.viewer-controls-fixed');
    const head = workspace?.querySelector('.viewer-transcript-head');
    if (!page || !workspace || !dock || !head) return {destroy() {}};
    let raf = 0, disposed = false;
    const mobile = () => matchMedia('(max-width:767px)').matches;
    function measure() {
      if (disposed) return;
      const viewport = window.visualViewport?.height || window.innerHeight;
      const usable = viewport - (mobile() ? 64 : 0);
      // The dock and metadata have natural (unclipped) dimensions.
      // Include everything before the editor, and guarantee room for text.
      const prelude = workspace.getBoundingClientRect().top - page.getBoundingClientRect().top;
      const dockBox = dock.getBoundingClientRect();
      const headBox = head.getBoundingClientRect();
      const minTranscript = mobile() ? 200 : 220;
      const needed = Math.max(0, prelude) + dockBox.height + headBox.height + minTranscript + 14;
      const fit = needed <= usable;
      page.classList.toggle('viewer-page-fit', fit);
    }
    const schedule = () => {
      if (!disposed && !raf) raf = requestAnimationFrame(() => { raf = 0; measure(); });
    };
    const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(schedule);
    // Details panels, zoom settings or responsive text wrapping may change
    // the height of the controls after initial render.
    observer?.observe(page);
    observer?.observe(dock);
    for (const item of page.children) {
      if (item !== workspace) observer?.observe(item);
    }
    window.addEventListener('resize', schedule);
    window.visualViewport?.addEventListener('resize', schedule);
    // iframe/browser fullscreen and open keyboard should never leave a stuck
    // overflow lock when this template is detached by HTMX navigation.
    schedule();
    return {destroy() {
      disposed = true;
      cancelAnimationFrame(raf);
      observer?.disconnect();
      window.removeEventListener('resize', schedule);
      window.visualViewport?.removeEventListener('resize', schedule);
      page.classList.remove('viewer-page-fit');
    }};
  }
  window.WhisperDockedLayout = {init};
})();
