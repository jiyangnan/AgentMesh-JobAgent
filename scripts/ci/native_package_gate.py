"""Offline native-contract checks plus an optional isolated wheel-install smoke.

Dependency installation may contact the package index. Smoke commands cannot
access the network or launch a browser/process. This is not a host Computer Use
acceptance test and never discovers jobs, sends applications, or reads a user
profile. No installed/local Codex skill is used as an input.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import tomllib

import yaml


FILES = ("SKILL.md", "agents/openai.yaml")
SKILL_NAME = "codex-job-agent"


def require(condition: object, message: str) -> None:
    if not condition:
        raise ValueError(message)


class UniqueSafeLoader(yaml.SafeLoader):
    """Reject ambiguous duplicate metadata keys rather than accepting the last."""


def _unique_mapping(loader, node, deep=False):
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        require(key not in result, f"duplicate YAML key: {key}")
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


UniqueSafeLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _unique_mapping)


def validate_sources(root: Path) -> dict:
    version = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    assignments = ast.parse((root / "src/jobagent/__init__.py").read_text(encoding="utf-8"))
    package_version = next(
        ast.literal_eval(node.value)
        for node in assignments.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "__version__" for target in node.targets)
    )
    require(package_version == version, "package and project versions differ")
    contents = {}
    for name in FILES:
        source = (root / "skills" / SKILL_NAME / name).read_bytes()
        packaged = (root / "src/jobagent/data/codex_skill" / name).read_bytes()
        require(source == packaged, f"source/package Skill mismatch: {name}")
        contents[name] = source

    text = contents["SKILL.md"].decode("utf-8")
    match = re.match(r"\A---\r?\n(.*?)\r?\n---(?:\r?\n|\Z)", text, re.DOTALL)
    require(match is not None, "Skill must start with YAML frontmatter")
    frontmatter = yaml.load(match.group(1), Loader=UniqueSafeLoader)
    require(isinstance(frontmatter, dict), "Skill frontmatter must be a mapping")
    require(set(frontmatter) == {"name", "description"}, "Codex Skill frontmatter contains unsupported fields; version belongs in skill_contract")
    require(frontmatter["name"] == SKILL_NAME, "unexpected Codex Skill name")
    description = frontmatter["description"]
    require(isinstance(description, str) and 1 <= len(description) <= 1024, "invalid Skill description")
    require("<" not in description and ">" not in description, "Skill description contains angle brackets")
    require(bool(text[match.end():].strip()), "Skill instructions are empty")

    metadata = yaml.load(contents["agents/openai.yaml"], Loader=UniqueSafeLoader)
    require(isinstance(metadata, dict) and isinstance(metadata.get("interface"), dict), "missing Codex interface metadata")
    interface = metadata["interface"]
    require(isinstance(interface.get("display_name"), str) and bool(interface["display_name"].strip()), "missing Skill display name")
    short = interface.get("short_description")
    require(isinstance(short, str) and 25 <= len(short) <= 64, "Skill short description must contain 25–64 characters")
    prompt = interface.get("default_prompt")
    require(isinstance(prompt, str) and f"${SKILL_NAME}" in prompt, "default prompt must name the Codex Skill")
    return {
        "client_version": version,
        "instructions": text,
        "file_sha256": {name: hashlib.sha256(data).hexdigest() for name, data in contents.items()},
    }


def isolated_env(directory: Path, inherited: dict[str, str] | None = None) -> dict[str, str]:
    env = dict(os.environ if inherited is None else inherited)
    for key in list(env):
        if key.upper().startswith(("JOBAGENT_", "AGENTMESH_", "CODEX_", "PYTHON")) or key.upper() in {"VIRTUAL_ENV", "PIP_TARGET", "PIP_PREFIX", "PIP_USER"}:
            env.pop(key)
    for key, relative in {
        "HOME": "home", "USERPROFILE": "home", "CODEX_HOME": "codex",
        "APPDATA": "appdata", "LOCALAPPDATA": "localappdata",
        "XDG_CONFIG_HOME": "config", "XDG_CACHE_HOME": "cache", "XDG_DATA_HOME": "data",
        "TMPDIR": "tmp", "TMP": "tmp", "TEMP": "tmp",
    }.items():
        path = directory / relative
        path.mkdir(parents=True, exist_ok=True)
        env[key] = str(path)
    env["PYTHONUTF8"] = "1"
    env["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
    # This gate checks the just-built wheel, not release-manifest availability.
    # Use the existing test/offline switch only in this disposable environment.
    env["JOBAGENT_SKIP_UPDATE"] = "1"
    return env


# Installed smoke commands are intentionally restricted to help, contract and
# package Skill installation. Audit hooks also reject accidental network/process
# regressions even if an application catches the resulting exception.
GUARDED_MODULE = r"""
import runpy, sys
blocked = []
def audit(event, args):
    if event.startswith("socket.") or event in {"subprocess.Popen", "os.system", "os.exec", "os.posix_spawn"}:
        blocked.append(event)
        raise RuntimeError("native package smoke forbids network and external processes")
