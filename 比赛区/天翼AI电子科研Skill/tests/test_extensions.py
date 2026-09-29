import json
import struct

from tianyi_electronics_skill.artifacts import write_single_frame
from tianyi_electronics_skill.protocols import ProtocolRegistry


def test_json_protocol_and_artifacts(tmp_path):
    registry = ProtocolRegistry()
    payload = {"samples": [-1, 0, 1, 0, -1, 0, 1, 0], "sample_rate_hz": 4}
    assert registry.identify(payload)["selected"]["protocol"] == "json-waveform"
    frame = registry.decode(payload, "test")
    result = write_single_frame(frame.samples, frame.sample_rate_hz, tmp_path, "test")
    assert all(path.endswith(suffix) for path, suffix in zip(result.values(), (".json", ".csv", ".svg")))
    assert "频率" in open(result["svg"], encoding="utf-8").read()


def test_declarative_binary_manifest(tmp_path):
    manifest = {
        "kind": "binary-waveform", "name": "fixture", "match": {"magic_hex": "57415645"},
        "payload": {"header_bytes": 4, "sample_type": "f32", "endian": "little", "sample_rate_hz": 10}
    }
    manifest_dir = tmp_path / "protocols"
    manifest_dir.mkdir()
    (manifest_dir / "fixture.json").write_text(json.dumps(manifest), encoding="utf-8")
    registry = ProtocolRegistry()
    assert registry.load_manifests(manifest_dir) == 1
    payload = b"WAVE" + struct.pack("<4f", 1.0, -2.0, 3.0, -1.0)
    frame = registry.decode(payload, "binary")
    assert frame.protocol == "fixture"
    assert frame.samples == [1.0, -2.0, 3.0, -1.0]
