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
  const originalTitle = document.title;
  let pendingRequest = null;
  let generation = 0;
  const isNavigationRequest = event =>
    event.detail?.requestConfig?.headers?.['X-Atelier-Navigation'] === '1';
  const isCurrentRequest = event => pending && isNavigationRequest(event) &&
    event.detail.xhr === pendingRequest;
  function navigationFailed() {
    stopLoading();
    window.Alpine?.store('toasts')?.add({type:'error',
      message:'Connessione interrotta. Riprova a selezionare la pagina.'});
  }
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
    document.querySelectorAll('.atelier-account-menu[open]').forEach(menu => menu.removeAttribute('open'));
    const main = document.getElementById('main');
    if (main) main.dataset.view = active;
  }
  function stopLoading() {
    pending = false;
    pendingUrl = null;
    pendingHistory = false;
    pendingRequest = null;
    document.documentElement.classList.remove('atelier-navigating');
    const page = document.querySelector(ROOT);
    page?.removeAttribute('aria-busy');
  }
  function navigate(url, historyMode = 'push') {
    if (!window.htmx || pending || !document.querySelector(ROOT)) return false;
    const currentGeneration = ++generation;
    const source = document.querySelector(ROOT);
    pending = true;
    pendingUrl = url;
    pendingHistory = historyMode === 'push';
    document.documentElement.classList.add('atelier-navigating');
    document.querySelector(ROOT).setAttribute('aria-busy','true');
    // htmx owns history and scripts; page GET returns a full HTML document,
    // but only #page-content is swapped. The navbar is never recreated.
    try {
      htmx.ajax('GET', url, {
        source,
        target: ROOT, select: ROOT, swap: 'outerHTML show:top',
        headers: {'X-Atelier-Navigation':'1'}
      }).catch(() => {
        if (pending && generation === currentGeneration) navigationFailed();
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

  document.addEventListener('htmx:beforeRequest', event => {
    if (pending && isNavigationRequest(event)) pendingRequest = event.detail.xhr;
  });
  document.addEventListener('htmx:beforeOnLoad', event => {
    if (!isNavigationRequest(event)) return;
    // Back/forward navigation may supersede a request still in flight.
    if (!isCurrentRequest(event)) { event.preventDefault(); return; }
    const xhr = event.detail.xhr;
    if (xhr.status >= 200 && xhr.status < 300 && xhr.status !== 204) {
      const response = new DOMParser().parseFromString(xhr.responseText, 'text/html');
      if (!response.querySelector(ROOT)) {
        // Session expiry redirects to a complete login page, not a fragment.
        event.preventDefault();
        const destination = xhr.responseURL || pendingUrl;
        stopLoading();
        location.assign(destination);
      }
    }
  });
  document.addEventListener('htmx:beforeSwap', (event) => {
    if (isCurrentRequest(event) && event.detail?.target?.id === 'page-content' && event.detail.shouldSwap) {
      document.dispatchEvent(new Event('atelier:leaving'));
    }
  });
  document.addEventListener('htmx:afterSwap', (event) => {
    const node = event.detail && event.detail.target;
    if (!node || node.id !== 'page-content') return;
    if (!isCurrentRequest(event)) { updateNav(); return; }
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
    if (isCurrentRequest(event)) {
      const url = pendingUrl; stopLoading();
      if (url) location.assign(url);
    }
  });
  for (const eventName of ['htmx:sendError', 'htmx:timeout']) {
    document.addEventListener(eventName, event => {
      if (isCurrentRequest(event)) navigationFailed();
    });
  }
  document.addEventListener('htmx:afterRequest', event => {
    if (isCurrentRequest(event) && event.detail.xhr.status === 204) stopLoading();
  });
  // Manual history management is used for htmx.ajax: it swaps a selected
  // fragment but does not automatically push a URL without an hx-push-url
  // attribute on the triggering element.
  window.addEventListener('popstate', () => {
    const superseded = pendingRequest;
    ++generation;
    stopLoading();
    superseded?.abort();
    if (pageRoute(location.pathname)) navigate(location.pathname + location.search + location.hash, 'none');
    else location.reload();
  });
  document.addEventListener('DOMContentLoaded', updateNav);
  window.AtelierNavigation = {isPageRoute:pageRoute, navigate, updateNav};
})();
