"""Pass 24, item 6 (user: 「asioもrbの使って」): the app enables
sounddevice's ASIO PortAudio slot only when the ASIO DLL is
really present. The DLL itself is built by CI (vcpkg
portaudio[asio]) — what is testable here is the gate: no DLL,
no SD_ENABLE_ASIO (setting it without the file would make
sounddevice's import fail outright); DLL present, enabled.
The packaged-exe proof (smoke inventory listing an ASIO host)
lives in CI, not here.
"""
import os

import pytest

try:
    import app as appmod
    HAVE_APP = True
except Exception:
    HAVE_APP = False

pytestmark = pytest.mark.skipif(not HAVE_APP,
                                reason='app module unavailable')


def test_asio_slot_stays_off_without_the_dll(tmp_path, monkeypatch):
    monkeypatch.delenv('SD_ENABLE_ASIO', raising=False)
    folder = tmp_path / 'portaudio-binaries'
    folder.mkdir()
    assert appmod._enable_bundled_asio(roots=[folder]) is False
    assert 'SD_ENABLE_ASIO' not in os.environ


def test_asio_slot_turns_on_with_the_dll(tmp_path, monkeypatch):
    monkeypatch.delenv('SD_ENABLE_ASIO', raising=False)
    folder = tmp_path / 'portaudio-binaries'
    folder.mkdir()
    (folder / 'libportaudio64bit-asio.dll').write_bytes(b'placeholder')
    try:
        assert appmod._enable_bundled_asio(roots=[folder]) is True
        assert os.environ['SD_ENABLE_ASIO'] == '1'
        # Already on stays on (and short-circuits).
        assert appmod._enable_bundled_asio(roots=[]) is True
    finally:
        os.environ.pop('SD_ENABLE_ASIO', None)
