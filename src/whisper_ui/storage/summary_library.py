"""Independent summary documents, with read-through compatibility for summary.json."""
from __future__ import annotations
import fcntl
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile
from uuid import uuid4


def now():
    return datetime.now(timezone.utc).isoformat()


def _directory(store, job_id):
    if not re.fullmatch(r'[0-9a-f]{32}', job_id):
        raise ValueError('Invalid job id')
    return store._output_dir / job_id / 'summaries'


def _path(store, job_id, summary_id):
    if summary_id != 'legacy' and not re.fullmatch(r'[0-9a-f]{32}', summary_id):
        raise ValueError('Invalid summary id')
    return _directory(store, job_id) / (summary_id + '.json')


def read(store, job_id, summary_id):
    path = _path(store, job_id, summary_id)
    try:
        item = json.loads(path.read_text(encoding='utf-8'))
        if isinstance(item, dict):
            return item
    except (OSError, ValueError):
        pass
    if summary_id == 'legacy':
        original = store.load_summary(job_id)
        if original and original.get('text'):
            legacy = path.parent.parent / 'summary.json'
            try:
                created = datetime.fromtimestamp(legacy.stat().st_mtime, timezone.utc).isoformat()
            except OSError:
                created = ''
            return {**original, 'id':'legacy','job_id':job_id,'title':'Riassunto originale',
                    'created_at':created,'updated_at':created,'revision':'legacy','status':'finished'}
    return None


def list_documents(store, job_id):
    directory = _directory(store, job_id)
    ids = {'legacy'}
    if directory.is_dir():
        ids.update(p.stem for p in directory.glob('*.json') if re.fullmatch(r'[0-9a-f]{32}|legacy',p.stem))
    return sorted((item for sid in ids if (item := read(store, job_id, sid))),
                  key=lambda item:item.get('updated_at',''),reverse=True)


def _write(path, item):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = None
    try:
        with NamedTemporaryFile(mode='w',encoding='utf-8',dir=path.parent,prefix='.summary-',delete=False) as f:
            temp=Path(f.name);json.dump(item,f,ensure_ascii=False,indent=2)
        temp.chmod(0o660)
        temp.replace(path)
    finally:
        if temp is not None:
            temp.unlink(missing_ok=True)


def create(store, job_id, title, source, model, prompt):
    sid = uuid4().hex
    stamp = now()
    item = dict(id=sid,job_id=job_id,title=title,source=source,model=model,prompt=prompt,text='',
                status='queued',created_at=stamp,updated_at=stamp,revision=uuid4().hex)
    _write(_path(store,job_id,sid),item)
    return item


def update(store, job_id, summary_id, changes, expected_revision=None):
    path = _path(store,job_id,summary_id)
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.with_suffix('.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        item = read(store,job_id,summary_id)
        if item is None:
            raise FileNotFoundError(summary_id)
        if expected_revision is not None and item.get('revision') != expected_revision:
            raise ValueError('Revision conflict')
        item.update(changes,updated_at=now(),revision=uuid4().hex)
        _write(path,item)
        return item


def discard_unsubmitted(store, job_id, summary_id):
    # Only used for a freshly allocated, never-enqueued document.
    if summary_id != 'legacy':
        _path(store,job_id,summary_id).unlink(missing_ok=True)
