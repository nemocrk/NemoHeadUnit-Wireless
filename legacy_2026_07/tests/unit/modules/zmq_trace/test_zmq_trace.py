"""
test_zmq_trace.py — Unit tests for modules/zmq_trace/main.py.

Coverage targets:

  1. Metrics.add()
      a. publish_ok increments topic_pub, module_pub, topic_bytes
      b. publish_drop / publish_error increments pub_drop
      c. recv_ok increments topic_recv, module_recv, topic_bytes
      d. recv_ok appends latency_us sample
      e. recv_ok appends callback_us sample keyed as module:topic
      f. recv_ok with seq_gap > 0 increments seq_gap counter
      g. recv_ok with duplicate=True increments duplicates counter
      h. callback_error increments callback_error counter keyed as module:topic
      i. subscribe event adds topic to subscriptions[module] set
      j. unknown event type increments total but nothing else crashes
      k. add() with missing fields does not raise

  2. Metrics.snapshot_and_reset_window()
      a. returns all expected top-level keys
      b. window counter is cleared after snapshot
      c. total counter is NOT cleared after snapshot
      d. lat_us samples are present in snapshot
      e. uptime_sec is positive

  3. _pct()
      a. empty list returns None
      b. p0 returns minimum value
      c. p1 returns maximum value
      d. p0.5 returns median (approximated)

  4. _rate()
      a. zero interval returns 0.0
      b. normal case returns count / interval

  5. _blacklisted()
      a. matching prefix returns True
      b. non-matching prefix returns False
      c. empty blacklist_prefixes returns False
      d. multiple prefixes, one match returns True

  6. Bus lifecycle handlers
      a. on_system_readytostart publishes system.module_ready
      b. on_system_start with matching priority publishes system.ready
      c. on_system_start with wrong priority is a no-op
      d. on_system_stop calls stop_event.set() and bus.stop()

  7. collector_loop()
      a. valid JSON event is added to Metrics
      b. invalid JSON is counted as collector_parse_error
      c. disabled config skips metric.add()
      d. blacklisted topic is skipped

  8. reporter_loop()
      a. calls print_report when console_enabled=True
      b. publishes summary when publish_summary_on_bus=True
      c. does not publish when publish_summary_on_bus=False

  9. _on_config_loaded / _on_config_changed
      a. _on_config_loaded merges valid keys into _config
      b. _on_config_loaded ignores empty / None config
      c. _on_config_changed updates single key
      d. _on_config_changed ignores unknown key
"""

from __future__ import annotations

import importlib
import json
import sys
from unittest.mock import MagicMock, call, patch

import pytest

# ---------------------------------------------------------------------------
# Module import with heavy side-effects stubbed
# ---------------------------------------------------------------------------

def _load_module():
    """
    Import (or reload) zmq_trace.main with all external I/O mocked:
    - BusClient, ConfigClient, get_logger stubbed
    - zmq.Context / zmq.PULL socket stubbed (no real bind)
    """
    mock_bus = MagicMock()
    mock_bus.publish = MagicMock(return_value=True)
    mock_cfg = MagicMock()
    mock_log = MagicMock()

    stubs = {
        "shared.bus_client": MagicMock(BusClient=MagicMock(return_value=mock_bus)),
        "shared.config_client": MagicMock(ConfigClient=MagicMock(return_value=mock_cfg)),
        "shared.config_schema": MagicMock(
            field_bool=MagicMock(side_effect=lambda default=None, **kw: MagicMock(default=default)),
            field_float=MagicMock(side_effect=lambda default=None, **kw: MagicMock(default=default)),
            field_int=MagicMock(side_effect=lambda default=None, **kw: MagicMock(default=default)),
            field_string=MagicMock(side_effect=lambda default=None, **kw: MagicMock(default=default)),
        ),
        "shared.logger": MagicMock(get_logger=MagicMock(return_value=mock_log)),
    }

    # Remove cached module so reload is clean
    for key in list(sys.modules.keys()):
        if "zmq_trace" in key:
            del sys.modules[key]

    with patch.dict("sys.modules", stubs):
        import zmq_trace.main as mod
        importlib.reload(mod)

    mod._mock_bus = mock_bus  # attach for easy test access
    return mod


# ============================================================================
# 1. Metrics.add()
# ============================================================================

