/* One controller per page; audio objects stay outside Alpine's proxies. */
(function () {
  'use strict';
  const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  window.whisperCopy = async function (text) {
    if (navigator.clipboard?.writeText) {
      try { await navigator.clipboard.writeText(text); return; } catch (_) {}
    }
    const field = document.createElement('textarea');
    field.value = text; field.style.cssText = 'position:fixed;top:0;left:0;opacity:0';
    document.body.append(field); field.select();
    const copied = document.execCommand('copy'); field.remove();
    if (!copied) throw new Error('Clipboard unavailable');
  };
  window.viewerData = function () {
    let root, player, timeline, context, analyser, source, bins;
    let spectrumFrame = 0, spectrumOpen = false, disposed = false, sourceGeneration = 0;
    let keyboardIndex=-1;
    let timer, noticeTimer, manualUntil = 0, lastFollow = -1;
    const cleanups = [];
    const on = (el, event, fn) => { el?.addEventListener(event, fn); cleanups.push(() => el?.removeEventListener(event, fn)); };
    return {
      miniPlayer: false, audioExpanded: false, search: '', textSource: 'original', readingMode: false, shortcutsOpen: false,
      selectedIndex: -1, currentSegIdx: -1, currentTime: -1, clock: '00:00', clockTotal: '--:--',
      viewWindow: 'Vista completa', playing: false, followPlayback: true, audioSource: 'original',
      speed: '1', mediaError: '', notice: '', spectrumStatus: 'Avvia l’audio per vedere le frequenze.',
      _timeline: null,
      init() {
        root = this.$el; player = root.querySelector('#media-player');
        this.textSource = root.dataset.version; this.audioSource = root.dataset.source;
        this.clockTotal = window.WhisperTimeline.formatTime(Number(root.dataset.duration));
        if (player) {
          on(player, 'timeupdate', () => this.updateTime());
          on(player, 'play', () => { this.playing = true; this.drawSpectrum(); });
          on(player, 'pause', () => { this.playing = false; this.drawSpectrum(); });
          on(player, 'ended', () => { this.playing = false; this.drawSpectrum(); });
          on(player, 'error', () => { this.mediaError = 'Impossibile aprire questa traccia. Prova l’altra versione o scarica il file.'; });
          timeline = window.WhisperTimeline.init({
            player, canvas: root.querySelector('#viewer-waveform'), overview: root.querySelector('#viewer-overview'),
            range: root.querySelector('#viewer-seek'), status: root.querySelector('#viewer-waveform-status'),
            url: this.waveformUrl(), duration: Number(root.dataset.duration),
            onClock: (t,d) => { this.clock = window.WhisperTimeline.formatTime(t); this.clockTotal = window.WhisperTimeline.formatTime(d); },
            onSeek: () => this.updateTime(true),
            onWindow: (a,b,z) => { this.viewWindow = z>1.05 ? a+' – '+b+' · '+z.toFixed(1)+'×' : 'Vista completa'; }
          });
          this._timeline = timeline;
          if (window.IntersectionObserver) {
            const observer = new IntersectionObserver(entries => { this.miniPlayer = !entries[0].isIntersecting; }, {rootMargin:'-70px 0px -140px 0px', threshold:.2});
            observer.observe(root.querySelector('.wv-transport')); cleanups.push(() => observer.disconnect());
          }
        }
        if (root.querySelector('#studio-correction-status')) {
          let previous;
          const check = async () => {
            const box = root.querySelector('#studio-correction-status');
            try {
              const response = await fetch('/viewer/'+root.dataset.job+'/correction-status', {credentials:'same-origin'});
              if (!response.ok || disposed) return;
              const data = await response.json();
              if (disposed) return;
              const state = data.status;
              const button = root.querySelector('form[action$="/correct"] button');
              if (button) button.disabled = ['queued','started','deferred','scheduled'].includes(state);
              box.textContent = ({queued:'Correzione in coda',started:'Correzione in corso',finished:'Correzione terminata',failed:'Correzione fallita: puoi riprovare',not_requested:'Nessuna correzione richiesta',deferred:'Correzione in attesa',scheduled:'Correzione programmata'})[state] || 'Stato temporaneamente non disponibile';
              if (state==='finished' && previous && previous!=='finished') location.reload();
              previous = state;
            } catch (_) { if (!disposed) box.textContent = 'Stato temporaneamente non disponibile'; }
          };
          check(); timer = setInterval(check, 10000);
        }
      },
      waveformUrl() { return '/viewer/'+root.dataset.job+'/waveform?source='+this.audioSource; },
      rowText(row) { return row.dataset[this.textSource] || ''; },
      matches(row) { return !this.search || this.rowText(row).toLocaleLowerCase().includes(this.search.toLocaleLowerCase()); },
      highlight(row) {
        const text = this.rowText(row), q = this.search;
        if (!q) return escape(text);
        const regex = new RegExp(q.replace(/[.*+?^{}$()|[\]\\]/g, '\\$&'), 'gi');
        let end = 0, html = '';
        for (const match of text.matchAll(regex)) {
          html += escape(text.slice(end,match.index))+'<mark>'+escape(match[0])+'</mark>'; end = match.index+match[0].length;
        }
        return html+escape(text.slice(end));
      },
      rows() { return Array.from(root.querySelectorAll(this.readingMode ? '.wv-paragraph' : '.wv-segment')); },
      get matchCount() { return this.rows().filter(row => this.matches(row)).length; },
      switchText(version) { this.textSource = version; this.selectedIndex = -1; },
      setReading(value) { this.readingMode = value; this.selectedIndex = -1; this.$nextTick(() => this.updateTime(true)); },
      async copyText(text) {
        try { await window.whisperCopy(text); this.notice = 'Testo copiato'; }
        catch (_) { this.notice = 'Copia non disponibile. Seleziona il testo e copialo manualmente.'; }
        clearTimeout(noticeTimer); noticeTimer = setTimeout(() => { if (!disposed) this.notice=''; },2500);
      },
      copyRow(row) { return this.copyText(this.rowText(row)); },
      copyTranscript() { return this.copyText(this.rows().filter(row => this.matches(row)).map(row => this.rowText(row)).join('\n\n')); },
      pauseFollow() { manualUntil = Date.now()+6000; },
      toggleFollow() { this.followPlayback=!this.followPlayback; manualUntil=0; if(this.followPlayback)this.updateTime(true); },
      updateTime(force=false) {
        if (!player) return;
        const time = player.currentTime; this.currentTime=time;
        const segments = root.querySelectorAll('.wv-segment');
        let lo=0, hi=segments.length-1, index=-1;
        while(lo<=hi) { const mid=(lo+hi)>>1; if(Number(segments[mid].dataset.start)<=time) {index=mid;lo=mid+1;} else hi=mid-1; }
        if(index>=0 && time>=Number(segments[index].dataset.end))index=-1;
        this.currentSegIdx=index;
        if(!this.followPlayback || Date.now()<manualUntil || (!force && index===lastFollow))return;
        lastFollow=index;
        const row=this.readingMode ? this.rows().find(el => Number(el.dataset.start)<=time && Number(el.dataset.end)>time) : segments[index];
        if(row && this.matches(row))this.scrollRow(row);
      },
      scrollRow(row) {
        const pane=root.querySelector('.wv-transcript-scroll'), r=row.getBoundingClientRect(), p=pane.getBoundingClientRect();
        if(r.top<p.top+12 || r.bottom>p.bottom-12)pane.scrollTo({top:pane.scrollTop+r.top-p.top-24,behavior:'auto'});
      },
      async ensureAudio() {
        if (!spectrumOpen || !player) return;
        const Audio = window.AudioContext || window.webkitAudioContext;
        if (!Audio) { this.spectrumStatus='Spettro non supportato da questo browser.'; return; }
        if (!context) {
          context = new Audio();
          analyser=context.createAnalyser(); analyser.fftSize=2048; analyser.smoothingTimeConstant=.75;
          source=context.createMediaElementSource(player); source.connect(analyser); analyser.connect(context.destination);
          bins=new Uint8Array(analyser.frequencyBinCount);
        }
        if(context.state==='suspended')await context.resume();
      },
      async play() {
        if(!player)return;
        this.mediaError='';
        try { await this.ensureAudio(); } catch (_) { this.spectrumStatus='Spettro non disponibile in questo browser.'; }
        try { if(!disposed)await player.play(); }
        catch(error) { if(error.name!=='AbortError')this.mediaError='Riproduzione non avviata. Premi di nuovo Ascolta o prova un’altra traccia.'; }
      },
      togglePlayPause() { if(!player)return; if(player.paused)this.play(); else player.pause(); },
      seekTo(value) {
        if(!player)return;
        manualUntil=0; this.followPlayback=true;
        player.currentTime=Math.max(0,Math.min(Number.isFinite(player.duration)?player.duration:Number(root.dataset.duration),value));
        this.updateTime(true); this.play();
      },
      seekDelta(delta) {
        if(!player)return;
        player.currentTime=Math.max(0,Math.min(Number.isFinite(player.duration)?player.duration:Number(root.dataset.duration),player.currentTime+delta));
        this.updateTime(true);
      },
      setSpeed() { if(player)player.playbackRate=Number(this.speed); },
      timelineZoom(factor) { timeline?.zoom(factor); },
      switchAudio(next) {
        if(!player || next===this.audioSource)return;
        const generation=++sourceGeneration, time=player.currentTime, resume=!player.paused;
        player.pause(); this.audioSource=next; this.mediaError='';
        const loaded=() => {
          player.removeEventListener('loadedmetadata',loaded);
          if(disposed || generation!==sourceGeneration)return;
          player.currentTime=Math.min(time,Number.isFinite(player.duration)?player.duration:time);
          this.setSpeed(); timeline?.refresh(); this.updateTime(true); if(resume)this.play();
        };
        on(player,'loadedmetadata',loaded);
        player.src='/viewer/'+root.dataset.job+'/'+(next==='original'?'media':'processed-audio');
        player.load(); timeline?.setSource(this.waveformUrl());
      },
      async toggleSpectrum(open) {
        spectrumOpen=open;
        if(open && player && !player.paused) {
          try { await this.ensureAudio(); } catch (_) { this.spectrumStatus='Spettro non disponibile in questo browser.'; }
        }
        this.drawSpectrum();
      },
      drawSpectrum() {
        cancelAnimationFrame(spectrumFrame);
        if(disposed || !spectrumOpen)return;
        const canvas=root.querySelector('#viewer-spectrum'), ctx=canvas?.getContext('2d');
        if(!ctx)return;
        const width=Math.max(2,Math.floor(canvas.parentElement.clientWidth)), height=96, d=Math.min(2,devicePixelRatio||1);
        if(canvas.width!==width*d)canvas.width=width*d;
        if(canvas.height!==height*d)canvas.height=height*d;
        ctx.setTransform(d,0,0,d,0,0); ctx.clearRect(0,0,width,height);
        const live=analyser && player && !player.paused && context.state==='running';
        canvas.dataset.state=live?'live':'paused';
        if(live) {
          analyser.getByteFrequencyData(bins);
          const nyquist=context.sampleRate/2, upper=Math.min(16000,nyquist), bars=48;
          let peak=0;
          for(let i=1;i<bins.length;i++)if(bins[i]>bins[peak])peak=i;
          canvas.dataset.peakHz=String(bins[peak]>0 ? Math.round(peak*context.sampleRate/analyser.fftSize) : 0);
          const color=getComputedStyle(root).getPropertyValue('--color-accent').trim()||'#28b9ad';
          for(let i=0;i<bars;i++){
            const low=60*(upper/60)**(i/bars), high=60*(upper/60)**((i+1)/bars);
            const a=Math.max(1,Math.floor(low/nyquist*bins.length)), b=Math.min(bins.length,Math.max(a+1,Math.ceil(high/nyquist*bins.length)));
            let value=0;for(let j=a;j<b;j++)value=Math.max(value,bins[j]);
            const h=value/255*(height-5);ctx.fillStyle=color;
            ctx.fillRect(i*width/bars,height-h,Math.max(1,width/bars-3),h);
          }
          this.spectrumStatus=bins[peak]>0 ? 'Frequenze del segnale in riproduzione' : 'Nessun segnale in questo passaggio';
          spectrumFrame=requestAnimationFrame(() => this.drawSpectrum());
        } else {
          canvas.dataset.peakHz='0';
          ctx.fillStyle='rgba(130,150,160,.35)';ctx.fillRect(0,height-1,width,1);
          this.spectrumStatus=analyser?'In pausa · riprendi per vedere le frequenze.':'Avvia l’audio per vedere le frequenze.';
        }
      },
      handleKey(event) {
        const target=event.target;
        if(target.closest('input,textarea,select,button,a,[contenteditable=true],dialog') || event.ctrlKey || event.metaKey || event.altKey)return;
        const key=event.key.toLowerCase();
        if(key==='/'){event.preventDefault();root.querySelector('input[type=search]')?.focus();}
        else if(key===' ' && player){event.preventDefault();this.togglePlayPause();}
        else if(key==='j' || key==='l'){event.preventDefault();this.seekDelta(key==='j'?-5:5);}
        else if(key==='arrowdown' || key==='arrowup'){
          event.preventDefault();const rows=this.rows().filter(row=>this.matches(row)); if(!rows.length)return;
          keyboardIndex=Math.max(0,Math.min(rows.length-1,keyboardIndex+(key==='arrowdown'?1:-1)));
          this.selectedIndex=Number(rows[keyboardIndex].dataset.segIdx ?? -1);
          this.scrollRow(rows[keyboardIndex]);
        } else if(key==='c') {const row=this.rows().filter(row=>this.matches(row))[Math.max(0,keyboardIndex)];if(row)this.copyRow(row);}
      },
      destroy() {
        disposed=true; clearInterval(timer);clearTimeout(noticeTimer);cancelAnimationFrame(spectrumFrame);
        cleanups.forEach(clean=>clean());timeline?.destroy();player?.pause();
        source?.disconnect();analyser?.disconnect();context?.close().catch(()=>{});
      }
    };
  };
})();
