'use strict';
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const upload = fs.readFileSync(path.join(__dirname, '../../src/whisper_ui/web/templates/upload.html'), 'utf8');
const start = upload.indexOf('    syncFileOrder() {');
const end = upload.indexOf('    formatSize(bytes) {', start);
assert.ok(start > 0 && end > start);
const methods = upload.slice(start, end);
class DataTransfer {
  constructor() { const files=[]; this.files=files; this.items={add:file=>files.push(file)}; }
}
const ctx = vm.createContext({DataTransfer});
const controls = vm.runInContext('({' + methods + '})',ctx);
const first={name:'First.mp3'},second={name:'Second.wav'},third={name:'Third.m4a'};
const inputs={fileInput:{files:[first,second,third]},folderInput:{files:[]}};
const state={...controls,tab:'files',selectedFiles:[first,second,third],$refs:inputs};
state.moveFile(2,-1);
assert.deepEqual([...state.selectedFiles].map(f=>f.name),['First.mp3','Third.m4a','Second.wav']);
assert.deepEqual([...inputs.fileInput.files].map(f=>f.name),['First.mp3','Third.m4a','Second.wav']);
state.moveFile(0,-1);
assert.equal(state.selectedFiles[0],first);
state.removeFile(1);
assert.deepEqual([...inputs.fileInput.files].map(f=>f.name),['First.mp3','Second.wav']);
state.tab='folder';state.selectedFiles=[second,first];state.moveFile(1,-1);
assert.deepEqual([...inputs.folderInput.files].map(f=>f.name),['First.mp3','Second.wav']);
assert.match(upload, /name="merge_mode"/);
assert.match(upload, /name="merge_order"/);
assert.match(upload, /selectedFiles\.map\(\(_, idx\) => idx\)\.join\(','\)/);
console.log('Ordered upload: file input and folder input order, removal and merge form controls PASS');
