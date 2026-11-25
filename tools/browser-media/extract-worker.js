/* MP4 -> mono PCM WAV. No video frame is decoded or sent over the network. */
import { Input, BlobSource, MP4, Output, WavOutputFormat, Conversion, BufferTarget, StreamTarget } from 'mediabunny';
const RATE = 16000;
const MEMORY_LIMIT = 256 * 1024 * 1024;
self.onmessage = async ({data: {file, tempName}}) => {
  let input, writable, root;
  try {
    input = new Input({source: new BlobSource(file, {maxCacheSize: 8 * 1024 * 1024}), formats: [MP4]});
    const tracks = await input.getAudioTracks();
    const primary = await input.getPrimaryAudioTrack();
    if (!primary) throw new Error('Il video non contiene una traccia audio.');
    const duration = await input.computeDuration([primary]);
    if (!Number.isFinite(duration) || duration <= 0) throw new Error('Durata audio non valida.');
    self.postMessage({type:'info', tracks:tracks.length, duration});
    // Prefer disk-backed OPFS for recordings lasting hours. Memory fallback is bounded.
    let target;
    try {
      root = await navigator.storage.getDirectory();
      const handle = await root.getFileHandle(tempName, {create:true});
      writable = await handle.createWritable();
      target = new StreamTarget(new WritableStream({
        write: chunk => writable.write({type:'write', position:chunk.position, data:chunk.data}),
        close: () => writable.close(),
        abort: reason => writable.abort(reason)
      }), {chunked:true, chunkSize:1024*1024});
    } catch (_) {
      if (writable) await writable.abort().catch(()=>{});
      if (root) await root.removeEntry(tempName).catch(()=>{});
      root = null; writable = null;
      if (duration * RATE * 2 + 4096 > MEMORY_LIMIT) throw new Error('Registrazione troppo lunga per la memoria disponibile nel browser. Usa un browser con spazio locale disponibile oppure estrai il WAV sul PC.');
      target = new BufferTarget();
    }
    const output = new Output({format:new WavOutputFormat(), target});
    const conversion = await Conversion.init({input, output, video:{discard:true}, audio: track => track.id === primary.id ? {numberOfChannels:1,sampleRate:RATE,codec:'pcm-s16',forceTranscode:true} : {discard:true}, tags:{}, showWarnings:false});
    if (!conversion.isValid || !conversion.utilizedTracks.some(t=>t.id===primary.id)) throw new Error('Codec audio non decodificabile in questo browser. Prova Chrome o Edge aggiornato.');
    let last = -1;
    conversion.onProgress = p => { const progress = Math.min(99,Math.max(0,Math.floor(p*100))); if(progress!==last){last=progress;self.postMessage({type:'progress',progress});} };
    await conversion.execute();
    const blob = root ? await (await root.getFileHandle(tempName)).getFile() : new Blob([target.buffer],{type:'audio/wav'});
    if (blob.size < 45) throw new Error('Il file audio estratto è vuoto.');
    self.postMessage({type:'done',blob,tracks:tracks.length,temporary:!!root});
  } catch (error) {
    if(writable) await writable.abort().catch(()=>{});
    if(root) await root.removeEntry(tempName).catch(()=>{});
    self.postMessage({type:'error',message:error instanceof Error ? error.message : String(error)});
  } finally { input?.dispose(); }
};
