from dataclasses import FrozenInstanceError
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

import pytest

from wavebench.errors import error_envelope
from wavebench.services import advisor_run_binding as binding
from wavebench.services.advisor_run_binding import (
    BindingSource, RunBindingError, capture_run_binding, verify_run_binding,
)


@pytest.fixture
def run_dir(tmp_path):
    root = tmp_path / "run"
    root.mkdir()
    (root / "run.json").write_text(
        '{"status": "completed", "steps": [], "experiment": {"name": "示例"}}',
        encoding="utf-8",
    )
    return root


def capture(root, sources=(), **kwargs):
    return capture_run_binding(
        root, sources=sources, task_id="capture-triage", task_version="1", **kwargs,
    )


def symlink(link, target, *, directory=False):
    try:
        link.symlink_to(target, target_is_directory=directory)
    except OSError:
        pytest.skip("symlink creation unavailable on this host")


def test_digest_covers_directory_task_and_exact_bytes(run_dir):
    raw = (run_dir / "run.json").read_bytes()
    (run_dir / "quality.json").write_text('{"warnings": ["low_cycle_count"]}')
    source = BindingSource("quality.json")
    snapshot = capture(run_dir, [source])
    expected = {
        "schema": "wavebench.advisor.run_binding.v1",
        "run_root": str(run_dir.resolve()), "task_id": "capture-triage", "task_version": "1",
        "sources": [
            {"path": "quality.json", "state": "present",
             "sha256": sha256((run_dir / "quality.json").read_bytes()).hexdigest()},
            {"path": "run.json", "state": "present", "sha256": sha256(raw).hexdigest()},
        ],
    }
    assert snapshot.as_dict() == expected
    encoded = json.dumps(expected, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    assert snapshot.binding_sha256 == sha256(encoded.encode()).hexdigest()
    assert snapshot.bytes_read == len(raw) + len((run_dir / "quality.json").read_bytes())
    assert snapshot.elapsed_s > 0
    assert (run_dir / "run.json").read_bytes() == raw
    for kwargs in ({"task_id": "other", "task_version": "1"},
                   {"task_id": "capture-triage", "task_version": "2"}):
        assert capture_run_binding(run_dir, sources=[source], **kwargs).binding_sha256 != snapshot.binding_sha256


def test_snapshot_parsing_never_reopens_or_mutates_sources(run_dir):
    snapshot = capture(run_dir)
    original = snapshot.read_bytes("run.json")
    snapshot.read_json("run.json")["steps"].append({"untrusted": True})
    snapshot.as_dict()["sources"].clear()
    (run_dir / "run.json").write_text('{"steps": ["changed"]}')
    assert snapshot.read_bytes("run.json") == original
    assert snapshot.read_json("run.json")["steps"] == []
    assert len(snapshot.as_dict()["sources"]) == 1
    with pytest.raises(FrozenInstanceError):
        snapshot.task_id = "changed"
    with pytest.raises(KeyError):
        snapshot.read_bytes("not-consumed.json")


def test_changed_input_retains_both_digests_and_original_snapshot(run_dir):
    snapshot = capture(run_dir)
    old = snapshot.binding_sha256
    (run_dir / "run.json").write_text('{"steps": [], "status": "failed"}')
    with pytest.raises(RunBindingError) as caught:
        verify_run_binding(snapshot)
    payload = error_envelope(caught.value)
    assert payload["code"] == "run_binding_changed"
    assert payload["details"]["paths"] == ["run.json"]
    assert payload["details"]["expected_digest"] == old
    assert payload["details"]["actual_digest"] != old
    assert snapshot.read_json("run.json")["status"] == "completed"


def test_equivalent_root_and_manifest_order_are_stable(run_dir, monkeypatch):
    monkeypatch.chdir(run_dir.parent)
    sources = [BindingSource("b.json", optional=True), BindingSource("a.json", optional=True)]
    absolute = capture(run_dir, sources)
    relative = capture(Path("run") / ".", reversed(sources))
    assert absolute.binding_sha256 == relative.binding_sha256
    assert absolute.read_bytes("a.json") is None
    assert absolute.read_json("a.json") is None


def test_same_name_in_another_directory_is_not_the_same_binding(run_dir, tmp_path):
    original = capture(run_dir)
    copy = tmp_path / "elsewhere" / "run"
    shutil.copytree(run_dir, copy)
    assert capture(copy).binding_sha256 != original.binding_sha256
    moved = tmp_path / "moved"
    copy.rename(moved)
    assert capture(moved).binding_sha256 != original.binding_sha256


def test_root_alias_retarget_is_detected_even_with_identical_content(run_dir, tmp_path):
    other = tmp_path / "other"
    shutil.copytree(run_dir, other)
    alias = tmp_path / "alias"
    symlink(alias, run_dir, directory=True)
    snapshot = capture(alias)
    assert snapshot.binding_sha256 == capture(run_dir).binding_sha256
    alias.unlink()
    symlink(alias, other, directory=True)
    with pytest.raises(RunBindingError) as caught:
        verify_run_binding(snapshot)
    assert caught.value.code == "run_binding_changed"


@pytest.mark.parametrize("initially_present", [False, True])
def test_optional_file_presence_is_evidence(run_dir, initially_present):
    path = run_dir / "optional.json"
    if initially_present:
        path.write_text("{}")
    snapshot = capture(run_dir, [BindingSource(path.name, optional=True)])
    if initially_present:
        path.unlink()
    else:
        path.write_text("{}")
    with pytest.raises(RunBindingError) as caught:
        verify_run_binding(snapshot)
    assert caught.value.code == "run_binding_changed"
    assert caught.value.paths == (path.name,)


def test_irrelevant_files_mtime_and_identical_replacement_do_not_invalidate(run_dir):
    snapshot = capture(run_dir)
    path = run_dir / "run.json"
    os.utime(path, (1, 1))
    (run_dir / "decisions").mkdir()
    (run_dir / "decisions" / "result.json").write_text("{}")
    (run_dir / "report.html").write_text("changed")
    with (run_dir / "large-waveform.npy").open("wb") as output:
        output.truncate(1024**3)
    replacement = run_dir / "replacement"
    replacement.write_bytes(snapshot.read_bytes("run.json"))
    os.replace(replacement, path)
    actual = verify_run_binding(snapshot)
    assert actual.binding_sha256 == snapshot.binding_sha256
    assert actual.bytes_read == snapshot.bytes_read


@pytest.mark.parametrize("raw", [b"[]", b"null", b'{"x":1,"x":2}',
                                 b'{"a":{"x":1,"x":2}}', b'{"x":NaN}',
                                 b'{"x":Infinity}', b'{"x":1e999}', b'\xff', b'{'])
def test_invalid_run_json_is_rejected(run_dir, raw):
    (run_dir / "run.json").write_bytes(raw)
    with pytest.raises(RunBindingError) as caught:
        capture(run_dir)
    assert caught.value.reason in {"invalid_json", "run_must_be_object"}
    assert caught.value.paths == ("run.json",)


@pytest.mark.parametrize("path", ["../outside.json", "/absolute.json", "a/../x.json", "./x",
                                  "a//b", "a\\b", "C:/x", "CON", "", "a\x00b",
                                  "decisions/request.json", "DECISIONS/request.json"])
def test_unsafe_paths_are_rejected_before_starting_worker(run_dir, path, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("invalid manifest started a worker")
    monkeypatch.setattr(binding.subprocess, "run", forbidden)
    with pytest.raises(RunBindingError):
        capture(run_dir, [BindingSource(path, optional=True)])


def test_symlinks_cannot_escape_or_include_audit_output(run_dir, tmp_path):
    outside = tmp_path / "outside.json"
    outside.write_text('{"private":"do-not-read"}')
    alias = run_dir / "alias.json"
    symlink(alias, outside)
    with pytest.raises(RunBindingError) as caught:
        capture(run_dir, [BindingSource(alias.name)])
    assert "do-not-read" not in str(error_envelope(caught.value))
    alias.unlink()
    (run_dir / "decisions").mkdir()
    target = run_dir / "decisions" / "request.json"
    target.write_text("{}")
    symlink(alias, target)
    with pytest.raises(RunBindingError, match="audit_source_forbidden"):
        capture(run_dir, [BindingSource(alias.name)])


@pytest.mark.parametrize("optional", [False, True])
def test_directory_is_not_a_missing_file(run_dir, optional):
    (run_dir / "subdir").mkdir()
    with pytest.raises(RunBindingError, match="source_not_regular"):
        capture(run_dir, [BindingSource("subdir", optional=optional)])


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="POSIX FIFO")
def test_fifo_is_rejected_without_blocking(run_dir):
    os.mkfifo(run_dir / "source.json")
    with pytest.raises(RunBindingError, match="source_not_regular"):
        capture(run_dir, [BindingSource("source.json")], timeout_s=2)


def test_unreadable_optional_source_is_not_absence(run_dir, monkeypatch):
    original = Path.stat
    def denied(path, *args, **kwargs):
        if path.name == "optional.json":
            raise PermissionError("private host path")
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "stat", denied)
    with pytest.raises(RunBindingError) as caught:
        binding._read_snapshot(str(run_dir), (BindingSource("optional.json", optional=True),))
    assert caught.value.reason == "source_unreadable"
    assert "private host path" not in str(error_envelope(caught.value))


