/* The only MP4 upload path: prepare audio locally, then submit the normal form. */
(function () {
  'use strict';
  const isVideo = file => /\.mp4$/i.test(file.name) || file.type === 'video/mp4';
  const removeTemp = async name => {
    try { const root = await navigator.storage.getDirectory(); await root.removeEntry(name); } catch (_) {}
  };
  function create() {
    let active = null;
    let generation = 0;
    const temporary = new Set();
    function extract(file, onUpdate) {
      return new Promise((resolve, reject) => {
        const name = 'atelier-audio-' + crypto.randomUUID() + '.wav';
        let worker;
        try { worker = new Worker('/static/vendor/atelier-extract-worker.js?v=mp4-r1'); } catch (error) { reject(error); return; }
        const finish = (error, data) => {
          if (active?.worker !== worker) return;
          active = null; worker.terminate();
          if (error) { void removeTemp(name); reject(error); return; }
          if (data.temporary) temporary.add(name);
          resolve(new File([data.blob], file.name.replace(/\.mp4$/i, '') + '.wav', {type:'audio/wav',lastModified:file.lastModified}));
        };
        active = {worker, cancel: () => finish(new Error('Estrazione annullata.'))};
        worker.onmessage = ({data}) => {
          if (data.type === 'done') finish(null, data);
          else if (data.type === 'error') finish(new Error(data.message));
          else onUpdate(data);
        };
        worker.onerror = () => finish(new Error('Impossibile avviare il convertitore locale.'));
        worker.onmessageerror = () => finish(new Error('Impossibile leggere il risultato della conversione.'));
        try { worker.postMessage({file,tempName:name}); } catch (error) { finish(error); }
      });
    }
    async function prepare(files, onUpdate) {
      const token = ++generation;
      const result = [];
      const total = files.filter(isVideo).length;
      let index = 0;
      for (const file of files) {
        if (token !== generation) throw new Error('Estrazione annullata.');
        if (!isVideo(file)) { result.push(file); continue; }
        index++;
        onUpdate({name:file.name,index,total,progress:0,tracks:0});
        result.push(await extract(file, msg => onUpdate({...msg,name:file.name,index,total})));
      }
      if (token !== generation) throw new Error('Estrazione annullata.');
      return result;
    }
    function cancel() { generation++; active?.cancel(); }
    function cleanup() { cancel(); for (const name of temporary) void removeTemp(name); temporary.clear(); }
    return {prepare,cancel,cleanup};
  }
  window.AtelierLocalMedia = {isVideo,create};
})();
