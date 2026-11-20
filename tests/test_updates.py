import hashlib
import threading

import pytest

from ventude.core import net, updates


def release(tag="v1.0.7", **asset):
    data = {
        "tag_name": tag,
        "draft": False,
        "prerelease": False,
        "body": "- Novidade A\n- Novidade B",
        "html_url": "https://github.com/3Gb3/Ventude_Notch/releases/tag/" + tag,
        "assets": [
            {"name": "outro.zip", "browser_download_url": "https://github.com/x/outro.zip"},
            {
                "name": "Ventude-Setup.exe",
                "browser_download_url": "https://github.com/3Gb3/Ventude_Notch/releases/download/x/Ventude-Setup.exe",
                "size": 10,
                **asset,
            },
        ],
    }
    return data


def test_versions():
    assert updates.parse_version("v1.0.12") == (1, 0, 12)
    assert updates.parse_version("1.0.12") > updates.parse_version("1.0.9")
    assert updates.parse_version("") == (0,)


def test_parse_release_only_newer_and_with_installer():
    found = updates.parse_release(release(digest="sha256:ABC"), "1.0.5")
    assert found.version == "1.0.7" and found.sha256 == "abc" and "Novidade A" in found.notes
    assert updates.parse_release(release(), "1.0.7") is None
    assert updates.parse_release(release(), "1.0.9") is None
    assert updates.parse_release({**release(), "prerelease": True}, "1.0.0") is None
    assert updates.parse_release({**release(), "assets": []}, "1.0.0") is None
    assert updates.parse_release(None, "1.0.0") is None


def test_check_is_silent_when_offline(monkeypatch):
    def boom(*_a, **_k):
        raise net.NetError("sem rede")

    monkeypatch.setattr(net, "get_json", boom)
    assert updates.check("1.0.0") is None
    monkeypatch.setattr(net, "get_json", lambda *_a, **_k: release("v2.0.1"))
    assert updates.check("1.0.0").version == "2.0.1"


def test_enabled_only_when_packaged(monkeypatch):
    monkeypatch.delenv("VENTUDE_FORCE_UPDATE_CHECK", raising=False)
    monkeypatch.delenv("VENTUDE_NO_UPDATE", raising=False)
    assert updates.enabled() is False
    monkeypatch.setattr("sys.frozen", True, raising=False)
    assert updates.enabled() is True
    monkeypatch.setenv("VENTUDE_NO_UPDATE", "1")
    assert updates.enabled() is False


class FakeResponse:
    def __init__(self, data: bytes):
        self.data, self.pos = data, 0
        self.headers = {"Content-Length": str(len(data))}

    def read(self, size):
        chunk = self.data[self.pos : self.pos + size]
        self.pos += size
        return chunk

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_download_checks_hash_progress_and_cancel(monkeypatch, tmp_path):
    payload = b"x" * 600_000
    monkeypatch.setattr(updates.urllib.request, "urlopen", lambda *_a, **_k: FakeResponse(payload))
    good = updates.Update(
        "1.0.7",
        "",
        release()["assets"][1]["browser_download_url"],
        len(payload),
        hashlib.sha256(payload).hexdigest(),
    )
    seen = []
    path = updates.download(good, lambda d, t: seen.append((d, t)), folder=tmp_path)
    assert path.name == "Ventude-Setup-1.0.7.exe" and path.read_bytes() == payload
    assert seen[-1] == (len(payload), len(payload))

    bad = updates.Update("1.0.8", "", good.url, 0, "0" * 64)
    with pytest.raises(net.NetError, match="não confere"):
        updates.download(bad, folder=tmp_path)
    assert not list(tmp_path.glob("*.part"))

    stop = threading.Event()
    stop.set()
    with pytest.raises(updates.Cancelled):
        updates.download(good, cancel=stop, folder=tmp_path)

    evil = updates.Update("1.0.9", "", "https://evilgithub.com/Ventude-Setup.exe", 0)
    with pytest.raises(net.NetError, match="inesperado"):
        updates.download(evil, folder=tmp_path)


def test_update_dialog_flow(qapp, monkeypatch, tmp_path):
    from ventude.core.tasks import Tasks
    from ventude.ui.update_dialog import UpdateDialog

    installed, quit_called = [], []
    target = tmp_path / "Ventude-Setup-1.0.7.exe"
    monkeypatch.setattr(updates, "download", lambda u, progress, cancel: (progress(5, 10), target)[1])
    monkeypatch.setattr(updates, "launch_installer", installed.append)
    tasks = Tasks()
    dialog = UpdateDialog(
        updates.Update("1.0.7", "- Novidade", "https://github.com/x", 10),
        tasks,
        lambda: quit_called.append(True),
    )
    dialog.show()
    dialog._start()
    import time

    end = time.monotonic() + 3
    while not installed and time.monotonic() < end:
        qapp.processEvents()
    tasks.shutdown()
    assert installed == [target] and quit_called == [True]


def test_stamp_version_starts_line_at_pyproject_version():
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parent.parent / "scripts" / "stamp_version.py"
    spec = importlib.util.spec_from_file_location("stamp_version", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.base_version() == (2, 0, 0)
    assert module.next_version((2, 0, 0), ["v1.2.4", "v1.2.5"]) == "2.0.0"  # primeira da linha 2.0
    assert module.next_version((1, 1, 0), ["v1.0.7", "v1.0.8"]) == "1.1.0"
    assert module.next_version((1, 1, 0), ["v1.0.8", "v1.1.0", "v1.1.1", "v1.1.x"]) == "1.1.2"
    assert module.next_version((1, 2, 0), ["v1.1.9"]) == "1.2.0"
    assert updates.parse_version("v1.1.0") > updates.parse_version("1.0.42")
