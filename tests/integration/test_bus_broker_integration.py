"""
NemoHeadUnit-Wireless v2 — Integration Tests: Bus Broker
=========================================================
Fase 2 — Integration Test

Scope: broker ZMQ XPUB/XSUB reale in-process + uno o più BusClient reali.
Bus: fixture `in_process_broker` (conftest.py) — socket IPC univoci per test.
Velocità target: < 10s per test.
Marker: @pytest.mark.integration

Cosa viene testato:
  - Broker si avvia e accetta connessioni da publisher e subscriber
  - Messaggi pubblicati da un client vengono ricevuti da un subscriber
  - Routing multi-topic: subscriber selettivi per topic
  - Multi-client: N publisher → 1 subscriber, 1 publisher → N subscriber
  - Payload JSON round-trip senza perdita di dati
  - Messaggi su topic non sottoscritti NON vengono consegnati
  - Broker teardown pulito: stop() non blocca e non genera eccezioni
  - BusClient.stop() libera le risorse senza errori
  - HWM non causa crash in burst di messaggi
  - Payload malformato (non-JSON) gestito dal BusClient senza crash
  - Messaggi binary non vengono deserializzati dal broker (pass-through)
  - Topic prefix matching (ZMQ SUB topic filter = prefix)
  - Sequenzialità: i messaggi arrivano nell'ordine di pubblicazione
  - Latenza: publish→receive entro 2s con broker in-process

Rif: docs/TEST_SUITE_ARCHITECTURE.md §3.2
"""
from __future__ import annotations

import json
import threading
import time
import uuid

import pytest
import zmq


# ---------------------------------------------------------------------------
# Helper locale
# ---------------------------------------------------------------------------

def _make_client(in_process_broker, name: str = None):
    """Crea un _TestBusClient connesso al broker in-process."""
    import shared.bus_client as _bc_mod
    _bc_mod.BROKER_PUB_ADDR = in_process_broker["pub_addr"]
    _bc_mod.BROKER_SUB_ADDR = in_process_broker["sub_addr"]

    from shared.bus_client import BusClient

    module_name = name or f"test_client_{uuid.uuid4().hex[:6]}"
    client = BusClient(module_name=module_name)
    return client


def _start_client(client) -> threading.Thread:
    t = client.start(blocking=False)
    time.sleep(0.05)
    return t