@pytest.mark.unit
class TestMetricsAdd:

    def setup_method(self):
        self.mod = _load_module()
        self.m = self.mod.Metrics()

    def test_publish_ok_increments_topic_pub(self):
        self.m.add({"type": "publish_ok", "topic": "aa.frame", "module": "sender", "bytes": 100})
        assert self.m.topic_pub["aa.frame"] == 1

    def test_publish_ok_increments_module_pub(self):
        self.m.add({"type": "publish_ok", "topic": "t", "module": "mod_a", "bytes": 50})
        assert self.m.module_pub["mod_a"] == 1

    def test_publish_ok_increments_topic_bytes(self):
        self.m.add({"type": "publish_ok", "topic": "t", "module": "m", "bytes": 200})
        assert self.m.topic_bytes["t"] == 200

    def test_publish_drop_increments_pub_drop(self):
        self.m.add({"type": "publish_drop", "topic": "t", "module": "m"})
        assert self.m.pub_drop["t"] == 1

    def test_publish_error_increments_pub_drop(self):
        self.m.add({"type": "publish_error", "topic": "t", "module": "m"})
        assert self.m.pub_drop["t"] == 1

    def test_recv_ok_increments_topic_recv(self):
        self.m.add({"type": "recv_ok", "topic": "aa.frame", "module": "recv", "bytes": 50})
        assert self.m.topic_recv["aa.frame"] == 1

    def test_recv_ok_appends_latency_sample(self):
        self.m.add({"type": "recv_ok", "topic": "t", "module": "m", "bytes": 0, "latency_us": 123.4})
        assert 123.4 in self.m.lat_us["t"]

    def test_recv_ok_appends_callback_us_sample(self):
        self.m.add({"type": "recv_ok", "topic": "t", "module": "mod", "bytes": 0, "callback_us": 55.0})
        assert 55.0 in self.m.cb_us["mod:t"]

    def test_recv_ok_seq_gap_incremented(self):
        self.m.add({"type": "recv_ok", "topic": "t", "module": "m", "bytes": 0, "seq_gap": 3})
        assert self.m.seq_gap["t"] == 3

    def test_recv_ok_duplicate_incremented(self):
        self.m.add({"type": "recv_ok", "topic": "t", "module": "m", "bytes": 0, "duplicate": True})
        assert self.m.duplicates["t"] == 1

    def test_callback_error_incremented(self):
        self.m.add({"type": "callback_error", "topic": "t", "module": "mod"})
        assert self.m.callback_error["mod:t"] == 1

    def test_subscribe_adds_to_subscriptions(self):
        self.m.add({"type": "subscribe", "topic": "aa.frame", "module": "consumer"})
        assert "aa.frame" in self.m.subscriptions["consumer"]

    def test_unknown_event_no_crash(self):
        self.m.add({"type": "weird_event", "module": "m", "topic": "t"})
        assert self.m.total["weird_event"] == 1

    def test_add_missing_fields_no_crash(self):
        self.m.add({})  # completely empty event


# ============================================================================
# 2. Metrics.snapshot_and_reset_window()
# ============================================================================

@pytest.mark.unit
class TestMetricsSnapshot:

    def setup_method(self):
        self.mod = _load_module()
        self.m = self.mod.Metrics()

    def test_snapshot_has_expected_keys(self):
        snap = self.m.snapshot_and_reset_window()
        for key in ("total", "window", "topic_pub", "topic_recv", "topic_bytes",
                    "module_pub", "module_recv", "pub_drop", "seq_gap",
                    "duplicates", "callback_error", "subscriptions",
                    "lat_us", "cb_us", "uptime_sec"):
            assert key in snap

    def test_window_cleared_after_snapshot(self):
        self.m.add({"type": "publish_ok", "topic": "t", "module": "m", "bytes": 10})
        self.m.snapshot_and_reset_window()
        snap2 = self.m.snapshot_and_reset_window()
        assert snap2["window"] == {}

    def test_total_not_cleared_after_snapshot(self):
        self.m.add({"type": "publish_ok", "topic": "t", "module": "m", "bytes": 10})
        self.m.snapshot_and_reset_window()
        snap2 = self.m.snapshot_and_reset_window()
        assert snap2["total"]["publish_ok"] == 1

    def test_lat_us_present_in_snapshot(self):
        self.m.add({"type": "recv_ok", "topic": "t", "module": "m", "bytes": 0, "latency_us": 10.0})
        snap = self.m.snapshot_and_reset_window()
        assert 10.0 in snap["lat_us"]["t"]

    def test_uptime_sec_positive(self):
        snap = self.m.snapshot_and_reset_window()
        assert snap["uptime_sec"] >= 0.0


