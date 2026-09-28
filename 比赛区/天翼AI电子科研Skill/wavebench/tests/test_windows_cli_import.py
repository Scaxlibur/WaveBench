import importlib


def test_cli_imports_without_posix_fcntl() -> None:
    module = importlib.import_module("wavebench.cli")

    assert callable(module.main)
