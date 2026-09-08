from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("native_package_gate", ROOT / "scripts/ci/native_package_gate.py")
gate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gate)


@pytest.fixture
def source_tree(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\nversion = "1.2.3"\n', encoding="utf-8")
    (tmp_path / "src/jobagent").mkdir(parents=True)
    (tmp_path / "src/jobagent/__init__.py").write_text('__version__ = "1.2.3"\n', encoding="utf-8")
    contents = {
        "SKILL.md": b"---\nname: codex-job-agent\ndescription: Native job application workflow.\n---\n\nObserve, confirm, and report.\n",
        "agents/openai.yaml": b'interface:\n  display_name: "Job Agent for Codex"\n  short_description: "User-confirmed job applications through native UI"\n  default_prompt: "Use $codex-job-agent for this task."\n',
    }
    for prefix in ("skills/codex-job-agent", "src/jobagent/data/codex_skill"):
        for name, data in contents.items():
            path = tmp_path / prefix / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
    return tmp_path


def change_both(root, name, text):
    for prefix in ("skills/codex-job-agent", "src/jobagent/data/codex_skill"):
        (root / prefix / name).write_text(text, encoding="utf-8")


def test_skill_source_and_resources_are_valid_without_frontmatter_version(source_tree):
    expected = gate.validate_sources(source_tree)
    assert expected["client_version"] == "1.2.3"
    assert set(expected["file_sha256"]) == set(gate.FILES)
    assert all(len(digest) == 64 for digest in expected["file_sha256"].values())
    assert "version:" not in expected["instructions"]


@pytest.mark.parametrize("name", gate.FILES)
def test_mismatched_distribution_bytes_fail(source_tree, name):
    path = source_tree / "src/jobagent/data/codex_skill" / name
    path.write_bytes(path.read_bytes() + b"\n")
    with pytest.raises(ValueError, match="source/package Skill mismatch"):
        gate.validate_sources(source_tree)


@pytest.mark.parametrize("frontmatter", [
    "name: codex-job-agent\ndescription: Native workflow\nversion: 1.2.3",
    "name: codex-job-agent\nname: other\ndescription: Native workflow",
    "name: other\ndescription: Native workflow",
    "name: codex-job-agent\ndescription: ''",
    "name: codex-job-agent\ndescription: ['invalid']",
])
def test_invalid_or_ambiguous_frontmatter_fails(source_tree, frontmatter):
    change_both(source_tree, "SKILL.md", f"---\n{frontmatter}\n---\nInstructions\n")
    with pytest.raises(ValueError):
        gate.validate_sources(source_tree)


def test_missing_frontmatter_fails(source_tree):
    change_both(source_tree, "SKILL.md", "No frontmatter.\n")
    with pytest.raises(ValueError, match="frontmatter"):
        gate.validate_sources(source_tree)


def test_invalid_interface_metadata_fails(source_tree):
    change_both(source_tree, "agents/openai.yaml", "interface:\n  display_name: Example\n  short_description: Short\n")
    with pytest.raises(ValueError, match="short description"):
        gate.validate_sources(source_tree)


def test_project_package_version_mismatch_fails(source_tree):
    (source_tree / "src/jobagent/__init__.py").write_text('__version__ = "9.9.9"\n', encoding="utf-8")
    with pytest.raises(ValueError, match="versions differ"):
        gate.validate_sources(source_tree)


def test_contract_version_hashes_and_complete_instructions_match(source_tree):
    expected = gate.validate_sources(source_tree)
    contract = {
        **expected, "ok": True, "event": "codex_skill_contract", "skill_name": "codex-job-agent",
        "protocol": "jobagent.browser_work", "protocol_version": 1, "next_suggested": "jobagent work next",
    }
    gate.validate_contract(contract, expected)
    for key, value in (("client_version", "0.0.0"), ("instructions", "truncated"), ("file_sha256", {}), ("protocol_version", True)):
        broken = copy.deepcopy(contract)
        broken[key] = value
        with pytest.raises(ValueError):
            gate.validate_contract(broken, expected)


def test_isolation_replaces_all_homes_and_drops_runtime_credentials(tmp_path):
    inherited = {"HOME": "/real-user", "USERPROFILE": "/real-user", "CODEX_HOME": "/real-codex", "JOBAGENT_API_KEY": "must-not-be-used", "AGENTMESH_API_KEY": "must-not-be-used", "PYTHONPATH": "/source", "VIRTUAL_ENV": "/old-venv", "PATH": "keep-path"}
    env = gate.isolated_env(tmp_path, inherited)
    assert inherited["HOME"] == "/real-user"
    assert env["PATH"] == "keep-path"
    assert env["JOBAGENT_SKIP_UPDATE"] == "1"
    assert not any(key in env for key in ("JOBAGENT_API_KEY", "AGENTMESH_API_KEY", "PYTHONPATH", "VIRTUAL_ENV"))
    for key in ("HOME", "USERPROFILE", "CODEX_HOME", "APPDATA", "LOCALAPPDATA", "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_DATA_HOME", "TMPDIR", "TMP", "TEMP"):
        assert Path(env[key]).is_relative_to(tmp_path)
        assert Path(env[key]).is_dir()


@pytest.mark.parametrize("event", ["socket.connect", "socket.getaddrinfo", "subprocess.Popen", "os.system"])
def test_installed_smoke_guard_rejects_network_and_process_audit_events(tmp_path, event):
    # Synthetic audit events verify the guard without opening sockets/processes.
    script = gate.GUARDED_MODULE.replace('runpy.run_module(module, run_name="__main__")', f"sys.audit({json.dumps(event)})")
    result = subprocess.run([sys.executable, "-I", "-c", script, "unused"], cwd=tmp_path, env=gate.isolated_env(tmp_path), capture_output=True, text=True, timeout=10)
    assert result.returncode != 0
    assert "forbidden smoke activity" in result.stderr


def _workflow(name):
    # PyYAML's YAML 1.1 loader parses the unquoted GitHub key `on` as True.
    return yaml.safe_load((ROOT / ".github/workflows" / name).read_text(encoding="utf-8"))


def test_native_workflow_is_reusable_and_covers_both_supported_ci_hosts():
    workflow = _workflow("native-client.yml")
    triggers = workflow.get("on", workflow.get(True))
    assert set(triggers) == {"pull_request", "push", "workflow_dispatch", "workflow_call"}
    job = workflow["jobs"]["package-and-protocol"]
    assert set(job["strategy"]["matrix"]["os"]) == {"ubuntu-latest", "windows-latest"}
    assert job["env"]["HOME"] == job["env"]["USERPROFILE"]
    assert "github.workspace" in job["env"]["CODEX_HOME"]
    assert all("runner." not in value for value in job["env"].values())
    steps = job["steps"]
    assert any(step.get("run") == "python -m pytest -q" and "Linux" in step.get("if", "") for step in steps)
    windows = next(step["run"] for step in steps if "Windows" in step.get("if", ""))
    for name in ("test_installers.py", "test_codex_skill.py", "test_native_command_lock.py", "test_atomic_state.py", "test_native_package_gate.py"):
        assert name in windows
    assert any("native_package_gate.py --wheel-dir dist" in step.get("run", "") for step in steps)


def test_release_cannot_bypass_same_sha_native_gate_and_retains_legacy_gate():
    workflow = _workflow("publish-release.yml")
    gate_job = workflow["jobs"]["native-gate"]
    assert gate_job["uses"] == "./.github/workflows/native-client.yml"
    publish = workflow["jobs"]["publish"]
    assert publish["needs"] == "native-gate"
    scripts = "\n".join(step.get("run", "") for step in publish["steps"])
    assert "liepin_collection_recovery_gate.py" in scripts
    assert "native_package_gate.py --wheel-dir dist" in scripts
    assert 'gh release view "$TAG"' in scripts
    assert '--target "$GITHUB_SHA"' in scripts
