'use strict';
// Exercise the actual JS used by the Jinja studio, without a browser or audio GPU.
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const template = fs.readFileSync(path.join(__dirname, '../../src/whisper_ui/web/templates/studio.html'), 'utf8');
assert.equal(template.includes('<canvas'), false, 'No waveform/canvas should load');
assert.equal(template.includes('/waveform?'), false, 'No waveform API should be requested');
assert.match(template, /<audio[^>]*class="hidden"[^>]*aria-hidden="true"/);
assert.equal(/<audio[^>]*\scontrols(?:\s|>)/.test(template), false, 'Native audio controls must be hidden');
assert.match(template, /@click="togglePlayback\(\)"/);
assert.match(template, /@play="playing=true"/);
assert.match(template, /@pause="playing=false"/);
assert.match(template, /@timeupdate="onAudioTimeUpdate/);
assert.match(template, /@seeked="onAudioTimeUpdate/);
let script = template.split('<script nonce="{{ csp_nonce }}">')[1].split('</script>')[0];
script = script.replace(/{{[^}]+}}/g, (match) => {
  if (match.includes('versions|tojson')) return '[]';
  if (match.includes('left_initial|tojson')) return '"job~original"';
  if (match.includes('right_initial|tojson')) return '"job~corrected"';
  if (match.includes('summary_prompt|tojson')) return '""';
  return 'job';
});
const doc = {activeElement: null};
const scope = {document: doc, console, structuredClone, fetch: async()=>({ok:true,json:async()=>({})})};
vm.createContext(scope);
vm.runInContext(script + '\nthis.makeStudio=reviewStudio;', scope);
const instance = scope.makeStudio();
const scrollCalls = {left:[],right:[],draft:[]};
function list(ref) {
 const list = {scrollTop:0,clientHeight:240, contains: el=>el===doc.activeElement&&ref==='draft',
  getBoundingClientRect:()=>({top:0}),
  querySelector(selector) {
   const n=Number(selector.match(/\d+/)[0]);
   return {offsetHeight:35,getBoundingClientRect:()=>({top:n*40-this.scrollTop})};
  },
  scrollTo({top}) {this.scrollTop=top;scrollCalls[ref].push(top);}
 };
 return list;
}
const player = {currentTime:0,duration:3600,paused:true,plays:0,pauses:0,
 play(){this.paused=false;this.plays++;instance.playing=true;return Promise.resolve();},
 pause(){this.paused=true;this.pauses++;instance.playing=false;}};
instance.$refs = {audio:player,leftList:list('left'),rightList:list('right'),draftList:list('draft')};
instance.$nextTick=(f)=>f();
instance.onAudioMetadata(3600);
assert.equal(instance.audioDuration,3600);
assert.equal(instance.playing,false);
instance.togglePlayback();
assert.equal(player.plays,1);
assert.equal(instance.playing,true);
instance.togglePlayback();
assert.equal(player.pauses,1);
assert.equal(instance.playing,false);
instance.seekTo(14, false);
assert.equal(player.currentTime,14);
assert.equal(player.plays,1,'Scrubbing the slider must not auto-play');
assert.equal(instance.viewEnd(),3600);
instance.zoomTimeline(1.8);
assert.ok(instance.viewSpan()<3600);
const prev=instance.viewStart;
instance.panTimeline(.65);
assert.ok(instance.viewStart>prev);
instance.fitTimeline();
assert.equal(instance.viewStart,0);
assert.equal(instance.zoom,1);
function segs(n){return Array.from({length:n},(_,i)=>({start:i*4,end:i*4+2,text:`word ${i}`,speaker:'S1'}));}
instance.leftSegments=segs(900);
instance.rightSegments=segs(900);
instance.draft=segs(900);
instance.onAudioTimeUpdate(25);
assert.equal(instance.leftActive,6);
assert.equal(instance.rightActive,6);
assert.equal(instance.draftActive,6);
assert.ok(scrollCalls.left.length&&scrollCalls.right.length&&scrollCalls.draft.length,
 'All three columns must follow audio automatically');
const counts=Object.fromEntries(Object.entries(scrollCalls).map(([key,value])=>[key,value.length]));
instance.onAudioTimeUpdate(25.7);
for(const [key, value] of Object.entries(counts))assert.equal(scrollCalls[key].length,value,
 'No unnecessary scroll while the segment does not change');
instance.onAudioTimeUpdate(2800.2);
assert.equal(instance.leftActive,700);
assert.ok(instance.leftLimit>=701&&instance.rightLimit>=701&&instance.draftLimit>=701,
 'Long recordings must reveal segments beyond the initial 150');
// No active speech in long gaps: do not falsely highlight the previous speaker.
instance.onAudioTimeUpdate(2803);
assert.equal(instance.leftActive,-1);
// A focused editor must never be scrolled away while the user is typing.
doc.activeElement={tagName:'TEXTAREA'};
const draftScrollCount=scrollCalls.draft.length;
instance.onAudioTimeUpdate(2804.4);
assert.equal(instance.leftActive,701);
assert.equal(instance.draftActive,701);
assert.equal(scrollCalls.draft.length,draftScrollCount);
assert.ok(scrollCalls.left.length>counts.left);
// Follow can be disabled without stopping active highlighting.
instance.toggleFollow();
const leftScrollCount=scrollCalls.left.length;
instance.onAudioTimeUpdate(2808.6);
assert.equal(instance.leftActive,702);
assert.equal(scrollCalls.left.length,leftScrollCount);
// Seeking from the timeline synchronizes time and highlights immediately.
instance.seekTo(20);
assert.equal(player.currentTime,20);
assert.equal(instance.leftActive,5);
instance.zoomTimeline(1.8);
instance.onAudioTimeUpdate(3500);
assert.ok(instance.viewStart<=3500&&instance.viewEnd()>=3500,
 'Zoomed timeline should follow a seek out of its window');
console.log('Studio timeline: zoom, pan, seeks, follow A/B/C, long recordings, pause follow and focus protection PASS');
