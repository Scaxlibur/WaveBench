import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np

from wavebench.arbitrary import build_dg4000_dac14_binary_block, load_arbitrary_waveform, normalize_peak, normalized_to_dac14, validate_waveform_name, write_arbitrary_payload_json, write_dg4000_dac14_binary_block
from wavebench.errors import DataError
from wavebench.instruments import DG4000ByteOrder, DG4000DacBlock


class ArbitraryWaveformTests(unittest.TestCase):
    def test_loads_npy_vector_and_builds_normalized_payload(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "waveform.npy"
            np.save(path, np.array([-2.0, 0.0, 2.0]))

            waveform = load_arbitrary_waveform(path)

            self.assertEqual(waveform.points, 3)
            np.testing.assert_allclose(waveform.normalized, [-1.0, 0.0, 1.0])
            self.assertEqual(waveform.dac14.tolist(), [0, 8192, 16383])
            self.assertIsNone(waveform.sample_rate_hz)

    def test_loads_csv_time_value_and_infers_sample_rate(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "waveform.csv"
            path.write_text("0,0\n0.001,1\n0.002,0\n", encoding="utf-8")

            waveform = load_arbitrary_waveform(path)

            self.assertEqual(waveform.points, 3)
            self.assertAlmostEqual(waveform.sample_rate_hz, 1000.0)
            self.assertTrue(waveform.summary()["has_time_axis"])


    def test_payload_dict_contains_target_and_dac_values(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "waveform.npy"
            np.save(path, np.array([-1.0, 0.0, 1.0]))
            waveform = load_arbitrary_waveform(path, sample_rate_hz=1000.0)

            payload = waveform.payload_dict(name="EXAMPLE_ARB", channel=2, amplitude_vpp=1.0, offset_v=0.0)

            self.assertEqual(payload["format"], "wavebench.arbitrary.v1")
            self.assertEqual(payload["target"]["name"], "EXAMPLE_ARB")
            self.assertEqual(payload["target"]["channel"], 2)
            self.assertEqual(payload["target"]["sample_rate_hz"], 1000.0)
            self.assertEqual(payload["payload"]["encoding"], "dac14_unsigned_integer")
            self.assertEqual(payload["payload"]["values"], [0, 8192, 16383])

    def test_write_payload_json_creates_artifact(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "waveform.npy"
            output = root / "payload" / "rei.json"
            np.save(source, np.array([-1.0, 1.0]))
            waveform = load_arbitrary_waveform(source)

            written = write_arbitrary_payload_json(
                waveform, output, name="EXAMPLE_ARB", channel=2, amplitude_vpp=1.0, offset_v=0.0
            )

            self.assertEqual(written, output)
            text = output.read_text(encoding="utf-8")
            self.assertIn('"format": "wavebench.arbitrary.v1"', text)
            self.assertIn('"values": [', text)


    def test_builds_dg4000_dac_binary_block_little_endian_by_default(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "waveform.npy"
            np.save(path, np.array([-1.0, 0.0, 1.0]))
            waveform = load_arbitrary_waveform(path)

            block = build_dg4000_dac14_binary_block(waveform)

            self.assertIsInstance(block, DG4000DacBlock)
            self.assertIs(block.byte_order, DG4000ByteOrder.LITTLE)
            self.assertEqual(block.points, 3)
            self.assertEqual(block.data_bytes, 6)
            self.assertTrue(block.command.startswith(b":DATA:DAC VOLATILE,#16"))
            self.assertEqual(block.command[-6:], bytes([0x00, 0x00, 0x00, 0x20, 0xFF, 0x3F]))

    def test_writes_dg4000_dac_binary_block_little_endian_when_requested(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "waveform.npy"
            output = root / "arb.scpi"
            np.save(path, np.array([-1.0, 0.0, 1.0]))
            waveform = load_arbitrary_waveform(path)

            written = write_dg4000_dac14_binary_block(waveform, output, byte_order="little")

            self.assertEqual(written, output)
            self.assertEqual(output.read_bytes()[-6:], bytes([0x00, 0x00, 0x00, 0x20, 0xFF, 0x3F]))

    def test_validate_waveform_name_rejects_spaces(self):
        with self.assertRaises(DataError):
            validate_waveform_name("bad name")

    def test_rejects_nan_samples(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "waveform.npy"
            np.save(path, np.array([0.0, np.nan, 1.0]))

            with self.assertRaises(DataError):
                load_arbitrary_waveform(path)

    def test_rejects_too_many_points_when_limit_is_set(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "waveform.npy"
            np.save(path, np.arange(5, dtype=float))

            with self.assertRaises(DataError):
                load_arbitrary_waveform(path, max_points=4)

    def test_rejects_non_uniform_time_axis(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "waveform.csv"
            path.write_text("0,0\n0.001,1\n0.003,0\n", encoding="utf-8")

            with self.assertRaises(DataError):
                load_arbitrary_waveform(path)

    def test_normalize_peak_leaves_unit_range_unchanged(self):
        np.testing.assert_allclose(normalize_peak(np.array([-0.5, 0.25])), [-0.5, 0.25])

    def test_normalize_peak_leaves_all_zero_input_unchanged(self):
        np.testing.assert_allclose(normalize_peak(np.zeros(4)), np.zeros(4))

    def test_dac14_rejects_out_of_range_normalized_values(self):
        with self.assertRaises(DataError):
            normalized_to_dac14(np.array([-1.1, 0.0, 1.0]))


if __name__ == "__main__":
    unittest.main()
