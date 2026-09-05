"""Every test runs with UNMScope's user directory redirected to a temporary
folder (UNMSCOPE_HOME), so nothing under the real ~/.unmscope is read or
written by the suite."""
import pytest


@pytest.fixture(autouse=True)
def _unmscope_home(tmp_path, monkeypatch):
    home = tmp_path / "unmscope_home"
    home.mkdir()
    monkeypatch.setenv("UNMSCOPE_HOME", str(home))
    yield home
