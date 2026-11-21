"""The chaos benchmark: a small live run, the interval math and the shipped results file."""

import json
from pathlib import Path

import pytest

from agentqueue import chaos

ROOT = Path(__file__).resolve().parents[1]


def test_small_bench_run_keeps_the_invariants(tmp_path):
    r = chaos.bench_run("high", seed=11, workdir=tmp_path, n_jobs=12)
    assert r["not_finished"] == 0
    assert r["duplicate_effects"] == 0 and r["missing_effects"] == 0
    assert r["limit_violations"] == []
    assert r["kills"] >= 1 and r["provider_requests"] >= r["provider_completions"]
    assert r["succeeded"] + r["dead"] == 12


def test_interval():
    got = chaos.interval([1.0, 2.0, 3.0, 4.0, 5.0])
    assert got["mean"] == 3.0
    assert got["hi"] - got["mean"] == pytest.approx(2.776 * (2.5**0.5) / 5**0.5)
    assert chaos.interval([2.0])["lo"] is None


def test_percentile():
    assert chaos._pct([1.0, 2.0, 3.0, 4.0], 50) == 2.5
    assert chaos._pct([], 50) is None


@pytest.fixture(scope="module")
def results():
    return json.loads((ROOT / "results" / "bench.json").read_text())


def test_results_cover_three_levels_and_five_seeds(results):
    """Done means 6: three fault levels, five or more seeds each."""
    for lv in ("low", "medium", "high"):
        s = results["summary"][lv]
        assert len(s["seeds"]) >= 5
        for m in (
            "completion_rate",
            "wasted_calls",
            "p50_latency_s",
            "p95_latency_s",
            "requests_per_min",
        ):
            assert s[m]["n"] >= 5 and s[m]["lo"] is not None


def test_results_have_zero_duplicate_effects(results):
    """Done means 3: zero duplicate side effects at the receiver in every outbox run."""
    runs = [r for r in results["runs"] if not r["naive"]]
    assert len(runs) >= 15
    assert all(r["duplicate_effects"] == 0 and r["missing_effects"] == 0 for r in runs)


def test_results_never_break_a_limit(results):
    """Done means 4: the request log of every run passes the token bucket audit."""
    assert all(r["limit_violations"] == [] for r in results["runs"])
    assert all(r["not_finished"] == 0 for r in results["runs"])
