import json
from unittest.mock import patch

import numpy as np
import pytest

from wavebench.cli import main
from wavebench.errors import ConfigError
from wavebench.services.analysis_service import check_analysis, run_analysis


@pytest.fixture
def analysis_input(tmp_path):
    capture = tmp_path / "capture"
    capture.mkdir()
    np.save(capture / "ch1.npy", np.column_stack((np.arange(128.) / 128, np.ones(128))))
    (capture / "metadata.json").write_text(json.dumps({
        "waveform": {"summary": {"channel": 1}}, "files": {"npy": "ch1.npy"}
    }))
    recipe = tmp_path / "recipe.toml"
    recipe.write_text('''schema = "wavebench.analysis_recipe.v1"
operations = [
  {op="measure", metrics=["voltage_mean_v"]},
  {op="export", name="waveform", formats=["npy", "csv"]},
]
[expect]
voltage_mean_v = {min=0.9, max=1.1}
''')
    return capture, recipe


def test_offline_execution_without_config_and_source_unchanged(tmp_path, analysis_input):
    capture, recipe = analysis_input
    original = {path.name: path.read_bytes() for path in capture.iterdir()}
    output = tmp_path / "analysis"
    with patch("wavebench.cli.load_config", side_effect=AssertionError("hardware config")):
        assert main(["analysis", "check", "--capture", str(capture), "--channel", "1",
                     "--recipe", str(recipe)]) == 0
        assert main(["analysis", "run", "--capture", str(capture), "--channel", "1",
                     "--recipe", str(recipe), "--output", str(output)]) == 0
    result = json.loads((output / "analysis.json").read_text())
    assert result["artifact"]["metrics"] == {"voltage_mean_v": 1.0}
    assert result["source"]["status"] is None
    assert result["source"]["npy"] == "ch1.npy"
    manifest = json.loads((output / "manifest.json").read_text())
    assert manifest["schema"] == "wavebench.offline_pipeline.v1"
    assert manifest["exports"][0]["path"] == "exports/waveform.npy"
    assert {p.name: p.read_bytes() for p in capture.iterdir()} == original
    assert not (output / "run.json").exists()
    assert run_analysis(capture, 1, recipe, tmp_path / "again") == result


def test_multichannel_selection(tmp_path, analysis_input):
    capture, recipe = analysis_input
    np.save(capture / "ch2.npy", np.column_stack((np.arange(128.), np.ones(128) * 2)))
    (capture / "metadata.json").write_text(json.dumps({
        "channels": {"1": {}, "2": {}},
        "files": {"1": {"npy": "ch1.npy"}, "2": {"npy": "ch2.npy"}},
    }))
    result = run_analysis(capture, 2, recipe, tmp_path / "analysis")
    assert result["artifact"]["metrics"]["voltage_mean_v"] == 2
    assert result["status"] == "failed"
    assert result["artifact"]["analysis_pipeline"]["status"] == "ok"


@pytest.mark.parametrize("fault", ["channel", "missing_npy", "escape", "symlink", "bad_array"])
def test_source_failure_is_recorded(tmp_path, analysis_input, fault):
    capture, recipe = analysis_input
    channel = 2 if fault == "channel" else 1
    if fault == "missing_npy":
        (capture / "ch1.npy").unlink()
    elif fault == "escape":
        (capture / "metadata.json").write_text(json.dumps({
            "waveform": {"summary": {"channel": 1}}, "files": {"npy": "../outside.npy"}
        }))
    elif fault == "symlink":
        outside = tmp_path / "outside.npy"
        (capture / "ch1.npy").rename(outside)
        (capture / "ch1.npy").symlink_to(outside)
    elif fault == "bad_array":
        np.save(capture / "ch1.npy", np.ones(10))
    result = run_analysis(capture, channel, recipe, tmp_path / "analysis")
    assert result["status"] == "failed"
    assert result["artifact"]["analysis_pipeline"]["failed_stage"] == "source"


def test_output_boundaries_and_strict_recipe(tmp_path, analysis_input):
    capture, recipe = analysis_input
    for output in (capture, capture / "derived", tmp_path):
        with pytest.raises(ConfigError):
            run_analysis(capture, 1, recipe, output)
    run = tmp_path / "run"
    run.mkdir()
    (run / "run.json").write_text("{}")
    with pytest.raises(ConfigError, match="existing run"):
        run_analysis(capture, 1, recipe, run / "derived")
    recipe.write_text(recipe.read_text().replace("operations =", "unknown = 1\noperations ="))
    with pytest.raises(ConfigError, match="unknown"):
        check_analysis(capture, 1, recipe)
