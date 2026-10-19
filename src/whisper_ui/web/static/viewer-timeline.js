/* Waveform scrubber: bounded server-side peaks, accessible native range control,
 * and no duplicate media downloads or untrusted HTML. */
(function () {
  'use strict';
  const formatTime = value => {
    if (!Number.isFinite(value) || value < 0) return '00:00';
    const t = Math.floor(value), h = Math.floor(t / 3600), m = Math.floor(t / 60) % 60, s = t % 60;
    return (h ? `${h}:` + String(m).padStart(2, '0') : String(m).padStart(2, '0')) + ':' + String(s).padStart(2, '0');
  };
  function init({ player, canvas, range, status, url, duration, onSeek, onClock }) {
    if (!player || !canvas || !range) return { destroy() {} };
    let peaks = [], destroyed = false, raf = 0, total = Math.max(0, duration || 0);
    const ctx = canvas.getContext('2d');
    if (!ctx) return { destroy() {} };
    function draw() {
      if (destroyed || !canvas.isConnected) return;
      const rect = canvas.getBoundingClientRect();
      const width = rect.width || 600, height = rect.height || 84;
      const dpr = Math.min(2, window.devicePixelRatio || 1);
      const pw = Math.round(width * dpr), ph = Math.round(height * dpr);
      if (canvas.width !== pw || canvas.height !== ph) { canvas.width = pw; canvas.height = ph; }
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, width, height);
      const progress = total ? Math.max(0, Math.min(1, player.currentTime / total)) : 0;
      const line = getComputedStyle(canvas).color || '#20bca9';
      const barWidth = Math.max(1.5, width / Math.max(1, peaks.length));
      if (peaks.length) {
        const center = height / 2;
        for (let x = 0; x < width; x += 3) {
          const v = peaks[Math.min(peaks.length - 1, Math.floor(x / width * peaks.length))] || 0;
          const amp = Math.max(2, v * height * 0.43);
          ctx.fillStyle = x / width <= progress ? line : 'rgba(135,155,175,.48)';
          ctx.fillRect(x, center - amp, Math.max(2, Math.min(2.4, barWidth)), amp * 2);
        }
      } else {
        ctx.fillStyle = 'rgba(135,155,175,.32)';
        ctx.fillRect(0, height / 2 - 2, width, 4);
      }
      ctx.fillStyle = line;
      ctx.fillRect(Math.max(0, Math.min(width - 2, width * progress - 1)), 0, 2, height);
    }
    function refresh() {
      total = Number.isFinite(player.duration) && player.duration > 0 ? player.duration : total;
      range.max = String(Math.max(0.01, total));
      range.value = String(Math.min(total, player.currentTime || 0));
      range.disabled = total <= 0;
      if (typeof onClock === 'function') onClock(player.currentTime || 0, total);
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(draw);
    }
    function scrub() {
      const value = Number(range.value);
      if (!Number.isFinite(value) || !total) return;
      player.currentTime = Math.max(0, Math.min(value, total));
      if (typeof onSeek === 'function') onSeek(player.currentTime);
      refresh();
    }
    const resize = typeof ResizeObserver !== 'undefined' ? new ResizeObserver(draw) : null;
    if (resize) resize.observe(canvas);
    player.addEventListener('timeupdate', refresh);
    player.addEventListener('durationchange', refresh);
    player.addEventListener('loadedmetadata', refresh);
    player.addEventListener('seeked', refresh);
    range.addEventListener('input', scrub);
    if (status) status.textContent = 'Caricamento forma d’onda…';
    fetch(url, { credentials: 'same-origin' }).then(response => {
      if (!response.ok) throw new Error('waveform unavailable');
      return response.json();
    }).then(data => {
      if (destroyed) return;
      peaks = Array.isArray(data.peaks) ? data.peaks.map(Number).filter(Number.isFinite) : [];
      if (status) status.textContent = peaks.length ? 'Forma d’onda reale · clicca o trascina per navigare' : 'Navigazione disponibile · forma d’onda vuota';
      draw();
    }).catch(() => {
      if (!destroyed && status) status.textContent = 'Navigazione disponibile · forma d’onda non presente';
      draw();
    });
    refresh();
    return { formatTime, refresh, destroy() {
      destroyed = true; cancelAnimationFrame(raf);if (resize) resize.disconnect();
      player.removeEventListener('timeupdate', refresh);
      player.removeEventListener('durationchange', refresh);
      player.removeEventListener('loadedmetadata', refresh);
      player.removeEventListener('seeked', refresh);
      range.removeEventListener('input', scrub);
    } };
  }
  window.WhisperTimeline = { init, formatTime };
})();
