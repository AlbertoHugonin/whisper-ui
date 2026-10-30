'use strict';
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const template = fs.readFileSync(path.join(__dirname, '../../src/whisper_ui/web/templates/_vad_help_dialog.html'), 'utf8');
const script = template.split('<script nonce="{{ csp_nonce }}">')[1].split('</script>')[0];
let listener;
const dialog = {
  open: false,
  count: 0,
  showModal() { this.open = true; this.count++; },
};
const document = {
  getElementById: id => id === 'vad-help-dialog' ? dialog : null,
  addEventListener: (name, fn) => { assert.equal(name, 'click'); listener = fn; },
};
vm.runInNewContext(script, { document });
assert.equal(typeof listener, 'function');
let prevented = 0;
listener({target: {closest: () => null}, preventDefault: () => prevented++});
assert.equal(dialog.count, 0);
const button = {dataset: {vadHelp: ''}};
listener({target: {closest: selector => {
  assert.equal(selector, '[data-vad-help]');
  return button;
}}, preventDefault: () => prevented++});
assert.equal(dialog.count, 1);
assert.equal(prevented, 1);
listener({target: {closest: () => button}, preventDefault: () => prevented++});
assert.equal(dialog.count, 1, 'Do not reopen a currently open dialog');
dialog.open = false;
listener({target: {closest: () => button}, preventDefault: () => prevented++});
assert.equal(dialog.count, 2, 'Dialog can reopen after native close');
assert.match(template, /aria-labelledby="vad-help-title"/);
assert.match(template, /aria-describedby="vad-help-description"/);
assert.equal((template.match(/id="vad-help-dialog"/g) || []).length, 1);
assert.match(template, /<form method="dialog"/);
console.log('VAD guide: click, no accidental opens, reopening, semantics PASS');