def test_missing_required_source_cannot_revalidate(run_dir):
    snapshot = capture(run_dir)
    (run_dir / "run.json").unlink()
    with pytest.raises(RunBindingError) as caught:
        verify_run_binding(snapshot)
    assert caught.value.code == "run_binding_unverifiable"
    assert caught.value.expected_digest == snapshot.binding_sha256
    assert caught.value.actual_digest is None


def test_count_limit_includes_run_and_missing_sources(run_dir):
    sources = [BindingSource(f"missing/{i}.json", optional=True) for i in range(31)]
    assert len(capture(run_dir, sources).as_dict()["sources"]) == 32
    with pytest.raises(RunBindingError, match="source_count_limit"):
        capture(run_dir, sources + [BindingSource("extra.json", optional=True)])
    with pytest.raises(RunBindingError, match="duplicate_source"):
        capture(run_dir, [BindingSource("run.json")])


def test_cumulative_byte_limit_exact_boundary_and_one_byte_over(run_dir):
    (run_dir / "run.json").write_bytes(b"{}")
    source = run_dir / "notes.txt"
    source.write_bytes(b"x" * (binding.MAX_SOURCE_BYTES - 2))
    snapshot = capture(run_dir, [BindingSource("notes.txt")])
    assert snapshot.bytes_read == binding.MAX_SOURCE_BYTES
    with source.open("ab") as output:
        output.write(b"x")
    with pytest.raises(RunBindingError, match="source_bytes_limit"):
        verify_run_binding(snapshot)


