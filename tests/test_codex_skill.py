from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from jobagent import __version__
from jobagent.infra import codex_skill


ROOT = Path(__file__).resolve().parents[1]


def snapshot(root: Path) -> dict[str, bytes]:
    return {str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*") if path.is_file()}


def test_source_and_packaged_skill_are_identical_and_valid():
    bundle = codex_skill._bundle()
    assert set(bundle) == {"SKILL.md", "agents/openai.yaml"}
    for name, data in bundle.items():
        assert data == (ROOT / "skills" / "codex-job-agent" / name).read_bytes()
    frontmatter = yaml.safe_load(bundle["SKILL.md"].decode().split("---", 2)[1])
    assert frontmatter["name"] == "codex-job-agent"
    assert "native Computer Use" in frontmatter["description"]
    metadata = yaml.safe_load(bundle["agents/openai.yaml"])
    assert "$codex-job-agent" in metadata["interface"]["default_prompt"]
    assert 25 <= len(metadata["interface"]["short_description"]) <= 64
    assert metadata.get("policy", {}).get("allow_implicit_invocation", True)


def test_contract_returns_current_full_instructions_without_writing(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    result = codex_skill.skill_contract()
    assert result["ok"] is True
    assert result["client_version"] == __version__
    assert result["protocol"] == "jobagent.browser_work"
    assert result["protocol_version"] == 1
    assert result["instructions"] == codex_skill._bundle()["SKILL.md"].decode()
    assert result["file_sha256"] == {name: codex_skill._digest(data) for name, data in codex_skill._bundle().items()}
    assert result["next_suggested"] == "jobagent work next"
    assert list(tmp_path.iterdir()) == []


def test_install_is_idempotent_and_does_not_touch_business_state(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    business = tmp_path / ".jobagent"
    business.mkdir()
    (business / "sentinel.json").write_text('{"unchanged": true}')
    first = codex_skill.install_skill()
    target = tmp_path / "codex" / "skills" / "codex-job-agent"
    assert first["ok"] and first["status"] == "installed"
    assert first["target"] == str(target)
    before = snapshot(tmp_path)
    mtimes = {path: path.stat().st_mtime_ns for path in target.rglob("*") if path.is_file()}
    second = codex_skill.install_skill()
    assert second["ok"] and second["status"] == "current"
    assert second["updated_files"] == []
    assert snapshot(tmp_path) == before
    assert {path: path.stat().st_mtime_ns for path in mtimes} == mtimes


def test_default_target_without_codex_home(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.delenv("CODEX_HOME", raising=False)
    result = codex_skill.install_skill()
    assert result["target"] == str(tmp_path / ".codex" / "skills" / "codex-job-agent")


@pytest.mark.parametrize("contents", [None, b"User's custom skill", codex_skill._bundle()["SKILL.md"]])
def test_existing_unmanaged_skill_is_never_adopted(contents, tmp_path):
    target = tmp_path / "custom"
    target.mkdir()
    if contents is not None:
        (target / "SKILL.md").write_bytes(contents)
    before = snapshot(tmp_path)
    result = codex_skill.install_skill(target)
    assert result["error"] == "codex_skill_unmanaged_target"
    assert result["conflicts"]
    assert snapshot(tmp_path) == before


def test_update_preserves_extra_user_files_and_replaces_only_managed_bytes(monkeypatch, tmp_path):
    target = tmp_path / "skill"
    assert codex_skill.install_skill(target)["ok"]
    (target / "my-notes.md").write_text("User customizations stay here.")
    before = codex_skill._bundle()
    updated = {**before, "SKILL.md": before["SKILL.md"] + b"\nUpdated product instructions.\n"}
    monkeypatch.setattr(codex_skill, "_bundle", lambda: updated)
    result = codex_skill.install_skill(target)
    assert result["ok"] and result["status"] == "updated"
    assert result["updated_files"] == ["SKILL.md"]
    assert (target / "my-notes.md").read_text() == "User customizations stay here."
    assert (target / "SKILL.md").read_bytes() == updated["SKILL.md"]
    assert codex_skill.install_skill(target)["status"] == "current"


@pytest.mark.parametrize("name", codex_skill.FILES)
def test_modified_managed_file_blocks_all_updates(name, monkeypatch, tmp_path):
    target = tmp_path / "skill"
    assert codex_skill.install_skill(target)["ok"]
    (target / name).write_text("User changed this product file.")
    before = snapshot(tmp_path)
    bundle = {key: value + b"\nNew release.\n" for key, value in codex_skill._bundle().items()}
    monkeypatch.setattr(codex_skill, "_bundle", lambda: bundle)
    result = codex_skill.install_skill(target)
    assert result["error"] == "codex_skill_local_changes"
    assert str(target / name) in result["conflicts"]
    assert snapshot(tmp_path) == before


def test_missing_managed_file_is_not_silently_recreated(tmp_path):
    target = tmp_path / "skill"
    assert codex_skill.install_skill(target)["ok"]
    (target / "SKILL.md").unlink()
    result = codex_skill.install_skill(target)
    assert result["error"] == "codex_skill_local_changes"
    assert not (target / "SKILL.md").exists()


@pytest.mark.parametrize("marker", [
    "broken", "[]", '{"schema_version": 1, "owner": "someone-else", "files": {}}',
    json.dumps({"schema_version": True, "owner": codex_skill.OWNER, "files": {"SKILL.md": "0" * 64}}),
])
def test_invalid_marker_preserves_target(marker, tmp_path):
    target = tmp_path / "skill"
    target.mkdir()
    (target / codex_skill.MARKER).write_text(marker)
    before = snapshot(tmp_path)
    assert codex_skill.install_skill(target)["error"] == "codex_skill_invalid_marker"
    assert snapshot(tmp_path) == before


def test_marker_cannot_claim_a_path_outside_skill(tmp_path):
    target = tmp_path / "skill"
    target.mkdir()
    outside = tmp_path / "user.txt"
    outside.write_text("private user content")
    (target / codex_skill.MARKER).write_text(json.dumps({
        "owner": codex_skill.OWNER, "schema_version": 1,
        "files": {"../user.txt": codex_skill._digest(outside.read_bytes())},
    }))
    before = snapshot(tmp_path)
    assert codex_skill.install_skill(target)["error"] == "codex_skill_invalid_marker"
    assert snapshot(tmp_path) == before


@pytest.mark.parametrize("name", ["C:/user.txt", "C:user.txt", "/user.txt", "../user.txt", "agents/../../user.txt", "agents\\user.txt"])
def test_marker_paths_are_relative_on_every_supported_os(name, tmp_path):
    target = tmp_path / "skill"
    target.mkdir()
    (target / codex_skill.MARKER).write_text(json.dumps({
        "owner": codex_skill.OWNER, "schema_version": 1, "files": {name: "0" * 64},
    }))
    before = snapshot(tmp_path)
    assert codex_skill.install_skill(target)["error"] == "codex_skill_invalid_marker"
    assert snapshot(tmp_path) == before


def test_symlink_target_is_not_followed(tmp_path):
    outside = tmp_path / "user"
    outside.mkdir()
    (outside / "SKILL.md").write_text("custom")
    target = tmp_path / "skill"
    target.symlink_to(outside, target_is_directory=True)
    assert codex_skill.install_skill(target)["error"] == "codex_skill_target_conflict"
    assert (outside / "SKILL.md").read_text() == "custom"


def test_managed_subdirectory_symlink_is_not_followed(tmp_path):
    target = tmp_path / "skill"
    assert codex_skill.install_skill(target)["ok"]
    outside = tmp_path / "user-agents"
    (target / "agents").rename(outside)
    (target / "agents").symlink_to(outside, target_is_directory=True)
    before = (outside / "openai.yaml").read_bytes()
    assert codex_skill.install_skill(target)["error"] == "codex_skill_local_changes"
    assert (outside / "openai.yaml").read_bytes() == before


def test_existing_install_lock_is_not_removed_or_stolen(tmp_path):
    target = tmp_path / "skill"
    lock = tmp_path / ".skill.jobagent-install.lock"
    lock.write_text("another installer")
    assert codex_skill.install_skill(target)["error"] == "codex_skill_install_busy"
    assert lock.read_text() == "another installer"
    assert not target.exists()


def test_interrupted_upgrade_can_resume_without_overwriting_user_changes(monkeypatch, tmp_path):
    target = tmp_path / "skill"
    assert codex_skill.install_skill(target)["ok"]
    original = codex_skill._bundle()
    new = {name: data + b"\nNew product release\n" for name, data in original.items()}
    monkeypatch.setattr(codex_skill, "_bundle", lambda: new)
    real_write = codex_skill._atomic_write

    def interrupted(path, data):
        if path.name == "openai.yaml":
            raise OSError("interrupted")
        real_write(path, data)

    monkeypatch.setattr(codex_skill, "_atomic_write", interrupted)
    assert codex_skill.install_skill(target)["ok"] is False
    assert (target / "SKILL.md").read_bytes() == new["SKILL.md"]
    monkeypatch.setattr(codex_skill, "_atomic_write", real_write)
    assert codex_skill.install_skill(target)["ok"]
    assert codex_skill.install_skill(target)["status"] == "current"


def test_install_cli_conflict_returns_nonzero_without_overwrite(monkeypatch, tmp_path, capsys):
    target = tmp_path / "custom"
    target.mkdir()
    monkeypatch.setattr("sys.argv", ["codex_skill", "install", "--target", str(target)])
    assert codex_skill.main() == 1
    assert json.loads(capsys.readouterr().out)["error"] == "codex_skill_unmanaged_target"
    assert list(target.iterdir()) == []
