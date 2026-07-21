"""
NemoHeadUnit-Wireless — Global Test Fixtures
============================================
Infrastruttura condivisa per tutta la test suite.

Fixture disponibili:
  in_process_broker  — broker ZMQ XPUB/XSUB reale su socket IPC univoci
  bus_client         — BusClient connesso al broker in-process
  mock_bus           — mock leggero senza socket ZMQ per unit test veloci
  aa_frame_factory   — factory per frame AA wire-format validi e invalidi
  qt_app             — QApplication offscreen (scope=session)
  dbus_session       — D-Bus session dedicata per test bluetooth
  hardware_available — utility per rilevamento device fisici a runtime

Rif: docs/TEST_SUITE_ARCHITECTURE.md §4
"""

from __future__ import annotations

import json
import importlib
import os
import struct
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Callable
from unittest.mock import MagicMock, call

import pytest
import zmq

# ---------------------------------------------------------------------------
# sys.path setup — garantisce import di shared, modules e protos da qualsiasi
# directory di esecuzione (root del repo dopo promozione da v2/).
# ---------------------------------------------------------------------------

_HERE = Path(__file__).parent          # tests/
_ROOT = _HERE.parent                   # root del repo

for _p in (_ROOT / "modules", _ROOT / "protos", _ROOT / "shared", _ROOT / "modules" / "channel_modules", _ROOT / "tests"):
    _s = str(_p)
    if _s not in sys.path:
        sys.path.insert(0, _s)


# ===========================================================================
# Proto Import Hook — Pre-loads all proto modules to avoid descriptor pool errors
# ===========================================================================

def _preload_all_protos():
    """Pre-carica tutti i moduli proto per popolare il descriptor pool.
    Deve essere invocato prima che i moduli di test effettuino i propri import."""
    _proto_root = _ROOT / "protos"
    if not _proto_root.exists():
        return

    for _proto_file in _proto_root.rglob("*_pb2.py"):
        # Converte il path in nome modulo (es. oaa/av/AVChannelData_pb2.py -> oaa.av.AVChannelData_pb2)
        rel_path = _proto_file.relative_to(_proto_root)
        module_name = str(rel_path.with_suffix('')).replace(os.sep, '.')
        try:
            if module_name not in sys.modules:
                __import__(module_name)
        except Exception:
            pass

# Esecuzione immediata al caricamento di conftest.py
_preload_all_protos()


try:
    # In the test process many BusClient/ZMQ objects are intentionally mocked or
    # abandoned by crash-path tests.  pyzmq's Context.__del__ can block forever
    # waiting for sockets owned by background threads, so tests rely on explicit
    # close/term paths and keep GC non-blocking.
    zmq.Context.__del__ = lambda self: None
except Exception:
    pass


def _restore_real_module(module_name: str) -> None:
    """Re-import a real module after collection-time tests installed stubs."""
    sys.modules.pop(module_name, None)
    module = importlib.import_module(module_name)
    if module_name == "zmq":
        try:
            module.Context.__del__ = lambda self: None
        except Exception:
            pass


def pytest_collection_finish(session):
    """Undo collection-time sys.modules stubs that would leak into later tests.

    Some unit files import their module under test with fake shared/logging/ZMQ
    dependencies at module import time.  Pytest imports every test file before
    running tests, so those fakes can otherwise affect unrelated tests whose
    modules are imported lazily during the test body.
    """
    for module_name in (
        "zmq",
        "loguru",
        "shared.config_schema",
        "shared.proto_utils",
        "shared.config_client",
        "shared.logger",
        "dbus",
        "dbus.service",
        "dbus.mainloop",
        "dbus.mainloop.glib",
    ):
        try:
            _restore_real_module(module_name)
        except Exception:
            pass


@pytest.fixture(autouse=True)
def _stable_test_environment(monkeypatch):
    """Keep ambient shell/debug state from changing deterministic unit tests."""
    monkeypatch.delenv("DEBUG", raising=False)


