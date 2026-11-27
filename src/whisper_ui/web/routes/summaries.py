"""Authenticated summary library and revision workspace."""
from pathlib import Path
from typing import Literal
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from pydantic import BaseModel, Field
from whisper_ui.core.constants import DEFAULT_SUMMARY_PROMPT
from whisper_ui.core.models import JobStatus
from whisper_ui.storage import summary_library as library
from whisper_ui.web.auth import owner_filter
from whisper_ui.web.deps import CurrentUserDep, DbDep, FileStoreDep, RedisDep, SettingsDep, make_content_disposition, templates
from whisper_ui.web.validation import validate_hex_id
from whisper_ui.web.routes.studio import _summary_models, _version
from whisper_ui.worker.standalone_summary import enqueue_summary, summary_status

router = APIRouter()
ACTIVE = {'queued','started','deferred','scheduled'}
PRIVATE = {'Cache-Control':'private, no-store'}


class Generate(BaseModel):
    job_id: str = Field(max_length=32)
    source: str = Field(max_length=20)
    title: str = Field(min_length=1,max_length=200)
    prompt: str = Field(default=DEFAULT_SUMMARY_PROMPT,max_length=4000)
    model: str = Field(min_length=1,max_length=256)
    strategy: Literal["auto", "direct", "multistage"] = "auto"

class Edit(BaseModel):
    title: str = Field(min_length=1,max_length=200)
    text: str = Field(max_length=500000)
    revision: str = Field(min_length=1,max_length=64)


def _owned(job_id, db, user):
    validate_hex_id(job_id,'job_id')
    job=db.get_job(job_id,owner_id=owner_filter(user))
    if not job or job.status != JobStatus.COMPLETED:
        raise HTTPException(404)
    return job


def _document(store, job_id, sid):
    try: item=library.read(store,job_id,sid)
    except ValueError: raise HTTPException(404) from None
    if item is None: raise HTTPException(404)
    return item


def _state(item, redis):
    if item.get('status') != 'queued':
        return item
    status=summary_status(redis,item['job_id'])
    if status.get('summary_id') == item['id']:
        state=status['status']
        # File is the authoritative completed result, RQ is progress only.
        if state in {'failed','stopped','canceled','finished'}:
            state='failed'
            status={**status,'status':state}
        return {**item,**status}
    return {**item,'status':'unknown'}


def _list(db,store,user,redis):
    rows=[];jobs=[];offset=0
    while True:
        batch=db.list_jobs_filtered(status=JobStatus.COMPLETED.value,limit=200,offset=offset,owner_id=owner_filter(user))
        if not batch: break
        for job in batch:
            jobs.append({'id':job.id,'filename':job.filename})
            for item in library.list_documents(store,job.id):
                item=_state(item,redis)
                rows.append({k:v for k,v in {**item,'filename':job.filename}.items() if k not in {'text','prompt'}})
        offset+=len(batch)
        if len(batch)<200: break
    rows.sort(key=lambda row:row.get('updated_at',''),reverse=True)
    return rows,jobs


@router.get('/summaries',response_class=HTMLResponse)
async def summaries_page(request:Request,db:DbDep,filestore:FileStoreDep,redis:RedisDep,settings:SettingsDep,user:CurrentUserDep,job:str='',summary:str=''):
    if job: _owned(job,db,user)
    if summary:
        if not job: raise HTTPException(400)
        _document(filestore,job,summary)
    return templates.TemplateResponse(request=request,name='summaries.html',context={'active_page':'summaries','summary_config':{'job':job,'summary':summary,'defaultModel':settings.ollama_model,'defaultPrompt':DEFAULT_SUMMARY_PROMPT,'llmAvailable':settings.llm_correction_available}},headers=PRIVATE)


@router.get('/summaries/api/library')
async def list_summaries(db:DbDep,filestore:FileStoreDep,redis:RedisDep,user:CurrentUserDep):
    rows,jobs=_list(db,filestore,user,redis)
    return JSONResponse({'summaries':rows,'jobs':jobs},headers=PRIVATE)


