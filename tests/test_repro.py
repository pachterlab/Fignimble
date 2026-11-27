"""Reproducibility: exports/ and fignimble.lock written as a project changes, the check against them, recipes run
again by reproduce, and versions tagged in git and compared.

    PYTHONPATH=engine python -m pytest tests/test_repro.py
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "engine"))

from fignimble import cli, core, repro, templates  # noqa: E402
from fignimble.render import Renderer  # noqa: E402

PANEL = '''import numpy as np


def draw(fig, k=1.0):
    ax = fig.add_subplot()
    x = np.arange(10)
    ax.plot(x, x * k)
    ax.set_xticks([])
    ax.set_yticks([])
'''

FIG = '''from fignimble import *

fig = Figure(width=80, height=40)
fig.panel("panels/line.py", x=2, y=2, w=36, h=36)
fig.panel("panels/line.py", x=42, y=2, w=36, h=36, args={"k": 2.0})
'''

TABLE = '''from fignimble import *

tab = Table(rows=[["gene", "fold"], ["CD3E", 2.5], ["CD8A", 1.25]], title="Genes")
'''

GIT = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com", "GIT_COMMITTER_NAME": "t",
       "GIT_COMMITTER_EMAIL": "t@example.com"}


@pytest.fixture
def project(tmp_path, monkeypatch):
    d = tmp_path / "proj"
    (d / "panels").mkdir(parents=True)
    templates.write_marker(d)
    (d / "panels" / "line.py").write_text(PANEL)
    (d / "fig1.py").write_text(FIG)
    (d / "table1.py").write_text(TABLE)
    monkeypatch.chdir(d)
    for k, v in GIT.items():
        monkeypatch.setenv(k, v)
    return d


def git(d, *args):
    return subprocess.run(["git", *args], cwd=d, capture_output=True, text=True, check=True).stdout


def commit_all(d, msg="work"):
    git(d, "add", "-A")
    git(d, "commit", "-qm", msg)


def test_record_writes_exports_and_lock_once(project):
    res = repro.record(Renderer(str(project)))
    assert not res["errors"]
    assert (project / "exports" / "fig1.pdf").is_file()
    assert (project / "exports" / "table1.csv").read_text().startswith("gene,fold")
    data = sorted(p.name for p in (project / "exports" / "data" / "fig1").iterdir())
    assert data == ["a.csv", "b.csv"]
    lock = json.loads((project / "fignimble.lock").read_text())
    e = lock["exports"]["exports/fig1.pdf"]
    assert e["figure"] == "fig1.py" and "panels/line.py" in e["sources"] and set(e["panels"]) == {"a", "b"}
    before = {p: p.stat().st_mtime_ns for p in (project / "exports").rglob("*") if p.is_file()}
    again = repro.record(Renderer(str(project)))
    assert again["written"] == [] and again["lock"] is False           # nothing changed: nothing rewritten
    assert before == {p: p.stat().st_mtime_ns for p in (project / "exports").rglob("*") if p.is_file()}
    assert repro.check(project)["ok"]


def test_exports_are_not_inputs(project):
    """Writing exports/ and the lock doesn't change what figures depend on (no render loop in the app)."""
    r = Renderer(str(project))
    fp = r.fingerprint()
    repro.record(r)
    assert Renderer(str(project)).fingerprint() == fp


def test_check_finds_stale_exports_and_values_that_dont_show(project):
    r = Renderer(str(project))
    repro.record(r)
    # the panels have no ticks: scaling the line changes its values, not its picture
    (project / "panels" / "line.py").write_text(PANEL.replace("x * k", "x * k * 3"))
    c = repro.check(project)
    states = {it["path"]: it["state"] for it in c["items"]}
    assert states["exports/fig1.pdf"] == "stale" and not c["ok"]
    c = repro.check(project, Renderer(str(project)))
    assert {it["path"]: it["state"] for it in c["items"]}["exports/fig1.pdf"] == "current"   # looks the same
    res = repro.record(Renderer(str(project)))
    assert "exports/data/fig1/a.csv" in res["written"]                  # but its values were written again
    assert repro.check(project)["ok"]


def test_figure_removed_takes_its_exports(project):
    repro.record(Renderer(str(project)))
    (project / "table1.py").unlink()
    res = repro.record(Renderer(str(project)))
    assert "exports/table1.csv" in res["removed"] and not (project / "exports" / "table1.csv").exists()