def pytest_runtest_setup(item):
    """Repair globals in test modules that may have imported collection stubs."""
    module = getattr(item, "module", None)
    if module is None:
        return

    if module.__name__.endswith("test_proto_utils"):
        real = importlib.import_module("shared.proto_utils")
        for name in (
            "decode_proto",
            "encode_proto",
            "encode_aa_frame",
            "decode_aa_frame",
            "parse_media_with_timestamp",
            "build_media_with_timestamp",
            "proto_to_dict",
            "dict_to_proto",
            "schema_from_proto_message",
            "channels_from_sdr_bytes",
            "channel_config_from_sdr",
            "_read_varint",
            "_skip_field",
        ):
            setattr(module, name, getattr(real, name))

    if module.__name__.endswith("test_config_client"):
        real_client = importlib.reload(importlib.import_module("shared.config_client"))
        real_schema = importlib.import_module("shared.config_schema")
        module.ConfigClient = real_client.ConfigClient
        module.field_int = real_schema.field_int
        module.field_enum = real_schema.field_enum


# ===========================================================================
# Hardware detection
# ===========================================================================

def hardware_available(device: str) -> bool:
    """Rileva a runtime se un device fisico è disponibile.

    Parametri supportati:
      "audio"      — sounddevice.query_devices() senza eccezioni
      "bluetooth"  — systemctl is-active bluetooth exit 0
      "gst_sw"     — GStreamer avdec_h264 disponibile
      "gst_vaapi"  — GStreamer vaapih264dec disponibile
      "dbus"       — dbus.SystemBus() senza eccezioni
      "qt"         — sempre True (offscreen)
    """
    if device == "qt":
        return True
    if device == "audio":
        try:
            import sounddevice as sd
            sd.query_devices()
            return True
        except Exception:
            return False
    if device == "bluetooth":
        try:
            result = subprocess.run(
                ["systemctl", "is-active", "bluetooth"],
                capture_output=True,
                timeout=3,
            )
            return result.returncode == 0
        except Exception:
            return False
    if device == "gst_sw":
        try:
            import gi
            gi.require_version("Gst", "1.0")
            from gi.repository import Gst
            Gst.init(None)
            return Gst.ElementFactory.find("avdec_h264") is not None
        except Exception:
            return False
    if device == "gst_vaapi":
        try:
            import gi
            gi.require_version("Gst", "1.0")
            from gi.repository import Gst
            Gst.init(None)
            return Gst.ElementFactory.find("vaapih264dec") is not None
        except Exception:
            return False
    if device == "dbus":
        try:
            import dbus
            dbus.SystemBus()
            return True
        except Exception:
            return False
    return False


# Costanti pre-calcolate (valutate una volta sola alla raccolta dei test)
AUDIO_AVAILABLE     = hardware_available("audio")
BLUETOOTH_AVAILABLE = hardware_available("bluetooth")
GST_SW_AVAILABLE    = hardware_available("gst_sw")
GST_VAAPI_AVAILABLE = hardware_available("gst_vaapi")
DBUS_AVAILABLE      = hardware_available("dbus")


# ===========================================================================
# in_process_broker
# ===========================================================================

