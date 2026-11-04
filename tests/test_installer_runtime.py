import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from jobagent.infra import installer_runtime


pytestmark = pytest.mark.skipif(os.name == 'nt', reason='POSIX installer runtime')


def _probe(environment):
    return subprocess.run(
        [str(environment / 'bin/python'), '-I', '-c', 'import sys; print(sys.prefix)'],
        text=True, capture_output=True, timeout=30,
    )


def test_install_survives_removal_of_temporary_python_entry(tmp_path):
    entry_dir = tmp_path / 'temporary-python'
    entry_dir.mkdir()
    entry = entry_dir / 'python3'
    entry.symlink_to(Path(sys._base_executable).resolve())
    install_dir = tmp_path / 'installation with spaces'
    install_dir.mkdir()

    result = subprocess.run(
        [str(entry), str(Path(installer_runtime.__file__)),
         '--install-dir', str(install_dir)],
        capture_output=True, text=True, timeout=90,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['created'] is True
    shutil.rmtree(entry_dir)

    probe = _probe(install_dir / '.venv')
    assert probe.returncode == 0, probe.stderr
    assert Path(probe.stdout.strip()) == install_dir / '.venv'
    assert str(entry_dir) not in (install_dir / '.venv/pyvenv.cfg').read_text()


def test_healthy_environment_is_preserved_on_repeat_install(tmp_path):
    installer_runtime.prepare_runtime(tmp_path)
    config = tmp_path / '.venv/pyvenv.cfg'
    before = config.read_bytes()
    marker = tmp_path / '.venv/keep-package-data'
    marker.write_bytes(b'existing environment')

    result = installer_runtime.prepare_runtime(tmp_path)

    assert result == {'ok': True, 'created': False, 'repaired': False}
    assert config.read_bytes() == before
    assert marker.read_bytes() == b'existing environment'
    assert not tmp_path.with_name(tmp_path.name + '.runtime-backups').exists()


def test_broken_environment_is_repaired_without_touching_user_files(tmp_path):
    installation = tmp_path / 'install'
    installation.mkdir()
    installer_runtime.prepare_runtime(installation)
    environment = installation / '.venv'
    interpreter = environment / 'bin/python'
    interpreter.unlink()
    interpreter.symlink_to(tmp_path / 'removed-python')
    original_config = (environment / 'pyvenv.cfg').read_bytes()
    state = tmp_path / '.jobagent/state'
    state.mkdir(parents=True)
    preserved = {
        state / 'profile.json': b'{"fixture": "profile"}',
        state / 'current_round.json': b'{"fixture": "round"}',
        state / 'audit_log.json': b'[{"fixture": "sent"}]',
        state.parent / 'credentials': b'synthetic-key',
        installation / 'unrelated-file': b'keep',
    }
    for path, contents in preserved.items():
        path.write_bytes(contents)

    result = installer_runtime.prepare_runtime(installation)

    assert result['repaired'] is True
    assert _probe(environment).returncode == 0
    backup = Path(result['previous_environment'])
    assert (backup / 'pyvenv.cfg').read_bytes() == original_config
    assert os.readlink(backup / 'bin/python') == str(tmp_path / 'removed-python')
    assert all(path.read_bytes() == value for path, value in preserved.items())


def test_existing_temporary_dependency_is_repaired_before_it_disappears(tmp_path):
    entry_dir = tmp_path / 'temporary-python'
    entry_dir.mkdir()
    entry = entry_dir / 'python3'
    entry.symlink_to(Path(sys._base_executable).resolve())
    install_dir = tmp_path / 'install'
    install_dir.mkdir()
    subprocess.run([str(entry), '-m', 'venv', str(install_dir / '.venv')],
                   check=True, capture_output=True, timeout=90)
    assert str(entry_dir) in (install_dir / '.venv/pyvenv.cfg').read_text()

    result = installer_runtime.prepare_runtime(install_dir)
    shutil.rmtree(entry_dir)

    assert result['repaired'] is True
    assert _probe(install_dir / '.venv').returncode == 0


def test_failed_repair_restores_previous_environment(tmp_path, monkeypatch):
    environment = tmp_path / '.venv'
    environment.mkdir()
    original = environment / 'old-environment'
    original.write_bytes(b'preserve even an unusable environment')

    def fail_creation(base, destination):
        destination.mkdir()
        (destination / 'partial').write_text('incomplete')
        raise RuntimeError('synthetic creation failure')

    monkeypatch.setattr(installer_runtime, '_create_environment', fail_creation)
    with pytest.raises(RuntimeError, match='synthetic creation failure'):
        installer_runtime.prepare_runtime(tmp_path)

    assert original.read_bytes() == b'preserve even an unusable environment'
    assert not (environment / 'partial').exists()


def test_temporary_base_interpreter_is_refused_before_environment_changes(tmp_path, monkeypatch):
    executable = tmp_path / 'python'
    shutil.copy2(sys._base_executable, executable)
    monkeypatch.setattr(sys, '_base_executable', str(executable))

    with pytest.raises(RuntimeError, match='temporary directory'):
        installer_runtime.prepare_runtime(tmp_path / 'install')

    assert not (tmp_path / 'install/.venv').exists()


def test_symlinked_environment_is_not_replaced(tmp_path):
    external = tmp_path / 'external'
    external.mkdir()
    marker = external / 'keep'
    marker.write_text('external environment')
    install_dir = tmp_path / 'install'
    install_dir.mkdir()
    (install_dir / '.venv').symlink_to(external, target_is_directory=True)

    with pytest.raises(RuntimeError, match='symbolic link'):
        installer_runtime.prepare_runtime(install_dir)

    assert marker.read_text() == 'external environment'
