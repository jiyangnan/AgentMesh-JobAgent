from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path

from jobagent.infra.release_update import POSIX_INSTALL_COMMAND, _archive_sha256


def _run(root: Path, *args: str) -> bytes:
    return subprocess.run(
        list(args),
        cwd=root,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    ).stdout


def test_archive_hash_ignores_machine_git_archive_configuration(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _run(repo, "git", "init", "-q")
    _run(repo, "git", "config", "user.name", "Job Agent Test")
    _run(repo, "git", "config", "user.email", "test@localhost")
    (repo / "file.txt").write_text("canonical release\n", encoding="utf-8")
    _run(repo, "git", "add", "file.txt")
    _run(repo, "git", "commit", "-qm", "fixture")
    commit = _run(repo, "git", "rev-parse", "HEAD").decode().strip()

    external_attributes = tmp_path / "global-attributes"
    external_attributes.write_text("* export-ignore\n", encoding="utf-8")
    _run(repo, "git", "config", "tar.umask", "077")
    _run(repo, "git", "config", "core.attributesFile", str(external_attributes))

    env = os.environ.copy()
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env["GIT_ATTR_NOSYSTEM"] = "1"
    env["GIT_NO_REPLACE_OBJECTS"] = "1"
    canonical = subprocess.run(
        [
            "git",
            "-c",
            "tar.umask=002",
            "-c",
            f"core.attributesFile={os.devnull}",
            "archive",
            "--format=tar",
            commit,
        ],
        cwd=repo,
        env=env,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    ).stdout

    assert _archive_sha256(repo, commit) == hashlib.sha256(canonical).hexdigest()
    assert _archive_sha256(repo, commit) != hashlib.sha256(
        _run(repo, "git", "archive", "--format=tar", commit)
    ).hexdigest()


def test_posix_recovery_downloads_before_execution_and_surfaces_tls_failure(
    tmp_path, monkeypatch
):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_curl = fake_bin / "curl"
    fake_curl.write_text(
        """#!/bin/sh
args="$*"
output=""
while [ "$#" -gt 0 ]; do
    if [ "$1" = "-o" ]; then
        shift
        output="$1"
    fi
    shift
done
case "$args" in
    *releases/latest/download/install.sh*) exit 22 ;;
esac
if [ "${RECOVERY_CURL_FAIL_ALL:-}" = "1" ]; then
    exit 35
fi
printf '%s\n' '#!/bin/sh' 'printf recovered > "$RECOVERY_PROBE"' > "$output"
""",
        encoding="utf-8",
    )
    fake_curl.chmod(0o755)
    probe = tmp_path / "probe"
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
    env["RECOVERY_PROBE"] = str(probe)

    recovered = subprocess.run(
        POSIX_INSTALL_COMMAND,
        shell=True,
        env=env,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert recovered.returncode == 0
    assert probe.read_text(encoding="utf-8") == "recovered"

    probe.unlink()
    env["RECOVERY_CURL_FAIL_ALL"] = "1"
    failed = subprocess.run(
        POSIX_INSTALL_COMMAND,
        shell=True,
        env=env,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert failed.returncode != 0
    assert not probe.exists()
