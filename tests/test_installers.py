from pathlib import Path
import tomllib

import pytest

from jobagent import __version__


ROOT = Path(__file__).resolve().parents[1]


def test_cli_version_matches_package_metadata():
    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert __version__ == metadata["project"]["version"]


@pytest.mark.parametrize("relative", ["scripts/install.sh", "scripts/install.ps1"])
def test_official_installer_targets_public_repo_and_current_credential_term(relative):
    text = (ROOT / relative).read_text(encoding="utf-8")

    assert "https://github.com/jiyangnan/AgentMesh-JobAgent.git" in text
    assert "raw.githubusercontent.com/jiyangnan/AgentMesh-JobAgent/main" in text
    assert "jiyangnan/job-agent" not in text
    assert "license key" not in text.lower()
    assert "jobagent boss discover" not in text
    assert text.index("jobagent round start") < text.index("jobagent boss login --check")
