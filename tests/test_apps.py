"""The two runners' loops without a radio: start, wait, stop."""

import threading
import time

import pytest

from frlg_minigames import pokejump_app
from frlg_minigames.berrypick_app import BerryPickApp
from frlg_minigames.profile import LdnConfig


@pytest.fixture
def keys(tmp_path):
    path = tmp_path / "prod.keys"
    path.write_text("")
    return str(path)


class IdleHostTransport:
    """A group nobody joins."""
    NEEDS_RADIO = False

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.participants = []
        self.started = self.stopped = False

    def start(self, preflight=True):
        self.started = True

    def recv(self):
        return []

    def send(self, data, destination):
        pass

    def wait_readable(self, timeout):
        time.sleep(min(timeout, 0.01))

    def stop(self):
        self.stopped = True


def test_berry_pick_app_opens_the_group_and_stops(keys):
    said, stop = [], threading.Event()
    app = BerryPickApp(ldn=LdnConfig(keys_path=keys), log=lambda *a: None, status=said.append,
                       stop=stop, transport_factory=IdleHostTransport)
    threading.Timer(0.3, stop.set).start()
    assert app.run() is False
    assert app.network.started and app.network.stopped
    assert app.network.kwargs["nickname"] == "Ash"
    assert said == ["Group open. On the Switch: JOIN GROUP and choose Ash."]


def test_berry_pick_app_needs_the_keys(tmp_path):
    app = BerryPickApp(ldn=LdnConfig(keys_path=str(tmp_path / "missing.keys")), log=lambda *a: None,
                       transport_factory=IdleHostTransport)
    with pytest.raises(FileNotFoundError):
        app.run()


def test_pokejump_app_keeps_looking_until_stopped(keys, monkeypatch):
    scans, stop = [], threading.Event()

    class NoGroup:
        def __init__(self, **kwargs):
            scans.append(kwargs)

        def start(self, attempts=3):
            raise RuntimeError("LDN join failed after 1 attempt(s):\nattempt 1: no joinable FRLG network")

    monkeypatch.setattr(pokejump_app.tmod, "LiveTransport", NoGroup)
    monkeypatch.setattr(pokejump_app, "RESCAN_SECONDS", 0.01)
    said = []
    app = pokejump_app.PokeJumpApp(ldn=LdnConfig(keys_path=keys), log=lambda *a: None,
                                   status=said.append, stop=stop)
    threading.Timer(0.3, stop.set).start()
    assert app.run() is False
    assert len(scans) > 3 and scans[0]["nickname"] == "Ash"
    assert said == ["Looking for your group. On the Switch: BECOME LEADER."]


def test_pokejump_app_reports_other_errors(keys, monkeypatch):
    class Broken:
        def __init__(self, **kwargs):
            pass

        def start(self, attempts=3):
            raise RuntimeError("could not open port 'COM9'")

    monkeypatch.setattr(pokejump_app.tmod, "LiveTransport", Broken)
    app = pokejump_app.PokeJumpApp(ldn=LdnConfig(keys_path=keys), log=lambda *a: None)
    with pytest.raises(RuntimeError, match="COM9"):
        app.run()
