from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
import runpy
from unittest.mock import patch

import pytest

import wavebench
from wavebench.cli import main


def test_installed_version_matches_distribution():
    assert wavebench.__version__ == version("wavebench")


def test_runtime_version_ignores_ancestor_project(tmp_path):
    package = tmp_path / "lib" / "wavebench"
    package.mkdir(parents=True)
    source = package / "__init__.py"
    source.write_text(Path(wavebench.__file__).read_text(encoding="utf-8"), encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text('[project]\nversion = "99.0"\n')
    with patch("importlib.metadata.version", return_value="0.8.28"):
        assert runpy.run_path(str(source))["__version__"] == "0.8.28"


def test_missing_metadata_reports_unknown():
    with patch("importlib.metadata.version", side_effect=PackageNotFoundError):
        assert runpy.run_path(wavebench.__file__)["__version__"] == "0+unknown"


def test_cli_version_without_configuration(capsys):
    with pytest.raises(SystemExit) as result:
        main(["--version"])
    assert result.value.code == 0
    assert capsys.readouterr().out == f"wavebench {version('wavebench')}\n"
