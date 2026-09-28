from pathlib import Path
import csv
import json
import unittest
from tempfile import TemporaryDirectory

from wavebench.report.index import build_manifest_entry, write_report_index


class ReportIndexTests(unittest.TestCase):
    def _make_run(
        self,
        root: Path,
        name: str,
        *,
        status: str = 'ok',
        with_summary: bool = True,
        with_report: bool = True,
        with_scope: bool = True,
        with_dmm: bool = True,
        expect_status: str = 'ok',
        expect_fft_status: str = 'ok',
    ) -> Path:
        run_dir = root / name
        run_dir.mkdir(parents=True)
        if with_summary:
            (run_dir / 'summary.csv').write_text('index,kind,status\n0,scope.capture,ok\n', encoding='utf-8')
        if with_report:
            (run_dir / 'report.html').write_text('<html></html>', encoding='utf-8')

        steps = []
        if with_scope:
            steps.append(
                {
                    'index': len(steps),
                    'kind': 'scope.capture',
                    'status': 'ok',
                    'artifact': {
                        'quality': {
                            'summary': {
                                'frequency_estimate_hz': 1000.5,
                                'voltage_vpp_v': 1.0,
                            }
                        },
                        'expect': {'status': expect_status},
                        'expect_fft': {'status': expect_fft_status},
                    },
                }
            )
        if with_dmm:
            steps.append(
                {
                    'index': len(steps),
                    'kind': 'dmm.read',
                    'status': 'ok',
                    'artifact': {
                        'dmm_reading': {'function': 'acv', 'value': 0.353, 'unit': 'V'},
                        'expect': {'status': expect_status},
                    },
                }
            )
        run = {
            'status': status,
            'experiment': {'name': name, 'label': name},
            'plan': f'plans/{name}.toml',
            'steps': steps,
            'restore': {'status': 'ok', 'source_channels': [1, 2]},
        }
        (run_dir / 'run.json').write_text(json.dumps(run, indent=2), encoding='utf-8')
        return run_dir

    def test_build_manifest_entry_extracts_primary_metrics(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = self._make_run(root, 'demo_run')
            entry = build_manifest_entry(run_dir)
            self.assertEqual(entry['status'], 'ok')
            self.assertEqual(entry['experiment_label'], 'demo_run')
            self.assertEqual(entry['restore_source_channels'], [1, 2])
            self.assertEqual(entry['primary_scope_frequency_hz'], 1000.5)
            self.assertEqual(entry['primary_scope_vpp_v'], 1.0)
            self.assertEqual(entry['primary_dmm_value'], 0.353)
            self.assertEqual(entry['primary_dmm_function'], 'acv')
            self.assertEqual(entry['expect_failures'], 0)
            self.assertEqual(entry['expect_fft_failures'], 0)

    def test_build_manifest_entry_falls_back_to_legacy_single_restore_channel(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = self._make_run(root, 'legacy_restore')
            run = json.loads((run_dir / 'run.json').read_text(encoding='utf-8'))
            run['restore'] = {'status': 'ok', 'source_channel': 2}
            (run_dir / 'run.json').write_text(json.dumps(run, indent=2), encoding='utf-8')
            entry = build_manifest_entry(run_dir)
            self.assertEqual(entry['restore_source_channels'], [2])

    def test_build_manifest_entry_handles_missing_report_and_summary(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = self._make_run(root, 'missing_artifacts', with_report=False, with_summary=False)
            entry = build_manifest_entry(run_dir)
            self.assertIsNone(entry['report_path'])
            self.assertIsNone(entry['artifact_paths']['summary_csv'])
            self.assertIsNone(entry['artifact_paths']['report_html'])

    def test_build_manifest_entry_handles_scope_only_run(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = self._make_run(root, 'scope_only', with_dmm=False)
            entry = build_manifest_entry(run_dir)
            self.assertTrue(entry['has_scope_capture'])
            self.assertFalse(entry['has_dmm'])
            self.assertIsNone(entry['primary_dmm_value'])
            self.assertEqual(entry['primary_scope_frequency_hz'], 1000.5)

    def test_build_manifest_entry_handles_dmm_only_run(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = self._make_run(root, 'dmm_only', with_scope=False)
            entry = build_manifest_entry(run_dir)
            self.assertFalse(entry['has_scope_capture'])
            self.assertTrue(entry['has_dmm'])
            self.assertIsNone(entry['primary_scope_frequency_hz'])
            self.assertEqual(entry['primary_dmm_value'], 0.353)

    def test_build_manifest_entry_counts_expect_failures(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = self._make_run(root, 'failed_expect', expect_status='failed', expect_fft_status='failed')
            entry = build_manifest_entry(run_dir)
            self.assertEqual(entry['expect_failures'], 2)
            self.assertEqual(entry['expect_fft_failures'], 1)

    def test_write_report_index_writes_json_csv_and_html(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_a = self._make_run(root, 'run_a')
            run_b = self._make_run(root, 'run_b', status='failed')
            out = root / 'out'
            result = write_report_index([run_a, run_b], out)
            self.assertTrue(result.manifest_json_path.exists())
            self.assertTrue(result.manifest_csv_path.exists())
            self.assertTrue(result.index_html_path.exists())
            html = result.index_html_path.read_text(encoding='utf-8')
            self.assertIn('WaveBench report index', html)
            self.assertIn('run_a', html)
            manifest = json.loads(result.manifest_json_path.read_text(encoding='utf-8'))
            self.assertEqual(manifest['count'], 2)
            self.assertEqual(len(manifest['runs']), 2)
            with result.manifest_csv_path.open(newline='', encoding='utf-8') as file:
                rows = list(csv.DictReader(file))
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]['restore_source_channels'], '1|2')

    def test_html_marks_missing_links(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = self._make_run(root, 'missing_links', with_report=False, with_summary=False)
            out = root / 'out'
            result = write_report_index([run_dir], out)
            html = result.index_html_path.read_text(encoding='utf-8')
            self.assertIn('report.html: missing', html)
            self.assertIn('summary.csv: missing', html)


if __name__ == '__main__':
    unittest.main()