class _BrokerThread(threading.Thread):
    """Thread che esegue uno zmq.proxy XPUB/XSUB su socket IPC univoci."""

    def __init__(self, pub_addr: str, sub_addr: str):
        super().__init__(daemon=True)
        self.pub_addr = pub_addr  # moduli pubblicano qui (PUSH→broker)
        self.sub_addr = sub_addr  # moduli si iscrivono qui (SUB←broker)
        self._ctx: zmq.Context | None = None
        self._xsub: zmq.Socket | None = None
        self._xpub: zmq.Socket | None = None
        self._ctrl: zmq.Socket | None = None
        self._ctrl_addr = f"inproc://broker-ctrl-{uuid.uuid4().hex}"
        self.ready = threading.Event()

    def run(self) -> None:
        self._ctx  = zmq.Context()
        self._xsub = self._ctx.socket(zmq.XSUB)
        self._xsub.bind(self.pub_addr)
        self._xpub = self._ctx.socket(zmq.XPUB)
        self._xpub.bind(self.sub_addr)
        self._ctrl = self._ctx.socket(zmq.PULL)
        self._ctrl.bind(self._ctrl_addr)
        self.ready.set()
        try:
            zmq.proxy_steerable(self._xsub, self._xpub, None, self._ctrl)
        except zmq.ZMQError:
            pass
        finally:
            for s in (self._xsub, self._xpub, self._ctrl):
                try:
                    s.close(linger=0)
                except Exception:
                    pass
            try:
                self._ctx.term()
            except Exception:
                pass

    def stop(self, timeout: float = 2.0) -> None:
        """Ferma il broker tramite TERMINATE poison pill."""
        try:
            ctrl_push = self._ctx.socket(zmq.PUSH)
            ctrl_push.connect(self._ctrl_addr)
            ctrl_push.send(zmq.Frame(b"TERMINATE"))
            ctrl_push.close(linger=0)
        except Exception:
            pass
        self.join(timeout=timeout)


@pytest.fixture
def in_process_broker():
    """Broker ZMQ XPUB/XSUB reale su socket IPC univoci per test.

    Fornisce un dict con:
      pub_addr  — indirizzo PUB (i moduli pubblicano qui)
      sub_addr  — indirizzo SUB (i moduli si iscrivono qui)

    Teardown: poison pill + join thread timeout 2s.

    Rif: TEST_SUITE_ARCHITECTURE.md §4.1
    """
    uid = uuid.uuid4().hex
    pub_addr = f"ipc:///tmp/nemotest-{uid}.pub"
    sub_addr = f"ipc:///tmp/nemotest-{uid}.sub"

    broker = _BrokerThread(pub_addr=pub_addr, sub_addr=sub_addr)
    broker.start()
    broker.ready.wait(timeout=3.0)
    # Piccola pausa per permettere al broker di mettersi in ascolto
    time.sleep(0.05)
    yield {"pub_addr": pub_addr, "sub_addr": sub_addr, "_broker": broker}

    broker.stop()


# ===========================================================================
# bus_client
# ===========================================================================

class _TestBusClient:
    """Wrapper leggero attorno a BusClient con helper per i test."""

    def __init__(self, pub_addr: str, sub_addr: str, module_name: str = "test_client"):
        from shared.bus_client import BusClient  # noqa: import locale

        # Monkey-patch gli indirizzi del broker prima dell'istanza
        import shared.bus_client as _bc_mod
        _bc_mod.BROKER_PUB_ADDR = pub_addr
        _bc_mod.BROKER_SUB_ADDR = sub_addr

        self._client = BusClient(module_name=module_name)
        self._recv_thread: threading.Thread | None = None
        self._received: list[tuple[str, dict]] = []
        self._lock = threading.Lock()
        self._waiters: dict[str, threading.Event] = {}
        self._waiter_payloads: dict[str, dict] = {}

    def publish(self, topic: str, payload: dict) -> bool:
        return self._client.publish(topic, payload)

    def subscribe(self, topic: str, handler: Callable | None = None) -> None:
        """Iscrive al topic. Se handler è None registra in _received."""
        if handler is None:
            def _default(t: str, p: dict) -> None:
                with self._lock:
                    self._received.append((t, p))
                if t in self._waiters:
                    self._waiter_payloads[t] = p
                    self._waiters[t].set()
            self._client.subscribe(topic, _default)
        else:
            self._client.subscribe(topic, handler)

    def start(self) -> None:
        self._recv_thread = self._client.start(blocking=False)

    def stop(self) -> None:
        self._client.stop()

    def wait_for(self, topic: str, timeout: float = 2.0) -> dict | None:
        """Attende la ricezione di un messaggio sul topic dato.

        Restituisce il payload o None se scade il timeout.
        Deve essere chiamato DOPO subscribe(topic) e start().
        """
        event = threading.Event()
        with self._lock:
            self._waiters[topic] = event
        # Controlla se già ricevuto
        for t, p in self._received:
            if t == topic:
                return p
        event.wait(timeout=timeout)
        return self._waiter_payloads.get(topic)

    @property
    def received(self) -> list[tuple[str, dict]]:
        with self._lock:
            return list(self._received)


