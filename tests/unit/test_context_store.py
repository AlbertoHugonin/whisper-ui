from __future__ import annotations

import fakeredis

from whisper_ui.worker.context_store import PipelineContextStore


def _store() -> PipelineContextStore:
    return PipelineContextStore(fakeredis.FakeRedis(), "job-1")


def test_disjoint_updates_from_parallel_branches_are_both_preserved():
    store = _store()
    store.initialize({"audio_path": "/tmp/a.wav"})

    store.update({"transcription_result": {"segments": [1]}})
    store.update({"diarize_result": [("SPK0", 0.0, 1.0)]})

    loaded = store.load()
    assert loaded["transcription_result"] == {"segments": [1]}
    assert loaded["diarize_result"] == [("SPK0", 0.0, 1.0)]
    assert loaded["audio_path"] == "/tmp/a.wav"


def test_update_if_generation_matches_drops_stale_write():
    """Core retry-isolation invariant: a stage that was enqueued under
    an older generation must not be able to write its output into the
    context after a retry has bumped the counter. This is what prevents
    a still-running diarize from a previous attempt from polluting a
    fresh retry's Redis hash.
    """
    redis = fakeredis.FakeRedis()
    store = PipelineContextStore(redis, "job-3")
    store.initialize({"audio_path": "/tmp/a.wav"})
    redis.set(store.generation_key, 2)

    committed = store.update_if_generation_matches({"diarize_result": ["stale!"]}, 1)

    assert committed is False
    assert "diarize_result" not in store.load()


def test_delete_keeps_generation_counter_visible_to_late_writers():
    """The finalizer drops the context hash but must leave the
    generation counter in place so a late writer from a previous attempt
    can still observe the new generation after a retry and drop its write.
    """
    redis = fakeredis.FakeRedis()
    store = PipelineContextStore(redis, "job-5")
    store.initialize({"audio_path": "/tmp/a.wav"})
    redis.set(store.generation_key, 5)

    store.delete()

    assert store.load() == {}
    assert store.get_generation() == 5
