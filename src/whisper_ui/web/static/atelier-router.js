/* Atelier client-side navigation — progressive enhancement of server-rendered
 * pages. Keeps the authenticated shell, theme, menu, and toast store alive.
 * Non-page actions (POSTs, downloads, external links, files) remain native. */
(function () {
  'use strict';
  const ROOT = '#page-content';
  const pageRoute = (path) => (
    path === '/' || path === '/upload' || path === '/jobs' ||
    path === '/presets' || path === '/presets/new' ||
    path === '/admin/users' || path === '/admin/jobs' ||
    /^\/presets\/[^/]+\/edit$/.test(path) ||
    /^\/(viewer|studio)\/[^/]+$/.test(path)
  );
  const navName = (path) => {
    if (path === '/') return 'dashboard';
    if (path.startsWith('/upload')) return 'upload';
    if (path.startsWith('/presets')) return 'presets';
    if (path.startsWith('/admin/users')) return 'admin_users';
    if (path.startsWith('/admin/jobs')) return 'admin_jobs';
    if (path.startsWith('/jobs') || path.startsWith('/viewer') || path.startsWith('/studio')) return 'jobs';
    return '';
  };
  let pending = false;
  let pendingUrl = null;
  let pendingHistory = false;
  let originalTitle = document.title;
  function updateNav() {
    const active = navName(location.pathname);
    const store = window.Alpine && window.Alpine.store('nav');
    if (store) store.activePage = active;
    document.querySelectorAll('.atelier-navigation a, .atelier-mobile-nav a').forEach(a => {
      if (navName(new URL(a.href).pathname) === active && active) {
        if (a.getAttribute('href') === location.pathname ||
            (active === 'jobs' && a.getAttribute('href') === '/jobs') ||
            (active === 'presets' && a.getAttribute('href') === '/presets')) a.setAttribute('aria-current','page');
        else a.removeAttribute('aria-current');
      } else a.removeAttribute('aria-current');
    });
    const main = document.getElementById('main');
    if (main) main.dataset.view = active;
  }
  function stopLoading() {
    pending = false;
    pendingUrl = null;
    pendingHistory = false;
    document.documentElement.classList.remove('atelier-navigating');
    const page = document.querySelector(ROOT);
    page?.removeAttribute('aria-busy');
  }
  function navigate(url, historyMode = 'push') {
    if (!window.htmx || pending || !document.querySelector(ROOT)) return false;
    pending = true;
    pendingUrl = url;
    pendingHistory = historyMode === 'push';
    document.documentElement.classList.add('atelier-navigating');
    document.querySelector(ROOT).setAttribute('aria-busy','true');
    // htmx owns history and scripts; page GET returns a full HTML document,
    // but only #page-content is swapped. The navbar is never recreated.
    try {
      htmx.ajax('GET', url, {
        target: ROOT, select: ROOT, swap: 'outerHTML show:top',
        headers: {'X-Atelier-Navigation':'1'}
      });
    } catch (_error) {
      stopLoading();
      location.assign(url); // Progressive-enhancement fallback.
    }
    return true;
  }
  document.addEventListener('click', function (event) {
    if (event.defaultPrevented || event.button !== 0 || event.ctrlKey || event.metaKey ||
        event.shiftKey || event.altKey) return;
    const anchor = event.target.closest('a[href]');
    if (!anchor || anchor.hasAttribute('download') || anchor.target && anchor.target !== '_self' ||
        anchor.closest('[data-native-navigation], [contenteditable]') ||
        anchor.hasAttribute('hx-get') || anchor.hasAttribute('hx-post') ||
        anchor.hasAttribute('hx-delete') || anchor.hasAttribute('hx-boost')) return;
    let url;
    try { url = new URL(anchor.href, location.href); } catch (_) { return; }
    if (url.origin !== location.origin || !pageRoute(url.pathname) || url.hash &&
        url.pathname === location.pathname && url.search === location.search) return;
    if (pending) { event.preventDefault(); return; }
    if (url.pathname === location.pathname && url.search === location.search) {
      event.preventDefault(); return;
    }
    if (navigate(url.pathname + url.search + url.hash)) event.preventDefault();
  }, true);

  document.addEventListener('htmx:beforeSwap', (event) => {
    if (pending && event.detail?.target?.id === 'page-content' && event.detail.shouldSwap) {
      document.dispatchEvent(new Event('atelier:leaving'));
    }
  });
  document.addEventListener('htmx:afterSwap', (event) => {
    const node = event.detail && event.detail.target;
    if (!node || node.id !== 'page-content') return;
    const destination = pendingUrl;
    const push = pendingHistory;
    stopLoading();
    if (push && destination && destination !== location.pathname + location.search + location.hash) {
      history.pushState({atelier:true}, '', destination);
    }
    updateNav();
    const heading = node.querySelector('h1');
    if (heading) document.title = heading.textContent.trim().replace(/\s+/g,' ').slice(0,90) + ' · Atelier';
    else document.title = originalTitle;
    // Focus the new screen without scrolling a second time for keyboard users.
    node.setAttribute('tabindex','-1');
    node.focus({preventScroll:true});
    window.scrollTo({top:0,behavior:'instant'});
    document.dispatchEvent(new CustomEvent('atelier:navigated', {detail:{path:location.pathname}}));
  });
  document.addEventListener('htmx:responseError', (event) => {
    if (pending && event.detail && event.detail.elt && event.detail.elt.id === 'page-content') {
      const url = pendingUrl; stopLoading();
      if (url) location.assign(url);
    }
  });
  document.addEventListener('htmx:sendError', (event) => {
    if (pending && event.detail && event.detail.elt && event.detail.elt.id === 'page-content') stopLoading();
  });
  // Manual history management is used for htmx.ajax: it swaps a selected
  // fragment but does not automatically push a URL without an hx-push-url
  // attribute on the triggering element.
  window.addEventListener('popstate', () => {
    stopLoading();
    if (pageRoute(location.pathname)) navigate(location.pathname + location.search + location.hash, 'none');
    else location.reload();
  });
  document.addEventListener('DOMContentLoaded', updateNav);
  window.AtelierNavigation = {isPageRoute:pageRoute, navigate, updateNav};
})();
