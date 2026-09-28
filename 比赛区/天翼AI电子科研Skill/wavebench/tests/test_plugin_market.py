import json

import pytest

from wavebench.errors import ConfigError
from wavebench.plugins.market import load_market_index


def write_index(path, plugins):
    path.write_text(json.dumps({"schema_version": 1, "plugins": plugins}), encoding="utf-8")


def make_market_plugin(plugin_id="example-scope"):
    return {
        "plugin_id": plugin_id,
        "driver_id": "example.scope",
        "name": "Example Scope",
        "package": "wavebench-example-scope",
        "version": "0.1.0",
        "kind": "scope",
        "summary": "Example scope metadata.",
        "homepage": "https://example.invalid/wavebench-example-scope",
        "capabilities": ["scope.idn", "scope.capture_waveform"],
        "tags": ["example", "scope"],
    }


def test_load_market_index_searches_fields(tmp_path):
    index_path = tmp_path / "market.json"
    write_index(index_path, [make_market_plugin()])

    market = load_market_index(index_path)

    assert market.source == index_path
    assert [plugin.plugin_id for plugin in market.search("capture")] == ["example-scope"]
    assert [plugin.plugin_id for plugin in market.search("missing")] == []


def test_load_market_index_get_returns_entry(tmp_path):
    index_path = tmp_path / "market.json"
    write_index(index_path, [make_market_plugin()])

    plugin = load_market_index(index_path).get("example-scope")

    assert plugin.driver_id == "example.scope"
    assert plugin.capabilities == ("scope.idn", "scope.capture_waveform")
    assert plugin.tags == ("example", "scope")


def test_load_market_index_rejects_duplicate_plugin_ids(tmp_path):
    index_path = tmp_path / "market.json"
    write_index(index_path, [make_market_plugin(), make_market_plugin()])

    with pytest.raises(ConfigError, match="duplicate market plugin_id"):
        load_market_index(index_path)


def test_load_market_index_rejects_missing_required_field(tmp_path):
    index_path = tmp_path / "market.json"
    plugin = make_market_plugin()
    del plugin["driver_id"]
    write_index(index_path, [plugin])

    with pytest.raises(ConfigError, match="field 'driver_id' must be a non-empty string"):
        load_market_index(index_path)


def test_load_market_index_rejects_unknown_plugin_id(tmp_path):
    index_path = tmp_path / "market.json"
    write_index(index_path, [make_market_plugin()])

    with pytest.raises(ConfigError, match="unknown market plugin_id"):
        load_market_index(index_path).get("missing")
