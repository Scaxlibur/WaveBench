from pathlib import Path

from wavebench.report.path_utils import artifact_url


def test_artifact_url_uses_relative_posix_url(tmp_path: Path) -> None:
    output = tmp_path / "report"
    asset = output / "nested" / "中文 文件.json"
    assert artifact_url(asset, output) == "nested/%E4%B8%AD%E6%96%87%20%E6%96%87%E4%BB%B6.json"


def test_artifact_url_never_emits_raw_cross_drive_windows_path() -> None:
    url = artifact_url(
        Path(r"D:\\captures\\wave.json"),
        Path(r"C:\\reports"),
    )
    assert url.startswith("file:///D:/")
    assert "\\" not in url
    assert "D:\\" not in url
