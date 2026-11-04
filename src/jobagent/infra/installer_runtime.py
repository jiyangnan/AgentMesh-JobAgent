"""Prepare the POSIX installer's Python environment without touching user state.

This file also runs directly before third-party dependencies are installed.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


def _temporary_path(path: Path) -> bool:
    roots = {Path(tempfile.gettempdir()), Path('/tmp'), Path('/var/tmp')}
    candidates = {path.absolute(), path.resolve()}
    return any(
        candidate == root or root in candidate.parents
        for candidate in candidates
        for entry in roots
        for root in (entry.absolute(), entry.resolve())
    )


def _base_python() -> Path:
    executable = Path(getattr(sys, '_base_executable', sys.executable)).resolve()
    if _temporary_path(executable):
        raise RuntimeError(
            'Python resolves to a temporary directory. Install Python 3.11+ '
            'in a persistent location before running the installer again.'
        )
    if not executable.is_file() or not os.access(executable, os.X_OK):
        raise RuntimeError('The persistent Python interpreter is not executable.')
    return executable


def _healthy_environment(environment: Path) -> bool:
    try:
        result = subprocess.run(
            [str(environment / 'bin/python'), '-I', '-c',
             'import importlib.util,json,sys; '
             'print(json.dumps({"prefix":sys.prefix,'
             '"base":sys._base_executable,"version":list(sys.version_info[:2]),'
             '"pip":importlib.util.find_spec("pip") is not None}))'],
            check=True, capture_output=True, text=True, timeout=30,
        )
        info = json.loads(result.stdout)
        config = dict(
            line.split(' = ', 1)
            for line in (environment / 'pyvenv.cfg').read_text().splitlines()
            if ' = ' in line
        )
        base = Path(info['base'])
        home = Path(config['home'])
        return (
            Path(info['prefix']).resolve() == environment.resolve()
            and tuple(info['version']) >= (3, 11)
            and info['pip'] is True
            and base.is_file()
            and os.access(base, os.X_OK)
            and not _temporary_path(base)
            and home.is_dir()
            and not _temporary_path(home)
        )
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        return False


def _create_environment(base: Path, environment: Path) -> None:
    subprocess.run([str(base), '-I', '-m', 'venv', str(environment)], check=True)


def prepare_runtime(install_dir: Path) -> dict:
    install_dir = install_dir.resolve()
    base = _base_python()
    environment = install_dir / '.venv'
    if environment.is_symlink():
        raise RuntimeError('The .venv directory is a symbolic link; automatic repair refused.')
    if _healthy_environment(environment):
        return {'ok': True, 'created': False, 'repaired': False}

    backup = None
    if environment.exists():
        if not environment.is_dir():
            raise RuntimeError('The .venv path is not a directory; automatic repair refused.')
        backup_root = install_dir.with_name(install_dir.name + '.runtime-backups')
        backup_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        backup = Path(tempfile.mkdtemp(prefix='venv-', dir=backup_root)) / '.venv'
        environment.rename(backup)

    try:
        _create_environment(base, environment)
        if not _healthy_environment(environment):
            raise RuntimeError('The new Python environment failed its startup check.')
    except BaseException:
        if environment.is_dir() and not environment.is_symlink():
            shutil.rmtree(environment)
        if backup is not None:
            backup.rename(environment)
        raise

    return {
        'ok': True, 'created': True, 'repaired': backup is not None,
        'base_python': str(base),
        'previous_environment': str(backup) if backup is not None else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--install-dir', type=Path, required=True)
    arguments = parser.parse_args()
    try:
        result = prepare_runtime(arguments.install_dir)
    except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
        print(f'Python environment setup failed: {exc}', file=sys.stderr)
        return 1
    print(json.dumps(result))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
