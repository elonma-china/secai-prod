"""Request-local prefetch evidence cannot leak across turns or concurrent sessions."""
import asyncio

import pytest

from secai_gateway.poc import prefetch


_CACHES = [
    ("coverage", "enough", None),
    ("cve_known", True, False),
    ("synthesis_evidence", True, False),
    ("real_code_evidence", True, False),
    ("kb_searched", True, False),
    ("kb_docs", "current KB description", None),
]


@pytest.fixture(autouse=True)
def clean_fallback():
    prefetch.clear_session_coverage()
    yield
    prefetch.clear_session_coverage()


@pytest.mark.parametrize("name,value,empty", _CACHES)
def test_new_request_does_not_read_or_overwrite_legacy_session_flags(name, value, empty):
    setter = getattr(prefetch, "set_session_" + name)
    getter = getattr(prefetch, "get_session_" + name)
    setter("shared", value)
    with prefetch.session_coverage_scope():
        assert getter("shared") == empty
        setter("shared", value)
        assert getter("shared") == value
        prefetch.clear_session_coverage()
        assert getter("shared") == empty
    assert getter("shared") == value
    with prefetch.session_coverage_scope():
        assert getter("shared") == empty


@pytest.mark.asyncio
async def test_same_session_concurrent_requests_are_isolated_and_tasks_inherit_scope():
    ready = asyncio.Event()
    release = asyncio.Event()

    async def first_request():
        with prefetch.session_coverage_scope():
            prefetch.set_session_kb_searched("shared", True)
            prefetch.set_session_kb_docs("shared", "request one docs")
            ready.set()
            await release.wait()

            async def child():
                return prefetch.get_session_kb_docs("shared")

            assert await asyncio.create_task(child()) == "request one docs"
            assert prefetch.get_session_kb_searched("shared") is True

    async def second_request():
        await ready.wait()
        with prefetch.session_coverage_scope():
            assert prefetch.get_session_kb_searched("shared") is False
            assert prefetch.get_session_kb_docs("shared") is None
            prefetch.set_session_kb_docs("shared", "request two docs")
            release.set()
            await asyncio.sleep(0)
            assert prefetch.get_session_kb_docs("shared") == "request two docs"

    await asyncio.gather(first_request(), second_request())
    assert prefetch.get_session_kb_docs("shared") is None


@pytest.mark.parametrize("error", [RuntimeError("failed"), asyncio.CancelledError()])
def test_scope_restores_outer_state_on_error_or_cancellation(error):
    with prefetch.session_coverage_scope():
        prefetch.set_session_kb_docs("shared", "outer")
        outer = prefetch.capture_session_coverage()
        with pytest.raises(type(error)):
            with prefetch.session_coverage_scope():
                prefetch.set_session_kb_docs("shared", "inner")
                raise error
        assert prefetch.capture_session_coverage() is outer
        assert prefetch.get_session_kb_docs("shared") == "outer"
    assert prefetch.capture_session_coverage() is None
    assert prefetch.get_session_kb_docs("shared") is None


def test_captured_scope_can_resume_in_stream_without_global_retention():
    with prefetch.session_coverage_scope() as state:
        prefetch.set_session_kb_docs("shared", "stream docs")
        assert prefetch.capture_session_coverage() is state
    assert prefetch.get_session_kb_docs("shared") is None
    with prefetch.session_coverage_scope(state):
        assert prefetch.get_session_kb_docs("shared") == "stream docs"
        prefetch.set_session_kb_searched("shared", True)
    assert prefetch.get_session_kb_searched("shared") is False


def test_clear_one_session_and_individual_cache_preserves_other_evidence():
    with prefetch.session_coverage_scope():
        for name, value, _empty in _CACHES:
            setter = getattr(prefetch, "set_session_" + name)
            setter("first", value)
            setter("second", value)
        prefetch.clear_session_kb_docs("first")
        assert prefetch.get_session_kb_docs("first") is None
        assert prefetch.get_session_kb_searched("first") is True
        prefetch.clear_session_coverage("first")
        for name, value, empty in _CACHES:
            getter = getattr(prefetch, "get_session_" + name)
            assert getter("first") == empty
            assert getter("second") == value
        prefetch.clear_session_kb_docs()
        assert prefetch.get_session_kb_docs("second") is None
        assert prefetch.get_session_kb_searched("second") is True