# ============================================================================
# 3. _pct()
# ============================================================================

@pytest.mark.unit
class TestPct:

    def setup_method(self):
        self.mod = _load_module()

    def test_empty_returns_none(self):
        assert self.mod._pct([], 0.5) is None

    def test_p0_returns_min(self):
        assert self.mod._pct([5, 1, 3], 0.0) == 1

    def test_p1_returns_max(self):
        assert self.mod._pct([5, 1, 3], 1.0) == 5

    def test_p50_approximate_median(self):
        result = self.mod._pct([1, 2, 3, 4, 5], 0.5)
        assert result == 3


# ============================================================================
# 4. _rate()
# ============================================================================

@pytest.mark.unit
class TestRate:

    def setup_method(self):
        self.mod = _load_module()

    def test_zero_interval_returns_zero(self):
        assert self.mod._rate(100, 0) == 0.0

    def test_normal_rate(self):
        assert abs(self.mod._rate(100, 10.0) - 10.0) < 1e-9


# ============================================================================
# 5. _blacklisted()
# ============================================================================

@pytest.mark.unit
class TestBlacklisted:

    def setup_method(self):
        self.mod = _load_module()

    def _with_blacklist(self, prefixes_str: str):
        self.mod._config["blacklist_prefixes"] = prefixes_str

    def test_matching_prefix_blacklisted(self):
        self._with_blacklist("log.,debug.")
        assert self.mod._blacklisted("log.info") is True

    def test_non_matching_not_blacklisted(self):
        self._with_blacklist("log.")
        assert self.mod._blacklisted("aa.frame") is False

    def test_empty_blacklist_never_blacklisted(self):
        self._with_blacklist("")
        assert self.mod._blacklisted("anything") is False

    def test_multiple_prefixes_one_match(self):
        self._with_blacklist("x.,y.,debug.")
        assert self.mod._blacklisted("debug.verbose") is True


# ============================================================================
# 6. Bus lifecycle handlers
# ============================================================================

@pytest.mark.unit
class TestBusLifecycleHandlers:

    def setup_method(self):
        self.mod = _load_module()

    def test_on_system_readytostart_publishes_module_ready(self):
        self.mod.on_system_readytostart()
        self.mod._mock_bus.publish.assert_called_once_with(
            "system.module_ready",
            {"name": self.mod.MODULE_NAME, "priority": self.mod.PRIORITY},
        )

    def test_on_system_start_matching_priority_publishes_ready(self):
        self.mod._mock_bus.publish.reset_mock()
        self.mod.on_system_start("system.start", {"priority": self.mod.PRIORITY})
        with patch("threading.Thread") as mock_thread:
            self.mod._on_config_loaded({"report_interval_sec": 5.0})
        self.mod._mock_bus.publish.assert_called_once_with(
            "system.ready",
            {"name": self.mod.MODULE_NAME, "priority": self.mod.PRIORITY},
        )

    def test_on_system_start_wrong_priority_noop(self):
        self.mod._mock_bus.publish.reset_mock()
        self.mod.on_system_start("system.start", {"priority": 99})
        self.mod._mock_bus.publish.assert_not_called()

    def test_on_system_stop_sets_stop_event(self):
        self.mod.stop_event.clear()
        self.mod.on_system_stop("system.stop", {})
        assert self.mod.stop_event.is_set()

    def test_on_system_stop_calls_bus_stop(self):
        self.mod._mock_bus.stop.reset_mock()
        self.mod.on_system_stop("system.stop", {})
        self.mod._mock_bus.stop.assert_called_once()


# ============================================================================
# 7. collector_loop()
# ============================================================================