@pytest.fixture
def bus_client(in_process_broker):
    """_TestBusClient connesso al broker in-process.

    Espone: publish(topic, payload), subscribe(topic, callback),
            wait_for(topic, timeout), received.

    Rif: TEST_SUITE_ARCHITECTURE.md §4.1
    """
    client = _TestBusClient(
        pub_addr=in_process_broker["pub_addr"],
        sub_addr=in_process_broker["sub_addr"],
    )
    client.start()
    # Piccola pausa per permettere la connessione dei socket
    time.sleep(0.05)
    yield client
    client.stop()


# ===========================================================================
# mock_bus
# ===========================================================================

class _MockBus:
    """Mock leggero del bus per unit test senza socket ZMQ.

    Registra tutte le chiamate publish/subscribe per asserzioni.
    Non avvia nessun thread né socket reale.
    """

    def __init__(self):
        self._published: list[tuple[str, dict]] = []
        self._subscriptions: dict[str, list[Callable]] = {}
        # Attributo mock per compatibilità con BusClient.publish spy
        self.publish = MagicMock(side_effect=self._record_publish)
        self.subscribe = MagicMock(side_effect=self._record_subscribe)

    def _record_publish(self, topic: str, payload: dict) -> bool:
        self._published.append((topic, payload))
        return True

    def _record_subscribe(self, topic: str, handler: Callable) -> None:
        self._subscriptions.setdefault(topic, []).append(handler)

    def trigger(self, topic: str, payload: dict) -> None:
        """Simula la ricezione di un messaggio: invoca tutti gli handler registrati."""
        for handler in self._subscriptions.get(topic, []):
            handler(topic, payload)

    def published_topics(self) -> list[str]:
        """Restituisce la lista dei topic pubblicati in ordine."""
        return [t for t, _ in self._published]

    def last_payload(self, topic: str) -> dict | None:
        """Restituisce l'ultimo payload pubblicato su un dato topic."""
        for t, p in reversed(self._published):
            if t == topic:
                return p
        return None

    def reset(self) -> None:
        """Azzera la cronologia per isolare i test."""
        self._published.clear()
        self._subscriptions.clear()
        self.publish.reset_mock(side_effect=self._record_publish)
        self.subscribe.reset_mock(side_effect=self._record_subscribe)


@pytest.fixture
def mock_bus():
    """Mock leggero del bus per unit test senza socket ZMQ.

    Registra tutte le chiamate publish/subscribe per asserzioni.
    Espone: publish, subscribe, trigger(topic, payload),
            published_topics(), last_payload(topic), reset().

    Rif: TEST_SUITE_ARCHITECTURE.md §4.1
    """
    return _MockBus()


# ===========================================================================
# aa_frame_factory
# ===========================================================================

