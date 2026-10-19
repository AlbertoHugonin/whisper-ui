/* Transcript Studio timeline: a zoomable and pannable audio waveform, ruler,
   always-visible playhead, full-length minimap and keyboard-accessible scrubber.
   The HTMLMediaElement is the SINGLE source of truth for clock and playback. */
(function () {
  'use strict';
  const clamp = (n, min, max) => Math.max(min, Math.min(max, n));
  function formatTime(value) {
    const t = Number.isFinite(value) ? Math.max(0, Math.floor(value)) : 0;
    const h = Math.floor(t / 3600), m = Math.floor(t / 60) % 60, s = t % 60;
    return (h ? `${h}:` + String(m).padStart(2, '0') : String(m).padStart(2, '0')) + ':' + String(s).padStart(2, '0');
  }
  function init({ player, canvas, overview, range, status, url, duration, segments = [], onSeek, onClock, onWindow }) {
    if (!player || !canvas || !range) return { destroy() {}, zoom() {}, pan() {}, fit() {}, setSource() {} };
    const ctx = canvas.getContext('2d');
    const octx = overview ? overview.getContext('2d') : null;
    if (!ctx) return { destroy() {}, zoom() {}, pan() {}, fit() {}, setSource() {} };
    let points = [], generation = 0, destroyed = false;
    let total = Math.max(0, Number(duration) || 0), zoom = 1, start = 0, frame = 0;
    const shell = canvas.closest('.viewer-waveform-wrap');
    const full = () => Math.max(.001, total);
    const visible = () => full() / zoom;
    const bound = () => { start = clamp(start, 0, Math.max(0, full() - visible())); };
    const fit = () => { zoom = 1; start = 0; update(); };
    function changeZoom(factor, anchor = player.currentTime || 0) {
      if (!total) return;
      const anchorRatio = clamp((anchor - start) / visible(), 0, 1);
      zoom = clamp(zoom * factor, 1, 64);
      start = anchor - anchorRatio * visible();
      bound(); update();
    }
    function pan(fraction) { start += visible() * fraction; bound(); update(); }
    function canvasSize(c, context) {
      // Never derive canvas size from its own intrinsic bitmap dimensions.
      // On an older/cached stylesheet that creates a feedback loop:
      // setting canvas.height enlarges the element, then ResizeObserver
      // measures that larger element, eventually producing a giant blank page.
      const isOverview = c === overview;
      const host = isOverview ? c.parentElement : shell;
      const rawWidth = host?.getBoundingClientRect().width || window.innerWidth || 600;
      const w = clamp(Math.floor(rawWidth - (isOverview ? 0 : 2)), 2, 4096);
      const h = isOverview ? 32 : (window.matchMedia('(max-width:680px)').matches ? 87 : 106);
      // Explicit CSS pixels protect even if the browser has cached old CSS.
      c.style.display = 'block';
      c.style.boxSizing = 'border-box';
      c.style.width = '100%';
      c.style.maxWidth = '100%';
      c.style.height = h + 'px';
      c.style.minHeight = '0';
      c.style.maxHeight = h + 'px';
      const d = Math.min(2, Math.max(1, window.devicePixelRatio || 1));
      const iw = Math.min(4096, Math.round(w * d));
      const ih = Math.round(h * d);
      if (c.width !== iw) c.width = iw;
      if (c.height !== ih) c.height = ih;
      // Use the actual backing-store scale if width was capped.
      context.setTransform(iw / w, 0, 0, ih / h, 0, 0);
      context.clearRect(0, 0, w, h);
      return { w, h };
    }
    function drawPeaks(context, w, h, left, right, compact) {
      const foreground = '#28b9ad';
      const middle = (compact ? h : h - 17) / 2;
      const extent = (compact ? h : h - 17) * .46;
      if (!points.length) {
        context.fillStyle = 'rgba(144,160,179,.3)';
        context.fillRect(0, middle - 1, w, 2);
        return;
      }
      for (let x = 0; x < w; x += 2) {
        const t0 = left + x / w * (right - left);
        const t1 = left + (x + 2) / w * (right - left);
        const a = clamp(Math.floor(t0 / full() * points.length), 0, points.length - 1);
        const b = clamp(Math.ceil(t1 / full() * points.length), a + 1, points.length);
        let v = 0;
        for (let k = a; k < b; k++) v = Math.max(v, points[k] || 0);
        const amp = Math.max(1.25, v * extent);
        context.fillStyle = t0 <= player.currentTime ? foreground : 'rgba(135,156,180,.69)';
        context.fillRect(x, middle - amp, 1.6, amp * 2);
      }
    }
    function ruler(context, w, h) {
      const span = visible();
      const target = Math.max(1, span / Math.max(2, Math.floor(w / 108)));
      const intervals = [1,2,5,10,15,30,60,120,300,600,900,1800,3600,7200];
      const step = intervals.find(x => x >= target) || 14400;
      context.fillStyle = 'rgba(180,198,220,.8)';
      context.font = '10px ui-monospace, SFMono-Regular, monospace';
      for (let t = Math.ceil(start / step) * step; t <= start + span; t += step) {
        const x = (t - start) / span * w;
        context.fillRect(x, h - 17, 1, 5);
        context.fillText(formatTime(t), x + 3, h - 3);
      }
    }
    function draw() {
      if (destroyed || !canvas.isConnected) return;
      const { w, h } = canvasSize(canvas, ctx);
      drawPeaks(ctx, w, h, start, start + visible(), false);
      // Speaker boundaries are understated waveform markers, not an overlay
      // that steals scrolling or clicks from the scrubber.
      for (const seg of segments) {
        if (seg.start < start || seg.start > start + visible()) continue;
        const x = (seg.start - start) / visible() * w;
        ctx.fillStyle = 'rgba(242,185,91,.36)';ctx.fillRect(x, 0, 1, h - 18);
      }
      ruler(ctx, w, h);
      const playhead = (player.currentTime - start) / visible() * w;
      if (playhead >= 0 && playhead <= w) {
        ctx.fillStyle = '#ffbf67';ctx.fillRect(playhead - 1, 0, 2, h - 16);
        ctx.beginPath();ctx.moveTo(playhead - 5, 0);ctx.lineTo(playhead + 5, 0);ctx.lineTo(playhead, 7);ctx.fill();
      }
      if (overview && octx) {
        const dim = canvasSize(overview, octx);
        drawPeaks(octx, dim.w, dim.h, 0, full(), true);
        const x = start / full() * dim.w, width = visible() / full() * dim.w;
        octx.fillStyle = 'rgba(37,176,171,.14)';octx.fillRect(x, 0, width, dim.h);
        octx.strokeStyle = 'rgba(66,216,202,.9)';octx.strokeRect(x+.5,.5,Math.max(0,width-1),dim.h-1);
      }
    }
    function update() {
      if (destroyed) return;
      const end = start + visible();
      range.min = String(start);range.max = String(Math.max(start+.001,end));
      range.value = String(clamp(player.currentTime || 0,start,end));
      range.disabled = !total;
      if (onClock) onClock(player.currentTime || 0,total);
      if (onWindow) onWindow(formatTime(start),formatTime(end),zoom);
      cancelAnimationFrame(frame);frame=requestAnimationFrame(draw);
    }
    function clockUpdate() {
      const mediaDuration = Number.isFinite(player.duration) && player.duration>0 ? player.duration : 0;
      if(mediaDuration && (!total || Math.abs(total-mediaDuration)>1)) { total=mediaDuration;bound(); }
      if(player.currentTime > start + visible() && zoom>1) {start=player.currentTime-visible()*.15;bound();}
      if(player.currentTime < start && zoom>1) {start=player.currentTime-visible()*.15;bound();}
      update();
    }
    function scrub() {
      if (!total) return;
      player.currentTime = clamp(Number(range.value),0,total);
      onSeek?.(player.currentTime);
      clockUpdate();
    }
    function scrollOverview(event) {
      if (!total || zoom===1) return;
      const r=overview.getBoundingClientRect();
      start=clamp(((event.clientX-r.left)/r.width)*full()-visible()/2,0,full()-visible());
      update();
    }
    function wheel(event) {
      if (!total) return;
      if(event.ctrlKey || event.metaKey) {
        event.preventDefault();changeZoom(event.deltaY>0?1/1.35:1.35);
      } else if (zoom>1 && (event.shiftKey || Math.abs(event.deltaX)>1)) {
        event.preventDefault();pan((event.deltaX||event.deltaY)/Math.max(150,shell.clientWidth));
      }
    }
    let abort=null;
    function setSource(nextUrl) {
      const thisGeneration=++generation;
      abort?.abort();abort=new AbortController();
      points=[];update();
      if(status)status.textContent='Analisi forma d’onda in corso…';
      fetch(nextUrl,{credentials:'same-origin',signal:abort.signal}).then(r=>{
        if(!r.ok)throw new Error('HTTP '+r.status);
        return r.json();
      }).then(d=>{
        if(destroyed||generation!==thisGeneration)return;
        points=Array.isArray(d.peaks)?d.peaks.map(v=>clamp(Number(v)||0,0,1)):[];
        if(status)status.textContent=points.length?'Forma d’onda · Zoom +/−, trascina il cursore, scorri con Shift + rotella':'Forma d’onda vuota';
        update();
      }).catch(error=>{
        if(destroyed||generation!==thisGeneration||error.name==='AbortError')return;
        if(status)status.textContent='Forma d’onda non disponibile · la ricerca temporale funziona comunque';
        update();
      });
    }
    const resize = typeof ResizeObserver!=='undefined'?new ResizeObserver(update):null;
    resize?.observe(shell || canvas);
    if (overview?.parentElement && overview.parentElement !== shell) resize?.observe(overview.parentElement);
    player.addEventListener('timeupdate',clockUpdate);
    player.addEventListener('durationchange',clockUpdate);
    player.addEventListener('loadedmetadata',clockUpdate);
    player.addEventListener('seeked',clockUpdate);
    range.addEventListener('input',scrub);
    shell?.addEventListener('wheel',wheel,{passive:false});
    overview?.addEventListener('click',scrollOverview);
    setSource(url);
    clockUpdate();
    return {
      formatTime, zoom:changeZoom, pan, fit, setSource, refresh:clockUpdate,
      destroy() {
        destroyed=true;abort?.abort();cancelAnimationFrame(frame);
        resize?.disconnect();
        for(const ev of ['timeupdate','durationchange','loadedmetadata','seeked'])player.removeEventListener(ev,clockUpdate);
        range.removeEventListener('input',scrub);
        shell?.removeEventListener('wheel',wheel);
        overview?.removeEventListener('click',scrollOverview);
      }
    };
  }
  window.WhisperTimeline={init,formatTime};
})();