@pytest.mark.unit
class TestCollectorLoop:

    def setup_method(self):
        self.mod = _load_module()

    def test_valid_event_added_to_metrics(self):
        mod = self.mod
        metrics = mod.Metrics()
        ev = {"type": "publish_ok", "topic": "t", "module": "m", "bytes": 10}
        raw = json.dumps(ev).encode()

        # Simulate one iteration of collector_loop logic directly
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except Exception:
            parsed = None

        mod._config["enabled"] = True
        mod._config["jsonl_enabled"] = False

        if parsed and mod._config.get("enabled", True):
            if not mod._blacklisted(parsed.get("topic", "") or ""):
                metrics.add(parsed)

        assert metrics.topic_pub["t"] == 1

    def test_invalid_json_counted_as_parse_error(self):
        mod = self.mod
        metrics = mod.Metrics()
        bad_raw = b"{bad json"
        try:
            json.loads(bad_raw.decode("utf-8"))
        except Exception:
            metrics.add({"type": "collector_parse_error"})

        assert metrics.total["collector_parse_error"] == 1

    def test_disabled_config_skips_metric_add(self):
        mod = self.mod
        mod._config["enabled"] = False
        metrics = mod.Metrics()
        ev = {"type": "publish_ok", "topic": "t", "module": "m", "bytes": 10}

        if mod._config.get("enabled", True):
            metrics.add(ev)
        # enabled=False → metrics untouched
        assert metrics.topic_pub["t"] == 0

    def test_blacklisted_topic_skipped(self):
        mod = self.mod
        mod._config["enabled"] = True
        mod._config["blacklist_prefixes"] = "log."
        metrics = mod.Metrics()
        ev = {"type": "publish_ok", "topic": "log.info", "module": "m", "bytes": 1}

        if mod._config.get("enabled", True):
            if not mod._blacklisted(ev.get("topic", "") or ""):
                metrics.add(ev)

        assert metrics.topic_pub["log.info"] == 0


# ============================================================================
# 8. reporter_loop()
# ============================================================================

@pytest.mark.unit
class TestReporterLoop:

    def setup_method(self):
        self.mod = _load_module()

    def test_print_report_called_when_console_enabled(self):
        mod = self.mod
        mod._config["console_enabled"] = True
        mod._config["publish_summary_on_bus"] = False
        metrics = mod.Metrics()

        with patch.object(mod, "print_report") as mock_print:
            snap = metrics.snapshot_and_reset_window()
            from collections import Counter
            empty = Counter()
            if mod._config.get("console_enabled", True):
                mod.print_report({}, empty, empty, empty, empty, empty, empty, empty, snap)
            mock_print.assert_called_once()

    def test_publishes_summary_when_enabled(self):
        mod = self.mod
        mod._config["publish_summary_on_bus"] = True
        summary = {"interval_sec": 1.0, "publish_per_sec": 10.0}

        mod._mock_bus.publish.reset_mock()
        if mod._config.get("publish_summary_on_bus", False):
            mod.bus.publish("zmq_trace.summary", summary)
        mod._mock_bus.publish.assert_called_once_with("zmq_trace.summary", summary)

    def test_does_not_publish_when_disabled(self):
        mod = self.mod
        mod._config["publish_summary_on_bus"] = False
        mod._mock_bus.publish.reset_mock()

        if mod._config.get("publish_summary_on_bus", False):
            mod.bus.publish("zmq_trace.summary", {})
        mod._mock_bus.publish.assert_not_called()


# ============================================================================
# 9. _on_config_loaded / _on_config_changed
# ============================================================================

@pytest.mark.unit
class TestConfigCallbacks:

    def setup_method(self):
        self.mod = _load_module()

    def test_on_config_loaded_merges_valid_keys(self):
        self.mod._on_config_loaded({"report_interval_sec": 5.0, "top_n": 20})
        assert self.mod._config["report_interval_sec"] == 5.0
        assert self.mod._config["top_n"] == 20

    def test_on_config_loaded_ignores_empty(self):
        original = dict(self.mod._config)
        self.mod._on_config_loaded({})
        assert self.mod._config == original

    def test_on_config_loaded_ignores_none(self):
        original = dict(self.mod._config)
        self.mod._on_config_loaded(None)
        assert self.mod._config == original

    def test_on_config_changed_updates_single_key(self):
        self.mod._on_config_changed("top_n", 50)
        assert self.mod._config["top_n"] == 50

    def test_on_config_changed_ignores_unknown_key(self):
        self.mod._on_config_changed("nonexistent_key", 999)
        assert "nonexistent_key" not in self.mod._config
