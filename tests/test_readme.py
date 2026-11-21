"""The README and the results page come from the result files, and the numbers match."""

import json
import re
from pathlib import Path

from agentqueue import report

ROOT = Path(__file__).resolve().parents[1]


def test_generated_files_are_up_to_date():
    """Broken case: someone edits a number in the README by hand."""
    assert report.main(check=True) == 0


def test_check_catches_a_hand_edit(tmp_path, monkeypatch):
    fake_root = tmp_path
    (fake_root / "docs").mkdir()
    (fake_root / "README.md").write_text(report.render_readme().replace("Completion rate", "Done"))
    (fake_root / "docs" / "index.html").write_text(report.render_index())
    monkeypatch.setattr(report, "ROOT", fake_root)
    assert report.main(check=True) == 1


def test_table_numbers_match_results():
    bench = json.loads((ROOT / "results" / "bench.json").read_text())
    readme = (ROOT / "README.md").read_text()
    for lv in ("low", "medium", "high"):
        s = bench["summary"][lv]
        row = next(ln for ln in readme.splitlines() if ln.startswith(f"| {lv} |"))
        cells = [c.strip() for c in row.strip("|").split("|")]
        rate = float(re.match(r"([\d.]+)%", cells[1]).group(1))
        assert abs(rate - 100 * s["completion_rate"]["mean"]) < 0.051
        wasted = float(cells[2].split()[0])
        assert abs(wasted - s["wasted_calls"]["mean"]) < 0.051
        assert cells[7] == str(s["limit_violations"])


def test_readme_states_seeds_and_fault_rates():
    readme = (ROOT / "README.md").read_text()
    bench = json.loads((ROOT / "results" / "bench.json").read_text())
    assert "Seeds: " + ", ".join(str(s) for s in bench["meta"]["seeds"]) in readme
    assert "| Level | 429 | 500 | Timeout | Slow | Dropped | Worker SIGKILL |" in readme
    assert "fake provider" in readme