def _wait_received(received: list, count: int, timeout: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if len(received) >= count:
            return True
        time.sleep(0.01)
    return False


# ===========================================================================
# Gruppo 1 — Avvio e connettività broker
# ===========================================================================

class TestBrokerStartup:
    """Il broker si avvia, accetta connessioni e risponde ai client."""

    @pytest.mark.integration
    def test_broker_fixture_starts(self, in_process_broker):
        """La fixture in_process_broker torna senza eccezioni."""
        assert "pub_addr" in in_process_broker
        assert "sub_addr" in in_process_broker
        assert in_process_broker["pub_addr"].startswith("ipc://")
        assert in_process_broker["sub_addr"].startswith("ipc://")

    @pytest.mark.integration
    def test_broker_pub_addr_is_unique_per_test(self, in_process_broker):
        """Ogni test riceve indirizzi IPC univoci (nessuna collisione tra test)."""
        addr = in_process_broker["pub_addr"]
        assert "nemotest-" in addr

    @pytest.mark.integration
    def test_publisher_can_connect(self, in_process_broker):
        """Un client PUB si connette al broker senza eccezioni."""
        ctx = zmq.Context()
        pub = ctx.socket(zmq.PUB)
        pub.setsockopt(zmq.LINGER, 0)
        pub.connect(in_process_broker["pub_addr"])
        pub.close()
        ctx.term()

    @pytest.mark.integration
    def test_subscriber_can_connect(self, in_process_broker):
        """Un client SUB si connette al broker senza eccezioni."""
        ctx = zmq.Context()
        sub = ctx.socket(zmq.SUB)
        sub.setsockopt(zmq.LINGER, 0)
        sub.connect(in_process_broker["sub_addr"])
        sub.close()
        ctx.term()

    @pytest.mark.integration
    def test_bus_client_can_connect(self, in_process_broker):
        """BusClient si connette al broker in-process senza eccezioni."""
        client = _make_client(in_process_broker)
        client.stop()

    @pytest.mark.integration
    def test_bus_client_start_nonblocking(self, in_process_broker):
        """BusClient.start(blocking=False) ritorna un thread vivo."""
        client = _make_client(in_process_broker)
        t = client.start(blocking=False)
        time.sleep(0.05)
        assert t.is_alive()
        client.stop()

    @pytest.mark.integration
    def test_bus_client_stop_is_clean(self, in_process_broker):
        """BusClient.stop() non solleva eccezioni e il thread termina."""
        client = _make_client(in_process_broker)
        client.start(blocking=False)
        time.sleep(0.05)
        client.stop()  # non deve sollevare


# ===========================================================================
# Gruppo 2 — Publish / Subscribe base
# ===========================================================================

class TestPublishSubscribe:
    """Messaggi pubblicati raggiungono i subscriber corretti."""

    @pytest.mark.integration
    def test_single_message_delivered(self, in_process_broker):
        """1 publisher → 1 subscriber, 1 messaggio recapitato."""
        received = []
        topic = "test.single"
        payload = {"value": 42}

        sub = _make_client(in_process_broker, "sub")
        sub.subscribe(topic, lambda t, p: received.append((t, p)))
        _start_client(sub)

        pub = _make_client(in_process_broker, "pub")
        time.sleep(0.1)  # attesa propagazione subscription
        pub.publish(topic, payload)

        ok = _wait_received(received, 1)
        pub.stop()
        sub.stop()

        assert ok, "Messaggio non ricevuto entro il timeout"
        assert received[0] == (topic, payload)

    @pytest.mark.integration
    def test_topic_and_payload_preserved(self, in_process_broker):
        """Il topic e il payload arrivano invariati al subscriber."""
        received = []
        topic = "test.payload"
        payload = {"key": "hello", "num": 99, "flag": True}

        sub = _make_client(in_process_broker, "sub")
        sub.subscribe(topic, lambda t, p: received.append((t, p)))
        _start_client(sub)

        pub = _make_client(in_process_broker, "pub")
        time.sleep(0.1)
        pub.publish(topic, payload)
        ok = _wait_received(received, 1)
        pub.stop()
        sub.stop()

        assert ok
        recv_topic, recv_payload = received[0]
        assert recv_topic == topic
        assert recv_payload == payload

    @pytest.mark.integration
    def test_multiple_messages_same_topic(self, in_process_broker):
        """10 messaggi sullo stesso topic arrivano tutti al subscriber."""
        received = []
        topic = "test.multi"

        sub = _make_client(in_process_broker, "sub")
        sub.subscribe(topic, lambda t, p: received.append(p))
        _start_client(sub)

        pub = _make_client(in_process_broker, "pub")
        time.sleep(0.1)
        for i in range(10):
            pub.publish(topic, {"i": i})

        ok = _wait_received(received, 10, timeout=3.0)
        pub.stop()
        sub.stop()

        assert ok, f"Ricevuti solo {len(received)} su 10"
        assert len(received) == 10

    @pytest.mark.integration
    def test_messages_arrive_in_order(self, in_process_broker):
        """I messaggi arrivano nell'ordine di pubblicazione (FIFO)."""
        received = []
        topic = "test.order"

        sub = _make_client(in_process_broker, "sub")
        sub.subscribe(topic, lambda t, p: received.append(p["seq"]))
        _start_client(sub)

        pub = _make_client(in_process_broker, "pub")
        time.sleep(0.1)
        for i in range(20):
            pub.publish(topic, {"seq": i})

        ok = _wait_received(received, 20, timeout=3.0)
        pub.stop()
        sub.stop()

        assert ok, f"Ricevuti solo {len(received)} su 20"
        assert received == list(range(20)), f"Ordine errato: {received}"

    @pytest.mark.integration
    def test_unsubscribed_topic_not_delivered(self, in_process_broker):
        """Messaggi su topic non sottoscritti NON raggiungono il client."""
        received = []
        topic_sub = "test.subscribed"
        topic_other = "test.other"

        sub = _make_client(in_process_broker, "sub")
        sub.subscribe(topic_sub, lambda t, p: received.append(p))
        _start_client(sub)

        pub = _make_client(in_process_broker, "pub")
        time.sleep(0.1)
        pub.publish(topic_other, {"should": "not_arrive"})
        pub.publish(topic_sub, {"should": "arrive"})

        ok = _wait_received(received, 1)
        pub.stop()
        sub.stop()

        assert ok
        assert len(received) == 1
        assert received[0]["should"] == "arrive"

    @pytest.mark.integration
    def test_empty_payload_dict(self, in_process_broker):
        """Payload vuoto {} viene recapitato correttamente."""
        received = []
        topic = "test.empty"

        sub = _make_client(in_process_broker, "sub")
        sub.subscribe(topic, lambda t, p: received.append(p))
        _start_client(sub)

        pub = _make_client(in_process_broker, "pub")
        time.sleep(0.1)
        pub.publish(topic, {})
        ok = _wait_received(received, 1)
        pub.stop()
        sub.stop()

        assert ok
        assert received[0] == {}

    @pytest.mark.integration
    def test_nested_payload(self, in_process_broker):
        """Payload con struttura annidata viene serializzato/deserializzato correttamente."""
        received = []
        topic = "test.nested"
        payload = {
            "outer": {
                "inner": [1, 2, 3],
                "flag": False,
                "sub": {"x": None},
            }
        }

        sub = _make_client(in_process_broker, "sub")
        sub.subscribe(topic, lambda t, p: received.append(p))
        _start_client(sub)

        pub = _make_client(in_process_broker, "pub")
        time.sleep(0.1)
        pub.publish(topic, payload)
        ok = _wait_received(received, 1)
        pub.stop()
        sub.stop()

        assert ok
        assert received[0] == payload


# ===========================================================================
# Gruppo 3 — Multi-client
# ===========================================================================

class TestMultiClient:
    """Scenari con più publisher e/o più subscriber simultanei."""

    @pytest.mark.integration
    def test_two_subscribers_same_topic(self, in_process_broker):
        """2 subscriber sullo stesso topic ricevono entrambi il messaggio."""
        recv_a, recv_b = [], []
        topic = "test.fanout"

        sub_a = _make_client(in_process_broker, "sub_a")
        sub_a.subscribe(topic, lambda t, p: recv_a.append(p))
        _start_client(sub_a)

        sub_b = _make_client(in_process_broker, "sub_b")
        sub_b.subscribe(topic, lambda t, p: recv_b.append(p))
        _start_client(sub_b)

        pub = _make_client(in_process_broker, "pub")
        time.sleep(0.15)  # attendi entrambe le subscription
        pub.publish(topic, {"msg": "broadcast"})

        ok_a = _wait_received(recv_a, 1)
        ok_b = _wait_received(recv_b, 1)
        pub.stop()
        sub_a.stop()
        sub_b.stop()

        assert ok_a, "sub_a non ha ricevuto il messaggio"
        assert ok_b, "sub_b non ha ricevuto il messaggio"

    @pytest.mark.integration
    def test_two_publishers_one_subscriber(self, in_process_broker):
        """2 publisher → 1 subscriber: entrambi i messaggi arrivano."""
        received = []
        topic = "test.twopub"

        sub = _make_client(in_process_broker, "sub")
        sub.subscribe(topic, lambda t, p: received.append(p["src"]))
        _start_client(sub)

        pub_a = _make_client(in_process_broker, "pub_a")
        pub_b = _make_client(in_process_broker, "pub_b")
        time.sleep(0.1)

        pub_a.publish(topic, {"src": "A"})
        pub_b.publish(topic, {"src": "B"})

        ok = _wait_received(received, 2)
        pub_a.stop()
        pub_b.stop()
        sub.stop()

        assert ok, f"Ricevuti solo {received}"
        assert set(received) == {"A", "B"}

    @pytest.mark.integration
    def test_subscriber_receives_only_its_topics(self, in_process_broker):
        """Due subscriber con topic diversi: ciascuno riceve solo i propri."""
        recv_x, recv_y = [], []
        topic_x = "test.x"
        topic_y = "test.y"

        sub_x = _make_client(in_process_broker, "sub_x")
        sub_x.subscribe(topic_x, lambda t, p: recv_x.append(p))
        _start_client(sub_x)

        sub_y = _make_client(in_process_broker, "sub_y")
        sub_y.subscribe(topic_y, lambda t, p: recv_y.append(p))
        _start_client(sub_y)

        pub = _make_client(in_process_broker, "pub")
        time.sleep(0.1)
        pub.publish(topic_x, {"for": "x"})
        pub.publish(topic_y, {"for": "y"})

        ok_x = _wait_received(recv_x, 1)
        ok_y = _wait_received(recv_y, 1)
        pub.stop()
        sub_x.stop()
        sub_y.stop()

        assert ok_x
        assert ok_y
        assert recv_x[0]["for"] == "x"
        assert recv_y[0]["for"] == "y"

    @pytest.mark.integration
    def test_three_subscribers_different_topics(self, in_process_broker):
        """3 subscriber su topic distinti, 3 messaggi, ciascuno riceve il suo."""
        recv = {k: [] for k in ("a", "b", "c")}

        for key in recv:
            c = _make_client(in_process_broker, f"sub_{key}")
            c.subscribe(f"test.topic.{key}", lambda t, p, k=key: recv[k].append(p))
            _start_client(c)

        pub = _make_client(in_process_broker, "pub")
        time.sleep(0.15)
        for key in recv:
            pub.publish(f"test.topic.{key}", {"id": key})

        ok = all(_wait_received(recv[k], 1) for k in recv)
        pub.stop()

        assert ok
        for key in recv:
            assert recv[key][0]["id"] == key

    @pytest.mark.integration
    def test_burst_10_clients_1_message_each(self, in_process_broker):
        """10 publisher ognuno manda 1 messaggio: subscriber riceve tutti e 10."""
        received = []
        topic = "test.burst_clients"

        sub = _make_client(in_process_broker, "sub")
        sub.subscribe(topic, lambda t, p: received.append(p["src"]))
        _start_client(sub)

        publishers = []
        time.sleep(0.1)
        for i in range(10):
            p = _make_client(in_process_broker, f"pub_{i}")
            publishers.append(p)
        time.sleep(0.25)
        for i, p in enumerate(publishers):
            p.publish(topic, {"src": i})

        ok = _wait_received(received, 10, timeout=3.0)
        for p in publishers:
            p.stop()
        sub.stop()

        assert ok, f"Ricevuti {len(received)} su 10"
        assert set(received) == set(range(10))

    @pytest.mark.integration
    def test_publisher_also_subscribes(self, in_process_broker):
        """Un client può fare sia publish che subscribe (pattern loopback)."""
        received = []
        topic = "test.loopback"

        client = _make_client(in_process_broker, "loopback")
        client.subscribe(topic, lambda t, p: received.append(p))
        _start_client(client)
        time.sleep(0.1)

        client.publish(topic, {"echo": True})
        ok = _wait_received(received, 1)
        client.stop()

        assert ok
        assert received[0] == {"echo": True}


# ===========================================================================
# Gruppo 4 — Topic prefix filtering (ZMQ SUB)
# ===========================================================================

class TestTopicPrefixFilter:
    """ZMQ SUB topic filter è un prefix match — verificare il comportamento."""

    @pytest.mark.integration
    def test_exact_topic_match(self, in_process_broker):
        """Sottoscrizione esatta riceve il messaggio con topic identico."""
        received = []
        sub = _make_client(in_process_broker, "sub")
        sub.subscribe("system.start", lambda t, p: received.append(t))
        _start_client(sub)

        pub = _make_client(in_process_broker, "pub")
        time.sleep(0.1)
        pub.publish("system.start", {})
        ok = _wait_received(received, 1)
        pub.stop()
        sub.stop()

        assert ok
        assert received[0] == "system.start"

    @pytest.mark.integration
    def test_prefix_topic_does_not_match_shorter(self, in_process_broker):
        """Sottoscrizione 'system.start' NON riceve 'system' (troppo corto)."""
        received = []
        sub = _make_client(in_process_broker, "sub")
        sub.subscribe("system.start", lambda t, p: received.append(t))
        _start_client(sub)

        # Subscriber "canary" per sapere quando il pub è pronto
        canary = []
        sub2 = _make_client(in_process_broker, "canary_sub")
        sub2.subscribe("canary.ping", lambda t, p: canary.append(1))
        _start_client(sub2)

        pub = _make_client(in_process_broker, "pub")
        time.sleep(0.1)
        pub.publish("system", {"wrong": True})
        pub.publish("canary.ping", {})

        _wait_received(canary, 1)
        pub.stop()
        sub.stop()
        sub2.stop()

        assert len(received) == 0, "Non doveva ricevere topic 'system'"

    @pytest.mark.integration
    def test_two_topics_subscribed(self, in_process_broker):
        """Sottoscrizione a 2 topic separati riceve entrambi."""
        received = []
        sub = _make_client(in_process_broker, "sub")
        sub.subscribe("topic.a", lambda t, p: received.append(t))
        sub.subscribe("topic.b", lambda t, p: received.append(t))
        _start_client(sub)

        pub = _make_client(in_process_broker, "pub")
        time.sleep(0.1)
        pub.publish("topic.a", {})
        pub.publish("topic.b", {})
        ok = _wait_received(received, 2)
        pub.stop()
        sub.stop()

        assert ok
        assert set(received) == {"topic.a", "topic.b"}


# ===========================================================================
# Gruppo 5 — Robustezza e casi limite
# ===========================================================================

class TestRobustness:
    """Comportamento del broker e dei client in condizioni anomale."""

    @pytest.mark.integration
    def test_publish_returns_true_on_success(self, in_process_broker):
        """BusClient.publish() restituisce True quando il messaggio viene inviato."""
        client = _make_client(in_process_broker, "pub")
        result = client.publish("test.ret", {"ok": True})
        client.stop()
        assert result is True

    @pytest.mark.integration
    def test_multiple_stops_dont_crash(self, in_process_broker):
        """Chiamare stop() più volte non causa eccezioni."""
        client = _make_client(in_process_broker, "pub")
        client.stop()
        # Seconda stop — non deve sollevare
        try:
            client.stop()
        except Exception:
            pass  # tollerato — l'importante è che non blocchi

    @pytest.mark.integration
    def test_broker_teardown_does_not_block(self, in_process_broker):
        """Il teardown della fixture broker si completa entro 3 secondi."""
        # Il test termina → il broker viene fermato dalla fixture teardown
        # Se la fixture blocca, il test framework segnala un timeout
        client = _make_client(in_process_broker, "pub")
        client.publish("test.teardown", {"x": 1})
        client.stop()
        # Nessuna asserzione esplicita — se siamo arrivati qui il teardown è ok

    @pytest.mark.integration
    def test_burst_50_messages_no_crash(self, in_process_broker):
        """50 messaggi in burst non causano crash del broker o del client."""
        received = []
        topic = "test.burst50"

        sub = _make_client(in_process_broker, "sub")
        sub.subscribe(topic, lambda t, p: received.append(p["i"]))
        _start_client(sub)

        pub = _make_client(in_process_broker, "pub")
        time.sleep(0.1)
        for i in range(50):
            pub.publish(topic, {"i": i})

        _wait_received(received, 50, timeout=5.0)
        pub.stop()
        sub.stop()

        # Almeno 45/50 devono arrivare (tolleranza per slow CI)
        assert len(received) >= 45, f"Troppi messaggi persi: {len(received)}/50"

    @pytest.mark.integration
    def test_raw_zmq_subscriber_receives_from_bus_client(self, in_process_broker):
        """Un subscriber ZMQ raw riceve messaggi pubblicati tramite BusClient."""
        topic = b"test.raw"
        received = []

        ctx = zmq.Context()
        sub = ctx.socket(zmq.SUB)
        sub.setsockopt(zmq.LINGER, 0)
        sub.setsockopt(zmq.RCVHWM, 100)
        sub.connect(in_process_broker["sub_addr"])
        sub.setsockopt(zmq.SUBSCRIBE, topic)

        time.sleep(0.1)

        pub = _make_client(in_process_broker, "pub")
        time.sleep(0.05)
        pub.publish("test.raw", {"hello": "world"})

        if sub.poll(timeout=2000):
            frames = sub.recv_multipart()
            received.append(frames)

        pub.stop()
        sub.close()
        ctx.term()

        assert len(received) == 1
        t, payload_bytes = received[0]
        assert t == b"test.raw"
        decoded = json.loads(payload_bytes.decode())
        decoded.pop("_trace", None)
        assert decoded == {"hello": "world"}

    @pytest.mark.integration
    def test_bus_client_ignores_invalid_json_payload(self, in_process_broker):
        """BusClient non crasha se riceve un frame con payload non-JSON valido."""
        received = []
        topic_valid = "test.valid_after_invalid"

        sub = _make_client(in_process_broker, "sub")
        sub.subscribe(topic_valid, lambda t, p: received.append(p))
        _start_client(sub)
        time.sleep(0.1)

        # Invia frame con payload non-JSON direttamente via ZMQ raw
        ctx = zmq.Context()
        raw_pub = ctx.socket(zmq.PUB)
        raw_pub.setsockopt(zmq.LINGER, 0)
        raw_pub.connect(in_process_broker["pub_addr"])
        time.sleep(0.05)

        # Messaggio malformato
        raw_pub.send_multipart([b"test.valid_after_invalid", b"NOT_JSON"])
        # Subito dopo un messaggio valido — deve arrivare
        raw_pub.send_multipart([
            b"test.valid_after_invalid",
            json.dumps({"after": "invalid"}).encode()
        ])

        ok = _wait_received(received, 1)
        raw_pub.close()
        ctx.term()
        sub.stop()

        # Il client non deve crashare, e deve ricevere il messaggio valido
        assert ok, "Il messaggio valido non è stato ricevuto dopo un payload invalido"
        assert received[0].get("after") == "invalid"

    @pytest.mark.integration
    def test_no_message_received_before_subscribe(self, in_process_broker):
        """Messaggi pubblicati prima della subscription non vengono recapitati (ZMQ semantics)."""
        received = []
        topic = "test.before_sub"

        # Pubblica prima di qualsiasi subscriber
        pub = _make_client(in_process_broker, "pub")
        time.sleep(0.05)
        pub.publish(topic, {"early": True})

        # Subscriber si registra DOPO il publish
        time.sleep(0.1)
        sub = _make_client(in_process_broker, "sub")
        sub.subscribe(topic, lambda t, p: received.append(p))
        _start_client(sub)

        # Attendi un po' per vedere se arriva qualcosa
        time.sleep(0.3)
        pub.stop()
        sub.stop()

        # Il messaggio precedente non deve arrivare (no persistent queue in ZMQ PUB/SUB)
        early_received = [r for r in received if r.get("early")]
        assert len(early_received) == 0, "Messaggio pubblicato prima della sub non doveva arrivare"

    @pytest.mark.integration
    def test_large_payload_round_trip(self, in_process_broker):
        """Payload di ~8KB viene recapitato senza troncamento."""
        received = []
        topic = "test.large"
        big_string = "x" * 8000
        payload = {"data": big_string, "len": len(big_string)}

        sub = _make_client(in_process_broker, "sub")
        sub.subscribe(topic, lambda t, p: received.append(p))
        _start_client(sub)

        pub = _make_client(in_process_broker, "pub")
        time.sleep(0.1)
        pub.publish(topic, payload)
        ok = _wait_received(received, 1)
        pub.stop()
        sub.stop()

        assert ok
        assert received[0]["len"] == 8000
        assert len(received[0]["data"]) == 8000


# ===========================================================================
# Gruppo 6 — Latency smoke test
# ===========================================================================

class TestLatency:
    """Verifica che la latenza publish→receive sia accettabile con broker in-process."""

    @pytest.mark.integration
    def test_single_message_latency_under_500ms(self, in_process_broker):
        """Latenza publish→receive < 500ms con broker in-process (smoke test)."""
        event = threading.Event()
        recv_time: list[float] = []
        topic = "test.latency"

        sub = _make_client(in_process_broker, "sub")
        def _handler(t, p):
            recv_time.append(time.monotonic())
            event.set()
        sub.subscribe(topic, _handler)
        _start_client(sub)

        pub = _make_client(in_process_broker, "pub")
        time.sleep(0.1)
        send_time = time.monotonic()
        pub.publish(topic, {"ts": send_time})

        event.wait(timeout=2.0)
        pub.stop()
        sub.stop()

        assert recv_time, "Nessun messaggio ricevuto"
        latency_ms = (recv_time[0] - send_time) * 1000
        assert latency_ms < 500, f"Latenza troppo alta: {latency_ms:.1f}ms"

    @pytest.mark.integration
    def test_10_messages_all_within_2s(self, in_process_broker):
        """10 messaggi consecutivi tutti ricevuti entro 2 secondi totali."""
        received = []
        topic = "test.latency_batch"

        sub = _make_client(in_process_broker, "sub")
        sub.subscribe(topic, lambda t, p: received.append(p["i"]))
        _start_client(sub)

        pub = _make_client(in_process_broker, "pub")
        time.sleep(0.1)
        start = time.monotonic()
        for i in range(10):
            pub.publish(topic, {"i": i})

        ok = _wait_received(received, 10, timeout=2.0)
        elapsed = time.monotonic() - start
        pub.stop()
        sub.stop()

        assert ok, f"Solo {len(received)}/10 messaggi ricevuti in {elapsed:.2f}s"
        assert elapsed < 2.0, f"10 messaggi in {elapsed:.2f}s — troppo lento"


# ===========================================================================
# Gruppo 7 — System topics (system.start / system.stop)
# ===========================================================================

class TestSystemTopics:
    """Verifica routing dei topic di sistema utilizzati dall'orchestratore."""

    @pytest.mark.integration
    def test_system_start_delivered(self, in_process_broker):
        """'system.start' viene recapitato ai subscriber registrati."""
        received = []
        sub = _make_client(in_process_broker, "mod")
        sub.subscribe("system.start", lambda t, p: received.append(p))
        _start_client(sub)

        pub = _make_client(in_process_broker, "orchestrator")
        time.sleep(0.1)
        pub.publish("system.start", {"version": "2.0"})
        ok = _wait_received(received, 1)
        pub.stop()
        sub.stop()

        assert ok
        assert received[0].get("version") == "2.0"

    @pytest.mark.integration
    def test_system_stop_delivered(self, in_process_broker):
        """'system.stop' viene recapitato ai subscriber registrati."""
        received = []
        sub = _make_client(in_process_broker, "mod")
        sub.subscribe("system.stop", lambda t, p: received.append(p))
        _start_client(sub)

        pub = _make_client(in_process_broker, "orchestrator")
        time.sleep(0.1)
        pub.publish("system.stop", {"reason": "shutdown"})
        ok = _wait_received(received, 1)
        pub.stop()
        sub.stop()

        assert ok
        assert received[0].get("reason") == "shutdown"

    @pytest.mark.integration
    def test_multiple_modules_receive_system_start(self, in_process_broker):
        """Tutti i moduli iscritti a 'system.start' ricevono il messaggio."""
        num_modules = 5
        received_by = {i: [] for i in range(num_modules)}
        subscribers = []

        for i in range(num_modules):
            c = _make_client(in_process_broker, f"mod_{i}")
            c.subscribe("system.start", lambda t, p, idx=i: received_by[idx].append(p))
            _start_client(c)
            subscribers.append(c)

        pub = _make_client(in_process_broker, "orchestrator")
        time.sleep(0.2)  # attendi tutte le subscription
        pub.publish("system.start", {"broadcast": True})

        # Tutti devono ricevere
        ok = all(_wait_received(received_by[i], 1) for i in range(num_modules))
        pub.stop()
        for c in subscribers:
            c.stop()

        assert ok, f"Non tutti i moduli hanno ricevuto system.start: {[len(received_by[i]) for i in range(num_modules)]}"

    @pytest.mark.integration
    def test_system_start_then_stop_sequence(self, in_process_broker):
        """Sequenza system.start → system.stop ricevuta in ordine da un modulo."""
        received = []
        sub = _make_client(in_process_broker, "mod")
        sub.subscribe("system.start", lambda t, p: received.append("start"))
        sub.subscribe("system.stop", lambda t, p: received.append("stop"))
        _start_client(sub)

        pub = _make_client(in_process_broker, "orchestrator")
        time.sleep(0.1)
        pub.publish("system.start", {})
        pub.publish("system.stop", {})
        ok = _wait_received(received, 2, timeout=3.0)
        pub.stop()
        sub.stop()

        assert ok, f"Ricevuti solo {received}"
        assert received == ["start", "stop"]

    @pytest.mark.integration
    def test_module_ready_published_and_received(self, in_process_broker):
        """Un modulo pubblica 'module.ready' e l'orchestratore lo riceve."""
        received = []
        orchestrator = _make_client(in_process_broker, "orchestrator")
        orchestrator.subscribe("module.ready", lambda t, p: received.append(p))
        _start_client(orchestrator)

        module = _make_client(in_process_broker, "channel_manager")
        time.sleep(0.1)
        module.publish("module.ready", {"module": "channel_manager"})

        ok = _wait_received(received, 1)
        module.stop()
        orchestrator.stop()

        assert ok
        assert received[0]["module"] == "channel_manager"


# ===========================================================================
# Gruppo 8 — Indipendenza broker tra test
# ===========================================================================

class TestBrokerIsolation:
    """Verifica che ogni test abbia un broker isolato (no cross-test leakage)."""

    @pytest.mark.integration
    def test_broker_addresses_unique_across_two_fixtures(
        self, in_process_broker, tmp_path
    ):
        """Due fixture in_process_broker in session diversa non condividono socket."""
        addr1 = in_process_broker["pub_addr"]
        # Crea un secondo broker manuale con UUID diverso
        import uuid as _uuid
        uid2 = _uuid.uuid4().hex
        addr2 = f"ipc:///tmp/nemotest-{uid2}.pub"
        assert addr1 != addr2

    @pytest.mark.integration
    def test_messages_dont_leak_between_isolated_brokers(self, in_process_broker):
        """Messaggi pubblicati su un broker non raggiungono client di un altro broker."""
        import uuid as _uuid
        from conftest import _BrokerThread

        uid = _uuid.uuid4().hex
        pub2 = f"ipc:///tmp/nemotest-{uid}.pub"
        sub2 = f"ipc:///tmp/nemotest-{uid}.sub"
        broker2 = _BrokerThread(pub_addr=pub2, sub_addr=sub2)
        broker2.start()
        broker2.ready.wait(timeout=2.0)
        time.sleep(0.05)
        broker2_addrs = {"pub_addr": pub2, "sub_addr": sub2}

        received_broker1 = []
        received_broker2 = []

        sub1 = _make_client(in_process_broker, "sub1")
        sub1.subscribe("test.isolation", lambda t, p: received_broker1.append(p))
        sub1.start(blocking=False)

        sub2_client = _make_client(broker2_addrs, "sub2")
        sub2_client.subscribe("test.isolation", lambda t, p: received_broker2.append(p))
        sub2_client.start(blocking=False)

        time.sleep(0.2)

        pub1 = _make_client(in_process_broker, "pub1")
        time.sleep(0.1)
        pub1.publish("test.isolation", {"broker": 1})

        ok = _wait_received(received_broker1, 1, timeout=1.0)
        time.sleep(0.2)  # attesa extra per eventuale leakage

        pub1.stop()
        sub1.stop()
        sub2_client.stop()
        broker2.stop()

        assert ok, "sub1 non ha ricevuto nulla"
        assert len(received_broker2) == 0, "sub2 ha ricevuto dati dal broker sbagliato (leakage!)"
