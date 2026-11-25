from types import SimpleNamespace
from pathlib import Path
import json
import pytest
from whisper_ui.storage import summary_library as lib


def test_legacy_and_multiple_independent_documents(tmp_path):
    jid='a'*32
    original=tmp_path/jid/'summary.json'
    original.parent.mkdir()
    original.write_text(json.dumps({'text':'Originale','source':'corrected'}))
    store=SimpleNamespace(_output_dir=tmp_path,load_summary=lambda j:json.loads(original.read_text()))
    legacy=lib.read(store,jid,'legacy')
    assert legacy['text']=='Originale'
    first=lib.create(store,jid,'Primo','original','model-a','prompt')
    second=lib.create(store,jid,'Secondo','corrected','model-b','prompt')
    lib.update(store,jid,first['id'],{'text':'Uno','status':'finished'})
    lib.update(store,jid,second['id'],{'text':'Due','status':'finished'})
    lib.update(store,jid,'legacy',{'text':'Modificato'},expected_revision='legacy')
    assert json.loads(original.read_text())['text']=='Originale'
    assert {x['text'] for x in lib.list_documents(store,jid)}=={'Uno','Due','Modificato'}
    with pytest.raises(ValueError):lib.update(store,jid,'legacy',{'text':'Stale'},expected_revision='legacy')
    with pytest.raises(ValueError):lib.read(store,jid,'../summary')
