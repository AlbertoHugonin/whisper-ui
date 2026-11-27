"""LAN-only design demo. Synthetic data, fake Redis, no production connections.
Use a dedicated service user and state directory. Never point this at real data.
"""
import math
import os
import wave
from array import array
from pathlib import Path

import fakeredis
import uvicorn
from whisper_ui.core.config import Settings
from whisper_ui.core.models import Job, JobStatus, Segment, TranscriptResult
from whisper_ui.core.presets import PresetSettings
from whisper_ui.storage import users_repo, summary_library
from whisper_ui.storage.database import JobDatabase
from whisper_ui.storage.filestore import FileStore
from whisper_ui.storage.presets import PresetRepository
from whisper_ui.web.app import create_app
from whisper_ui.web.app import templates

state = Path(os.environ['STATE_DIRECTORY']).resolve()
assert state.name == 'whisper-atelier-preview', 'Refusing non-preview data directory'
settings = Settings(database_path=state/'demo.db', upload_dir=state/'uploads', output_dir=state/'outputs', session_secret=os.environ['SESSION_SECRET'], session_https_only=False, allow_registration=False, device='cpu', redis_url='redis://127.0.0.1:1/0', ollama_base_url='http://127.0.0.1:1', llm_api_mode='openai-router', diarization_remote_worker_available=True, max_upload_size=25*1024*1024)
assert settings.database_path.is_relative_to(state)
db=JobDatabase(settings.database_path)
files=FileStore(settings.upload_dir,settings.output_dir)
admin=users_repo.get_user_by_username(db.conn,'demo')
if admin is None:
    admin=users_repo.create_user(db.conn,'demo',os.environ['PREVIEW_PASSWORD'],is_admin=True)
    preset=PresetRepository(db.conn).create(admin.id,'Riunione · italiano','Profilo dimostrativo',PresetSettings(language='it'))
    topics=[('Riunione progetto — pianificazione.wav','Pianificazione del progetto','Il gruppo ha definito le priorità per il prossimo ciclo di lavoro.'),('Intervista — raccolta requisiti.wav','Requisiti della piattaforma','La sessione ha raccolto le esigenze principali per la piattaforma.'),('Nota vocale — idee per il prototipo.wav','Idee per il prototipo','Sono state discusse tre ipotesi di interfaccia e i criteri di verifica.')]
    for i,(name,title,subject) in enumerate(topics):
        job=Job(id=f'{i+1:032x}',filename=name,owner_id=admin.id,status=JobStatus.COMPLETED,language='it',duration=12)
        media=files.get_upload_path(job.id,job.filename);media.parent.mkdir(parents=True,exist_ok=True)
        with wave.open(str(media),'wb') as audio:
            audio.setnchannels(1);audio.setsampwidth(2);audio.setframerate(16000)
            audio.writeframes(array('h',(int(2000*math.sin(2*math.pi*(220+i*30)*n/16000)*(.25+.75*math.sin(n/8000)**2)) for n in range(192000))).tobytes())
        processed=settings.output_dir/job.id/'processed.wav';processed.parent.mkdir(parents=True,exist_ok=True);processed.write_bytes(media.read_bytes())
        job.filepath=str(media)
        segs=[Segment(0,3,subject,'S1'),Segment(3,6,'Le attività saranno organizzate in fasi, con una verifica al termine di ciascuna.','S2'),Segment(6,9,'È importante conservare le decisioni e rendere il documento facile da consultare.','S1'),Segment(9,12,'Il prossimo incontro servirà a confrontare i risultati e a scegliere la soluzione definitiva.','S2')]
        result=TranscriptResult(segments=segs,duration=12,language='it')
        job.result_path=str(files.save_result(job.id,result));files.save_corrected_result(job.id,result);db.insert_job(job)
        doc=summary_library.create(files,job.id,title,'corrected','demo — nessuna inferenza','Testo dimostrativo',strategy='direct')
        summary_library.update(files,job.id,doc['id'],{'status':'finished','strategy_used':'direct','text':f'# {title}\n\n{subject}\n\n## Decisioni\n\nIl gruppo ha concordato un percorso di lavoro a fasi. Ogni attività prevede una verifica e un confronto dei risultati.\n\n## Prossimi passi\n\n- Preparare una proposta.\n- Verificare i requisiti.\n- Organizzare il prossimo incontro.\n\n*Contenuto sintetico per la verifica dell’interfaccia. L’audio associato è un segnale di prova, non una registrazione reale.*'})
app=create_app()
app.state.settings=settings;app.state.db=db;app.state.filestore=files;app.state.redis=fakeredis.FakeRedis();app.state.bootstrap_done=True
templates.env.globals['signal_preview']=True
# Do not dispatch jobs or startup background processes in this environment.
uvicorn.run(app,host='192.168.0.132',port=18087,lifespan='off',proxy_headers=False,access_log=False)