class _AAFrameFactory:
    """Factory per frame AA wire-format validi e invalidi.

    Struttura frame AA wire (header 6 byte):
      [0:2]  lunghezza payload (big-endian uint16) — esclusi i 6 byte header
      [2]    channel_id
      [3]    flag (0x0B per frame di controllo, 0x00 per media)
      [4:6]  message_type (big-endian uint16)

    Rif: TEST_SUITE_ARCHITECTURE.md §4.3
    """

    # Costanti message_type (approssimative per scopo di test)
    MSG_SETUP_REQUEST        = 0x0001
    MSG_CHANNEL_OPEN_REQUEST = 0x0003
    MSG_AV_MEDIA_DATA        = 0x0005
    MSG_AV_MEDIA_INDICATION  = 0x000D

    def _build_frame(
        self,
        channel_id: int,
        msg_type: int,
        payload: bytes,
        flag: int = 0x0B,
    ) -> bytes:
        header = struct.pack(">HBBH", len(payload), channel_id, flag, msg_type)
        return header + payload

    def setup_request(self, channel_id: int = 1, config: dict | None = None) -> bytes:
        """Frame SETUP_REQUEST valido per il canale indicato."""
        cfg = config or {"audio_configs": [], "video_configs": []}
        payload = json.dumps(cfg).encode()
        return self._build_frame(channel_id, self.MSG_SETUP_REQUEST, payload)

    def channel_open_request(self, channel_id: int = 1) -> bytes:
        """Frame CHANNEL_OPEN_REQUEST per il canale indicato."""
        return self._build_frame(channel_id, self.MSG_CHANNEL_OPEN_REQUEST, b"{}")

    def av_media_with_timestamp(self, ts_us: int, pcm_bytes: bytes) -> bytes:
        """Frame AV_MEDIA_DATA con timestamp e payload PCM."""
        ts_header = struct.pack(">Q", ts_us)
        return self._build_frame(0, self.MSG_AV_MEDIA_DATA, ts_header + pcm_bytes, flag=0x00)

    def av_media_indication(self, asc_bytes: bytes) -> bytes:
        """Frame AV_MEDIA_INDICATION (codec_data AAC)."""
        return self._build_frame(0, self.MSG_AV_MEDIA_INDICATION, asc_bytes, flag=0x00)

    def h264_idr_frame(self, width: int = 800, height: int = 480) -> bytes:
        """Frame H.264 IDR fittizio (Annex-B start code + NALU tipo 5)."""
        # start code + IDR NALU tipo 5 con dimensioni fake
        nalu = b"\x00\x00\x00\x01\x65" + bytes(width * height // 8)
        return self._build_frame(1, self.MSG_AV_MEDIA_DATA, nalu, flag=0x00)

    def h264_p_frame(self) -> bytes:
        """Frame H.264 P-frame fittizio."""
        nalu = b"\x00\x00\x00\x01\x41" + bytes(64)
        return self._build_frame(1, self.MSG_AV_MEDIA_DATA, nalu, flag=0x00)

    def malformed(self, strategy: str = "random_bytes") -> bytes:
        """Frame intenzionalmente malformato.

        Strategie:
          truncated_header   — solo 3 byte (header incompleto)
          zero_payload       — header valido con payload vuoto
          overflow_channel   — channel_id=255 (fuori range atteso)
          wrong_msg_type     — msg_type=0xFFFF (sconosciuto)
          random_bytes       — 32 byte casuali non strutturati
        """
        import os as _os
        strategies = {
            "truncated_header":  b"\x00\x0A\x01",
            "zero_payload":      self._build_frame(1, self.MSG_SETUP_REQUEST, b""),
            "overflow_channel":  self._build_frame(255, self.MSG_SETUP_REQUEST, b"{}"),
            "wrong_msg_type":    self._build_frame(1, 0xFFFF, b"{}"),
            "random_bytes":      _os.urandom(32),
        }
        return strategies.get(strategy, strategies["random_bytes"])


@pytest.fixture
def aa_frame_factory() -> _AAFrameFactory:
    """Factory per frame AA wire-format validi e invalidi.

    Metodi: setup_request, channel_open_request, av_media_with_timestamp,
            av_media_indication, h264_idr_frame, h264_p_frame, malformed.

    Rif: TEST_SUITE_ARCHITECTURE.md §4.3
    """
    return _AAFrameFactory()


# ===========================================================================
# mock_config
# ===========================================================================

@pytest.fixture
def mock_config(tmp_path):
    """Config YAML minimale in tmp_path.

    Restituisce il Path del file YAML creato.
    Parametrizzabile nel test: sovrascrivere il contenuto dopo la fixture.

    Rif: TEST_SUITE_ARCHITECTURE.md §4.2
    """
    import yaml  # type: ignore

    config_data = {
        "hu": {
            "name": "NemoHeadUnit-Test",
            "video_dpi": 140,
            "screen_width": 800,
            "screen_height": 480,
        },
        "bluetooth": {
            "auto_accept_pairing": True,
            "auto_connect": True,
        },
        "audio": {
            "output_sink": "auto",
            "input_source": "auto",
        },
    }
    config_file = tmp_path / "config.yaml"
    config_file.write_text(yaml.dump(config_data))
    return config_file


# ===========================================================================
# qt_app  (scope=session — una sola QApplication per sessione)
# ===========================================================================

@pytest.fixture(scope="session")
def qt_app():
    """QApplication con offscreen platform (scope=session).

    Imposta QT_QPA_PLATFORM=offscreen prima dell'import di Qt.
    Skippato automaticamente se PyQt6 non è installato.

    Rif: TEST_SUITE_ARCHITECTURE.md §4.6
    """
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    try:
        from PyQt6.QtWidgets import QApplication
    except ImportError:
        pytest.skip("PyQt6 non disponibile")

    app = QApplication.instance() or QApplication([])
    yield app
    # Non chiamare app.quit() a livello di session — potrebbe non essere sicuro


# ===========================================================================
# dbus_session  (D-Bus dedicata per test bluetooth)
# ===========================================================================

@pytest.fixture
def dbus_session():
    """Sessione D-Bus dedicata per test bluetooth.

    Avvia dbus-daemon --session come subprocess, setta DBUS_SESSION_BUS_ADDRESS.
    Teardown garantito. Skippato se dbus-daemon non è disponibile.

    Rif: TEST_SUITE_ARCHITECTURE.md §4.7
    """
    try:
        result = subprocess.run(
            ["dbus-daemon", "--session", "--print-address", "--fork"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            pytest.skip("dbus-daemon non disponibile")
        bus_address = result.stdout.strip()
        if not bus_address:
            pytest.skip("dbus-daemon non ha restituito un indirizzo")
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pytest.skip("dbus-daemon non trovato")

    original_addr = os.environ.get("DBUS_SESSION_BUS_ADDRESS")
    os.environ["DBUS_SESSION_BUS_ADDRESS"] = bus_address

    yield bus_address

    # Teardown: ripristina variabile d'ambiente
    if original_addr is not None:
        os.environ["DBUS_SESSION_BUS_ADDRESS"] = original_addr
    elif "DBUS_SESSION_BUS_ADDRESS" in os.environ:
        del os.environ["DBUS_SESSION_BUS_ADDRESS"]

    # Termina il daemon
    try:
        subprocess.run(
            ["dbus-daemon", "--session", "--address", bus_address, "--exit-on-disconnect"],
            capture_output=True,
            timeout=2,
        )
    except Exception:
        pass


# ===========================================================================
# Fixture parametrizzata hardware/mock (pattern standard §6)
# ===========================================================================

@pytest.fixture(params=[
    "mock",
    pytest.param(
        "hardware",
        marks=pytest.mark.skipif(not AUDIO_AVAILABLE, reason="Nessun device audio rilevato"),
    ),
])
def audio_source(request):
    """Fixture parametrizzata mock/hardware per test audio.

    Gira con entrambi i parametri se il device è disponibile, solo mock
    altrimenti. Pattern standard per tutti i test che toccano device fisici.

    Rif: TEST_SUITE_ARCHITECTURE.md §6
    """
    if request.param == "mock":
        mock = MagicMock()
        mock.name = "MockAudioSource"
        return mock
    # hardware — il parametro è skippato in CI senza device audio
    return request.param  # i test usano questo valore per setup reale
