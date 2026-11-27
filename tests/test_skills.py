"""The agent skills: each SKILL.md is well formed, and `fignimble install-skills` installs, updates and removes them
without touching folders it didn't make."""

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "engine"))

from fignimble import skills  # noqa: E402


def test_skills_are_well_formed():
    found = skills.available()
    assert {s.name for s in found} >= {"fignimble", "fignimble-new-figure", "fignimble-from-notebook",
                                       "fignimble-journal", "fignimble-poster", "fignimble-new-table",
                                       "fignimble-reproduce"}
    for s in found:
        text = (s.path / "SKILL.md").read_text(encoding="utf-8")
        front = re.match(r"---\n(.*?)\n---\n", text, re.S)
        assert front, s.name
        assert re.search(rf"^name: {re.escape(s.name)}$", front.group(1), re.M), s.name
        assert 50 < len(s.description) <= 1024, s.name


def test_plugin_manifests_agree():
    version = (ROOT / "VERSION").read_text().strip()
    for f in (".claude-plugin/plugin.json", ".codex-plugin/plugin.json"):
        m = json.loads((ROOT / f).read_text())
        assert m["name"] == "fignimble" and m["version"] == version and m["skills"] == "./skills/", f


def test_install_update_uninstall(tmp_path):
    found = skills.available()
    assert all(o == "installed" for _, o in skills.install(found, tmp_path))
    assert all(o == "unchanged" for _, o in skills.install(found, tmp_path))
    (tmp_path / "fignimble" / "SKILL.md").write_text("old")
    assert dict((s.name, o) for s, o in skills.install(found, tmp_path))["fignimble"] == "updated"
    mine = tmp_path / "fignimble-poster"
    skills.uninstall(skills.select(found, ["poster"]), tmp_path)
    mine.mkdir()
    (mine / "SKILL.md").write_text("the user's own")
    assert dict((s.name, o) for s, o in skills.install(found, tmp_path))["fignimble-poster"] == "skipped"
    assert dict((s.name, o) for s, o in skills.uninstall(found, tmp_path))["fignimble-poster"] == "kept"
    assert (mine / "SKILL.md").read_text() == "the user's own"
    assert not (tmp_path / "fignimble").exists()