@router.get('/summaries/api/models')
async def models(settings:SettingsDep,user:CurrentUserDep):
    if not settings.llm_correction_available: raise HTTPException(503)
    return JSONResponse({'models':await _summary_models(settings),'default':settings.ollama_model},headers=PRIVATE)


@router.get('/summaries/api/{job_id}/sources')
async def sources(job_id:str,db:DbDep,filestore:FileStoreDep,user:CurrentUserDep):
    job=_owned(job_id,db,user)
    available=[{'id':kind,'label':label} for kind,label,loader in [('original','Whisper originale',filestore.load_result),('corrected','Correzione LLM',filestore.load_corrected_result),('custom','Personalizzata',filestore.load_custom_result)] if loader(job.id) is not None]
    return JSONResponse({'sources':available,'prompt':job.summary_prompt or ''},headers=PRIVATE)


@router.post('/summaries/api/generate')
async def generate(data:Generate,db:DbDep,filestore:FileStoreDep,redis:RedisDep,settings:SettingsDep,user:CurrentUserDep):
    job=_owned(data.job_id,db,user)
    if not settings.llm_correction_available: raise HTTPException(503,detail='AI non disponibile')
    _version(f'{job.id}~{data.source}',[job],filestore)
    if not data.title.strip(): raise HTTPException(422,detail='Inserisci un titolo')
    if data.model != settings.ollama_model and data.model not in await _summary_models(settings): raise HTTPException(422,detail='Modello non presente nel router')
    if summary_status(redis,job.id)['status'] in ACTIVE: raise HTTPException(409,detail='Un riassunto di questa trascrizione è già in elaborazione')
    item=library.create(filestore,job.id,data.title.strip(),data.source,data.model,data.prompt.strip(),strategy=data.strategy)
    try: task=enqueue_summary(redis,job.id,data.prompt.strip(),data.source,data.model,item['id'],strategy=data.strategy)
    except Exception:
        # Transport failure may be ambiguous; keep the document recoverable.
        raise
    if task.get('summary_id') != item['id']:
        library.discard_unsubmitted(filestore,job.id,item['id']);raise HTTPException(409,detail='Un altro riepilogo è già in coda')
    return JSONResponse(_state(_document(filestore,job.id,item['id']),redis),status_code=202,headers=PRIVATE)


@router.get('/summaries/api/{job_id}/{sid}')
async def get_summary(job_id:str,sid:str,db:DbDep,filestore:FileStoreDep,redis:RedisDep,user:CurrentUserDep):
    job=_owned(job_id,db,user)
    return JSONResponse({**_state(_document(filestore,job_id,sid),redis),'filename':job.filename},headers=PRIVATE)


@router.put('/summaries/api/{job_id}/{sid}')
async def save_summary(job_id:str,sid:str,data:Edit,db:DbDep,filestore:FileStoreDep,user:CurrentUserDep):
    _owned(job_id,db,user)
    item=_document(filestore,job_id,sid)
    if item.get('status')!='finished': raise HTTPException(409,detail='Riassunto non ancora completato')
    if not data.title.strip(): raise HTTPException(422,detail='Inserisci un titolo')
    try: item=library.update(filestore,job_id,sid,{'title':data.title.strip(),'text':data.text},expected_revision=data.revision)
    except ValueError: raise HTTPException(409,detail='Modificato in un’altra scheda. Ricarica prima di salvare.') from None
    return JSONResponse(item,headers=PRIVATE)


@router.get('/summaries/api/{job_id}/{sid}/download')
async def download(job_id:str,sid:str,db:DbDep,filestore:FileStoreDep,user:CurrentUserDep,format:str='md'):
    _owned(job_id,db,user);item=_document(filestore,job_id,sid)
    if item.get('status')!='finished': raise HTTPException(409)
    if format not in {'md','txt'}: raise HTTPException(400)
    name=Path(item.get('title') or 'riassunto').name[:150]+'.'+format
    return Response(item.get('text',''),media_type='text/markdown' if format=='md' else 'text/plain',headers={**PRIVATE,'Content-Disposition':make_content_disposition(name)})
