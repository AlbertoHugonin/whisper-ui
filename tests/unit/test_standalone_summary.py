"""The summary model runs separately, preserving the original transcript."""

from contextlib import contextmanager
import pytest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from whisper_ui.core.models import Job, JobStatus, Segment, TranscriptResult
from whisper_ui.storage.filestore import FileStore
from whisper_ui.worker.standalone_summary import _split_transcript, run_standalone_summary


def test_long_transcript_split_is_bounded():
    chunks = _split_transcript(("line\n" * 20000), max_chars=1000)
    assert chunks and all(len(c) <= 1000 for c in chunks)
    assert len(chunks) > 5


@pytest.mark.parametrize("selected", ["", "qwen3.5-9b:latest"])
def test_summary_is_saved_separately(tmp_path, selected):
    filestore = FileStore(tmp_path / "uploads", tmp_path / "outputs")
    job = Job(filename="meeting.wav", status=JobStatus.COMPLETED, summary_prompt="Decisioni Airdass")
    filestore.save_result(
        job.id,
        TranscriptResult(
            segments=[Segment(0, 2, "Buongiorno Airdas", "S1")],
            language="it",
            duration=2,
        ),
    )
    db = MagicMock()
    db.get_job.return_value = job
    settings = SimpleNamespace(
        llm_correction_available=True,
        llm_api_mode="openai-router",
        ollama_base_url="http://router:18084",
        ollama_request_timeout=60,
        ollama_model="auto-resident",
        ollama_keep_alive="10m",
    )
    runtime = SimpleNamespace(db=db, filestore=filestore, settings=settings)

    @contextmanager
    def fake_runtime(job_id):
        assert job_id == job.id
        yield runtime

    class FakeClient:
        def __init__(self, url, timeout, api_mode):
            assert url == "http://router:18084"

        def complete(self, **kwargs):
            assert kwargs["model"] == (selected or "auto-resident")
            assert "Decisioni Airdass" in kwargs["user"]
            return "Discussa la piattaforma Airdass.", "stop"

        def close(self):
            pass

    with (
        patch("whisper_ui.worker.standalone_summary.build_worker_runtime", side_effect=fake_runtime),
        patch(
            "whisper_ui.worker.standalone_summary.SummaryClient",
            FakeClient,
        ),
    ):
        output = run_standalone_summary(job.id, "", "original", selected)
    assert output["chunks"] == 1
    summary = filestore.load_summary(job.id)
    assert summary["source"] == "original"
    assert summary["model"] == (selected or "auto-resident")
    assert summary["text"] == "Discussa la piattaforma Airdass."
    assert filestore.load_result(job.id).segments[0].text == "Buongiorno Airdas"


def test_summary_retries_truncated_output_and_never_returns_partial():
    from whisper_ui.worker.standalone_summary import _complete_summary
    client=MagicMock()
    client.complete.side_effect=[("truncated", "length"), ("complete.", "stop")]
    args=dict(model="model",system="system",user="full input",keep_alive="10m",merging=True)
    assert _complete_summary(client,**args)=="complete."
    assert [call.kwargs["max_tokens"] for call in client.complete.call_args_list]==[32768,65536]
    assert all(call.kwargs["user"]=="full input" for call in client.complete.call_args_list)
    client.reset_mock();client.complete.side_effect=[("partial", "length")]*2
    with pytest.raises(RuntimeError,match="no truncated result saved"):
        _complete_summary(client,**args)
    client.complete.side_effect=[("partial", None)]
    with pytest.raises(RuntimeError,match="did not finish normally"):
        _complete_summary(client,**args)


def test_router_summary_client_reads_finish_reason():
    import httpx
    from whisper_ui.worker.standalone_summary import SummaryClient
    client=SummaryClient("http://router",1,"openai-router")
    client._client.close()
    def handler(req):
        import json
        body=json.loads(req.content)
        assert body["max_tokens"]==8192
        assert "response_format" not in body
        return httpx.Response(200,json={"choices":[{"message":{"content":"partial"},"finish_reason":"length"}]})
    client._client=httpx.Client(base_url="http://router",transport=httpx.MockTransport(handler))
    try: assert client.complete(model="m",system="s",user="u",keep_alive="10m",max_tokens=8192)==("partial","length")
    finally: client.close()


@pytest.mark.parametrize("model,size,expected", [
    ("qwen3.8-flash-next-iq3_s", 46625, "direct"),
    ("qwen3.8-flash-next-ud-iq4_xs-128k", 100000, "multistage"),
    ("qwen3.8-flash-next-iq3_s", 200000, "multistage"),
    ("auto-resident", 100, "multistage"),
    ("unknown-model", 100, "multistage"),
])
def test_automatic_strategy_reserves_output_context(model, size, expected):
    from whisper_ui.worker.standalone_summary import choose_strategy
    assert choose_strategy("auto", model, "a" * size, "summary", "openai-router") == expected
    assert choose_strategy("direct", model, "a" * size, "summary", "openai-router") == "direct"
    assert choose_strategy("multistage", model, "a" * size, "summary", "openai-router") == "multistage"


@pytest.mark.parametrize("requested,model,expected,passes", [
    ("direct", "unknown-model", "direct", 1),
    ("auto", "qwen3.8-flash-next-iq3_s", "direct", 1),
    ("multistage", "qwen3.8-flash-next-iq3_s", "multistage", 4),
])
def test_generation_strategy_preserves_input_and_records_actual_mode(tmp_path, requested, model, expected, passes):
    from whisper_ui.storage import summary_library as lib
    from whisper_ui.worker.standalone_summary import choose_strategy
    store = FileStore(tmp_path / "uploads", tmp_path / "outputs")
    job = Job(filename="test.wav", status=JobStatus.COMPLETED)
    lines = ["first " + "a"*4000, "middle " + "b"*4000, "last " + "c"*4000]
    store.save_result(job.id, TranscriptResult(segments=[Segment(i,i+1,line,"S1") for i,line in enumerate(lines)],language="it",duration=3))
    doc = lib.create(store,job.id,"Test","original",model,"Prompt",strategy=requested)
    db = MagicMock();db.get_job.return_value=job
    settings=SimpleNamespace(llm_correction_available=True,llm_api_mode="openai-router",ollama_base_url="http://router",ollama_request_timeout=60,ollama_model=model,ollama_keep_alive="10m")
    @contextmanager
    def runtime(_):
        yield SimpleNamespace(db=db,filestore=store,settings=settings)
    calls=[]
    client=MagicMock();client.complete.side_effect=lambda **kw: (calls.append(kw) or "Complete summary.", "stop")
    with patch("whisper_ui.worker.standalone_summary.build_worker_runtime",side_effect=runtime),patch("whisper_ui.worker.standalone_summary.SummaryClient",return_value=client):
        result=run_standalone_summary(job.id,"Prompt","original",model,doc["id"],requested)
    assert len(calls)==passes
    assert result["passes"]==passes
    if expected=="direct":
        assert "\n".join(lines) in calls[0]["user"]
        assert calls[0]["max_tokens"]==32768
    else:
        assert all(line in calls[i]["user"] for i,line in enumerate(lines))
    saved=lib.read(store,job.id,doc["id"])
    assert saved["strategy"]==requested and saved["strategy_used"]==expected and saved["passes"]==passes and saved["status"]=="finished"
