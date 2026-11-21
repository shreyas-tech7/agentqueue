import json
import urllib.request

import pytest
from helpers import limits, make_store

from agentqueue import worker
from agentqueue.errors import PermanentError, RateLimited, ServerError, TransientError
from agentqueue.fakeprovider import FakeProvider, Faults
from agentqueue.receiver import _NO_PROXY
from agentqueue.worker import Context, Crasher


def call(fp, st=None):
    st = st or make_store_tmp()
    st.submit("k", {}, f"k{st.clock.now()}")
    st.clock.advance(1)
    ctx = Context(st, st.claim(), limits(fp.url), Crasher(None), 30)
    ctx._calls, ctx._step = [], "s"
    return ctx.model("hello")


_tmp = {}


def make_store_tmp():
    return _tmp["st"]


@pytest.fixture(autouse=True)
def _st(tmp_path):
    _tmp["st"] = make_store(tmp_path)


def test_listens_on_loopback_only():
    with FakeProvider() as fp:
        assert fp.url.startswith("http://127.0.0.1:")


def test_ok_reply_is_deterministic():
    with FakeProvider() as fp:
        a, b = call(fp), call(fp)
        assert a == b and len(a.split()) > 3
        assert fp.snapshot()["completions"] == 2


@pytest.mark.parametrize(
    "faults,exc",
    [
        (Faults(rate_429=1.0), RateLimited),
        (Faults(rate_500=1.0), ServerError),
        (Faults(rate_drop=1.0), TransientError),
        (Faults(rate_timeout=1.0, timeout_s=1.0), TransientError),
    ],
)
def test_injected_faults(faults, exc):
    with FakeProvider(faults) as fp, pytest.raises(exc):
        call(fp)


def test_429_carries_retry_after():
    with FakeProvider(Faults(rate_429=1.0, retry_after_s=0.7)) as fp:
        with pytest.raises(RateLimited) as e:
            call(fp)
        assert e.value.retry_after == pytest.approx(0.7)


def test_same_seed_same_faults():
    f = Faults(rate_429=0.2, rate_500=0.2, rate_slow=0.1, slow_s=0.0)
    seqs = []
    for _ in range(2):
        with FakeProvider(f, seed=9) as fp:
            got = []
            for _ in range(30):
                try:
                    call(fp)
                    got.append("ok")
                except Exception as e:
                    got.append(type(e).__name__)
            seqs.append(got)
    assert seqs[0] == seqs[1] and len(set(seqs[0])) >= 3


def test_receiver_dedupes_on_key():
    with FakeProvider() as fp:
        for key in ("a", "a", "b", None):
            h = {"Content-Type": "application/json"}
            if key:
                h["Idempotency-Key"] = key
            req = urllib.request.Request(fp.url + "/receive", data=b"{}", headers=h, method="POST")
            _NO_PROXY.open(req, timeout=5).read()
        snap = fp.snapshot()
        assert (
            snap["deliveries"],
            snap["redeliveries"],
            snap["effects"],
            snap["naive_effects"],
        ) == (3, 1, 2, 1)
        stats = json.loads(_NO_PROXY.open(fp.url + "/stats", timeout=5).read())
        assert stats["effects"] == 2


def test_a_400_is_permanent(tmp_path):
    with FakeProvider() as fp:
        st = make_store(tmp_path, "x.sqlite")
        st.submit("k", {}, "k")
        ctx = Context(st, st.claim(), limits(fp.url + "/nope"), Crasher(None), 30)
        ctx._calls, ctx._step = [], "s"
        with pytest.raises(PermanentError):
            ctx.model("x")


def test_summarize_and_classify_examples(tmp_path):
    import agentqueue.examples  # noqa: F401

    with FakeProvider() as fp:
        st = make_store(tmp_path, "e.sqlite")
        a, _ = st.submit("summarize", {"docs": ["one", "two"]}, "s")
        b, _ = st.submit("classify", {"ticket": "I was charged twice"}, "c")
        worker.work(st, limits(fp.url), until_empty=True, max_loops=100)
        assert st.job(a)["state"] == st.job(b)["state"] == "succeeded"
        assert json.loads(st.job(b)["result"])["label"] in ("billing", "bug", "question")
        assert " | " in json.loads(st.job(a)["result"])
        calls = st.conn.execute("SELECT step_name, status FROM calls").fetchall()
        assert {tuple(c) for c in calls} == {
            ("summarize-0", "ok"),
            ("summarize-1", "ok"),
            ("ask", "ok"),
        }


def test_duplicate_effects_are_measured(tmp_path):
    """Broken case: the outbox duplicate count was a constant 0. The same body under a second key
    is a duplicate the key check cannot catch, and it must show up."""
    import json
    import urllib.request

    from agentqueue.receiver import _NO_PROXY

    with FakeProvider() as fp:

        def post(key, body):
            req = urllib.request.Request(
                fp.url + "/receive",
                data=json.dumps(body).encode(),
                headers={"Content-Type": "application/json", "Idempotency-Key": key},
                method="POST",
            )
            _NO_PROXY.open(req, timeout=5).read()

        post("a", {"job": 1})
        post("a", {"job": 1})  # a redelivery, caught by the key
        assert fp.snapshot()["duplicate_effects"] == 0
        post("b", {"job": 1})  # the same effect under a new key
        snap = fp.snapshot()
        assert snap["duplicate_effects"] == 1 and snap["redeliveries"] == 1