@pytest.mark.parametrize("grow", [False, True])
def test_changes_during_read_and_growth_past_budget_are_rejected(run_dir, monkeypatch, grow):
    path = run_dir / "source.json"
    path.write_bytes(b"123")
    before = path.stat()
    fdopen = os.fdopen
    class ChangingReader:
        def __init__(self, handle):
            self.handle = handle
            self.changed = False
        def __enter__(self):
            return self
        def __exit__(self, *args):
            self.handle.close()
        def fileno(self):
            return self.handle.fileno()
        def read(self, n):
            if not self.changed:
                self.changed = True
                path.write_bytes(b"1234" if grow else b"456")
                # Equal-size writes may share a timestamp on Windows; make the
                # metadata change observable without relying on clock resolution.
                os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns + 2_000_000_000))
            return self.handle.read(n)
    monkeypatch.setattr(os, "fdopen", lambda fd, mode: ChangingReader(fdopen(fd, mode)))
    reason = "source_bytes_limit" if grow else "source_changed_during_read"
    with pytest.raises(RunBindingError, match=reason):
        binding._read_source(run_dir.resolve(), BindingSource(path.name), 3)


@pytest.mark.parametrize("timeout", [0, -1, True, float("nan"), float("inf"), "30"])
def test_invalid_timeout_rejected(run_dir, timeout):
    with pytest.raises(RunBindingError, match="invalid_timeout"):
        capture(run_dir, timeout_s=timeout)


def test_timeout_kills_and_reaps_a_stalled_worker(run_dir, monkeypatch):
    run = subprocess.run
    popen = subprocess.Popen
    children = []
    def tracked(*args, **kwargs):
        child = popen(*args, **kwargs)
        children.append(child)
        return child
    def stalled(command, **kwargs):
        return run([sys.executable, "-c", "import time; time.sleep(60)"], **kwargs)
    monkeypatch.setattr(subprocess, "Popen", tracked)
    monkeypatch.setattr(subprocess, "run", stalled)
    started = time.monotonic()
    with pytest.raises(RunBindingError) as caught:
        capture(run_dir, timeout_s=0.1)
    assert caught.value.code == "timeout"
    assert time.monotonic() - started < 5
    assert len(children) == 1 and children[0].poll() is not None


@pytest.mark.parametrize("returncode,stdout", [(1, b""), (0, b"not JSON"), (0, b"{}")])
def test_worker_failure_is_structured_without_stderr_leak(run_dir, monkeypatch, returncode, stdout):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(
        a[0], returncode, stdout, b"unrelated-private-data",
    ))
    with pytest.raises(RunBindingError) as caught:
        capture(run_dir)
    assert "unrelated-private-data" not in str(error_envelope(caught.value))
    assert caught.value.code == "run_binding_unverifiable"


def test_cancelled_worker_does_not_return_a_snapshot(run_dir, monkeypatch):
    def interrupted(*args, **kwargs):
        raise KeyboardInterrupt
    monkeypatch.setattr(subprocess, "run", interrupted)
    with pytest.raises(RunBindingError) as caught:
        capture(run_dir)
    assert caught.value.code == "cancelled"


@pytest.mark.parametrize("sources", [[], [BindingSource("new.json", optional=True)]])
def test_rebuilt_manifest_must_match_authorized_sources(run_dir, sources):
    snapshot = capture(run_dir, [BindingSource("old.json", optional=True)])
    with pytest.raises(RunBindingError) as caught:
        verify_run_binding(snapshot, sources=sources)
    assert caught.value.code == "run_binding_changed"
    assert "old.json" in caught.value.paths
    assert caught.value.expected_digest == snapshot.binding_sha256