RECIPES = '''
[env.here]
command = "env"

[[recipe]]
name = "dots"
outputs = ["out/dots.csv"]
data = ["out/dots.csv"]
run = "'{py}' make.py"
inputs = ["make.py", "raw.csv"]
env = "here"
resources = {{ time = "30 s" }}

[[recipe]]
name = "sum"
outputs = ["out/sum.txt"]
run = "'{py}' -c \\"print(sum(float(l.split(',')[1]) for l in open('out/dots.csv').read().split()[1:]), file=open('out/sum.txt', 'w'))\\""
inputs = ["out/dots.csv"]

[[recipe]]
outputs = ["images/*.svg"]
manual = "icons drawn by hand"
'''

MAKE = '''import csv
rows = list(csv.reader(open("raw.csv")))
with open("out/dots.csv", "w") as f:
    f.write("i,y\\n")
    for i, (v,) in enumerate(rows):
        f.write(f"{i},{float(v) * 2}\\n")
'''


@pytest.fixture
def recipes(project):
    with open(project / "fignimble.toml", "a") as f:
        f.write(RECIPES.format(py=sys.executable))
    (project / "make.py").write_text(MAKE)
    (project / "raw.csv").write_text("1\n2\n3\n")
    (project / "out").mkdir()
    (project / "images").mkdir()
    (project / "images" / "icon.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg" width="4" height="4"/>')
    return project


def test_config_reads_recipes_and_says_whats_wrong(recipes):
    cfg = repro.load_config(recipes)
    assert [r.name for r in cfg.recipes] == ["dots", "sum", "images"] and not cfg.problems
    assert [r.name for r in repro._order(list(reversed(cfg.recipes)), recipes)][:2] == ["images", "dots"]
    with open(recipes / "fignimble.toml", "a") as f:
        f.write('\n[[recipe]]\noutputs = ["x.png"]\nenv = "nowhere"\nwhen = 1\n')
    probs = " ".join(repro.load_config(recipes).problems)
    assert "neither run" in probs and "nowhere" in probs and "unknown key when" in probs


def test_reproduce_runs_in_order_compares_and_puts_back(recipes):
    res = repro.reproduce(recipes, check_only=False)
    by = {r["recipe"]: r for r in res["results"]}
    assert by["dots"]["verdict"] == "differs" and "weren't there before" in by["dots"]["detail"]
    assert by["images"]["verdict"] == "not-run" and by["images"]["why"] == "manual"
    assert (recipes / "out" / "sum.txt").read_text().strip() == "12.0"
    lock = repro.read_lock(recipes)
    assert lock["outputs"]["out/dots.csv"]["how"] == "made"
    # the same again: exact, and with --check the files are put back as they were
    (recipes / "out" / "sum.txt").write_text("kept\n")
    res = repro.reproduce(recipes, check_only=True)
    by = {r["recipe"]: r for r in res["results"]}
    assert by["dots"]["verdict"] == "exact"
    assert by["sum"]["verdict"] == "differs"                     # "kept" was there, 12.0 was made
    assert (recipes / "out" / "sum.txt").read_text() == "kept\n"   # put back
    # a changed input: stale in the check; a different number when made again
    (recipes / "raw.csv").write_text("1\n2\n4\n")
    states = {it["path"]: it for it in repro.check(recipes)["items"]}
    assert states["out/dots.csv"]["state"] == "stale" and "raw.csv changed" in states["out/dots.csv"]["detail"]
    res = repro.reproduce(recipes, check_only=True, only=["dots"])
    assert res["results"][0]["verdict"] == "differs" and "1 of 8 cells" in res["results"][0]["detail"]


def test_reproduce_budget_and_missing_inputs(recipes):
    (recipes / "raw.csv").unlink()
    res = repro.reproduce(recipes, check_only=True)
    by = {r["recipe"]: r for r in res["results"]}
    assert by["dots"]["why"] == "missing-data" and by["sum"]["why"] == "missing-data"
    (recipes / "raw.csv").write_text("1\n")
    res = repro.reproduce(recipes, check_only=True, budget="5 s", only=["dots"])
    assert res["results"][0]["why"] == "resources"


def test_tolerance():
    assert repro.compare_tables(b"x,y\n1,2.0000001\n", b"x,y\n1,2.0\n", 1e-6, 0)[0] == repro.WITHIN
    assert repro.compare_tables(b"x,y\n1,2.1\n", b"x,y\n1,2.0\n", 1e-6, 0)[0] == repro.DIFFERS
    assert repro.compare_tables(b"x\n1\n", b"x\n1\n2\n", 1e-6, 0)[0] == repro.DIFFERS
    assert repro.parse_duration("1h30m") == 5400 and repro.parse_duration("20 min") == 1200
    assert repro.parse_duration("soon") is None


def test_tag_and_diff(project):
    with pytest.raises(repro.GitError, match="isn't in a git repository"):
        repro.tag(project, "v1", renderer=Renderer(str(project)))
    git(project, "init", "-q")
    (project / ".gitignore").write_text(".fignimble/\n")
    with pytest.raises(repro.GitError, match="commit the project first"):
        repro.tag(project, "v1", renderer=Renderer(str(project)))
    commit_all(project)
    res = repro.tag(project, "submitted", renderer=Renderer(str(project)))
    assert res["tag"] == "fignimble/submitted"
    assert "exports/fig1.pdf" in git(project, "ls-files") and git(project, "status", "--porcelain") == ""
    assert [t["name"] for t in repro.tags(project)] == ["submitted"]
    with pytest.raises(repro.GitError, match="already"):
        repro.tag(project, "submitted", renderer=Renderer(str(project)))
    d = repro.diff(project, renderer=Renderer(str(project)))
    assert d["same"] and d["a"] == "submitted"
    # change a panel: its values and its picture differ, and the diff says from what
    (project / "panels" / "line.py").write_text(PANEL.replace("ax.plot(x, x * k)", "ax.plot(x, x * k, lw=4)\n"
                                                              "    ax.plot(x, -x)"))
    d = repro.diff(project, renderer=Renderer(str(project)))
    assert not d["same"]
    looks = {it["path"]: it for it in d["looks"]}
    assert looks["exports/fig1.pdf"]["verdict"] == "differs"
    assert set(looks["exports/fig1.pdf"]["panels"]) == {"a", "b"}
    assert "panels/line.py" in looks["exports/fig1.pdf"]["because"]
    assert Path(looks["exports/fig1.pdf"]["highlight"]).is_file()
    assert any(h["name"] == "panels/line.py" for h in d["how"])
    # two versions by name; the tag commits the exports the change made
    git(project, "add", "panels")
    git(project, "commit", "-qm", "thicker")
    res = repro.tag(project, "revision-1", renderer=Renderer(str(project)))
    assert res["committed"] and git(project, "status", "--porcelain") == ""
    d = repro.diff(project, "submitted", "revision-1")
    assert not d["same"] and d["b"] == "revision-1"


def test_cli_check_json_and_tags(project, capsys):
    assert cli.main(["record"]) == 0
    capsys.readouterr()
    assert cli.main(["check", "--fast", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["reproducibility"]["ok"] and out["reproducibility"]["summary"]["current"] >= 3
    assert cli.main(["tags"]) == 1                          # not a git repository


def test_session_records_after_changes(project, monkeypatch):
    monkeypatch.setattr(core, "RECORD_AFTER", 0.2)
    s = core.Session(str(project))
    try:
        deadline = time.time() + 120
        while time.time() < deadline and not (project / "fignimble.lock").exists():
            time.sleep(0.3)
        assert (project / "exports" / "fig1.pdf").is_file()
        first = (project / "exports" / "fig1.pdf").read_bytes()
        (project / "panels" / "line.py").write_text(PANEL.replace("ax.plot(x, x * k)", "ax.plot(x, x * k, lw=5)"))
        deadline = time.time() + 120
        while time.time() < deadline and (project / "exports" / "fig1.pdf").read_bytes() == first:
            time.sleep(0.3)
        assert (project / "exports" / "fig1.pdf").read_bytes() != first
        while s.recording:
            time.sleep(0.1)
        assert repro.check(project)["ok"]
    finally:
        s.close()


def test_session_records_only_marked_projects(tmp_path):
    d = tmp_path / "old"
    (d / "panels").mkdir(parents=True)
    (d / "panels" / "line.py").write_text(PANEL)
    (d / "fig1.py").write_text(FIG)
    s = core.Session(str(d))
    try:
        assert not s.records()
        (d / templates.MARKER).write_text(templates.marker_text() + "\n[exports]\non_save = false\n")
        assert not s.records()
    finally:
        s.close()