sys.addaudithook(audit)
module = sys.argv.pop(1)
try:
    runpy.run_module(module, run_name="__main__")
finally:
    if blocked:
        raise RuntimeError("forbidden smoke activity: " + ", ".join(blocked))
"""


def _run(command: list[str], *, cwd: Path, env: dict[str, str], expected_code: int = 0, timeout: int = 120) -> str:
    result = subprocess.run(command, cwd=cwd, env=env, capture_output=True, text=True, encoding="utf-8", timeout=timeout)
    require(result.returncode == expected_code, f"smoke command failed ({result.returncode}): {result.stdout}\n{result.stderr}")
    return result.stdout


def validate_contract(contract: dict, expected: dict) -> None:
    require(contract.get("ok") is True and contract.get("event") == "codex_skill_contract", "invalid native Skill contract")
    require(contract.get("skill_name") == SKILL_NAME, "wrong contract Skill")
    require(contract.get("protocol") == "jobagent.browser_work" and type(contract.get("protocol_version")) is int and contract["protocol_version"] == 1, "unsupported browser work contract")
    for key in ("client_version", "instructions", "file_sha256"):
        require(contract.get(key) == expected[key], f"installed contract mismatch: {key}")
    require(contract.get("next_suggested") == "jobagent work next", "unexpected contract continuation")


def smoke_wheel(wheel: Path, expected: dict) -> None:
    with tempfile.TemporaryDirectory(prefix="jobagent-native-package-") as temporary:
        directory = Path(temporary).resolve()
        env = isolated_env(directory)
        venv = directory / "venv"
        _run([sys.executable, "-I", "-X", "utf8", "-m", "venv", str(venv)], cwd=directory, env=env)
        python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        # Require compatible published dependency wheels: the package gate must
        # not depend on a developer's compiler/Rust toolchain or user settings.
        _run([str(python), "-I", "-X", "utf8", "-m", "pip", "install", "--no-input", "--only-binary=:all:", str(wheel.resolve())], cwd=directory, env=env, timeout=600)
        location = _run([str(python), "-I", "-X", "utf8", "-c", "import jobagent; print(jobagent.__file__)"], cwd=directory, env=env).strip()
        require(Path(location).resolve().is_relative_to(venv), "smoke imported a source checkout instead of the wheel")

        def module(name, *args, expected_code=0):
            return _run([str(python), "-I", "-X", "utf8", "-c", GUARDED_MODULE, name, *args], cwd=directory, env=env, expected_code=expected_code)

        require(expected["client_version"] in module("jobagent", "--version"), "installed CLI version mismatch")
        module("jobagent", "zhilian", "--help")
        help_text = module("jobagent", "work", "--help")
        require(all(command in help_text for command in ("next", "begin", "submit", "status", "contract")), "native work commands missing from installed CLI")
        validate_contract(json.loads(module("jobagent.infra.codex_skill", "contract")), expected)
        first = json.loads(module("jobagent.infra.codex_skill", "install"))
        require(first.get("ok") is True and first.get("status") == "installed", "first temporary Codex install failed")
        target = directory / "codex/skills" / SKILL_NAME
        require(Path(first["target"]) == target, "Skill escaped the temporary Codex home")
        before = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in target.rglob("*") if path.is_file()}
        second = json.loads(module("jobagent.infra.codex_skill", "install"))
        require(second.get("ok") is True and second.get("status") == "current", "second Codex install is not idempotent")
        require(before == {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in target.rglob("*") if path.is_file()}, "idempotent installation rewrote files")
        custom = directory / "custom-skill"
        custom.mkdir()
        custom_text = b"User-maintained skill: must never be overwritten.\n"
        (custom / "SKILL.md").write_bytes(custom_text)
        conflict = json.loads(module("jobagent.infra.codex_skill", "install", "--target", str(custom), expected_code=1))
        require(conflict.get("error") == "codex_skill_unmanaged_target", "custom Skill did not fail closed")
        require(list(custom.iterdir()) == [custom / "SKILL.md"] and (custom / "SKILL.md").read_bytes() == custom_text, "custom Skill contents changed")
        require(not (directory / "home/.jobagent").exists(), "help/contract/Skill installation unexpectedly created business state")
        # Unlike the pure package contract above, the CLI entry point can create
        # upgrade/command-lock metadata. It is still confined to the empty temp
        # home and must work without account credentials or any network access.
        validate_contract(json.loads(module("jobagent", "work", "contract")), expected)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--wheel-dir", type=Path, help="Directory containing exactly one wheel to install into a fresh temporary venv")
    args = parser.parse_args()
    expected = validate_sources(args.root.resolve())
    if args.wheel_dir is not None:
        wheels = list(args.wheel_dir.glob("*.whl"))
        require(len(wheels) == 1, "wheel directory must contain exactly one wheel")
        smoke_wheel(wheels[0], expected)
    print(json.dumps({"ok": True, "client_version": expected["client_version"], "source_skill_valid": True, "isolated_wheel_smoke": args.wheel_dir is not None, "real_host_cua_tested": False}))


if __name__ == "__main__":
    main()
