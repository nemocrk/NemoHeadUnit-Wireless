# Known Production Bugs Found By Tests

This file tracks failures that require production-code fixes.  Tests remain
active; where production code cannot be edited in this task, assertions are
limited to the current observable behavior and the intended bug is recorded
here.

> **2026-05-15 — ALL 5 BUGS FIXED. File kept for historical reference.**

---

## ✅ AudioModule Prebuffer Byte Counter

- Status: **FIXED** — commit `dea274a`
- Production file: `v2/modules/channel_modules/audio/main.py`
- Fix: added `self._prebuffer_bytes = 0` after flushing `self._prebuffer` in `_write_audio()`.

## ✅ ServiceDiscovery Speech/System Audio Type Extraction

- Status: **FIXED** — commit `987ffb3`
- Production file: `v2/modules/oaa_control_channel/service_discovery.py`
- Fix: `channels_from_sdr_bytes()` now sets `audio_type` for av_channels where
  `stream_type == AVStreamType.AUDIO` (covers ch 5/6 — SpeechAudio / SystemAudio
  which omit a codec). Extracted `_AUDIO_CODEC_VALUES` frozenset for clarity.

## ✅ Logger Popen Failure Logging

- Status: **FIXED** — `run_subprocess_and_log()` in `v2/shared/logger.py`
  already wraps `subprocess.Popen()` in a `try/except` that logs and re-raises.
  Verified on 2026-05-15: no code change needed.

## ✅ Logger Exception Outside Active Exception Context

- Status: **FIXED** — commit `1f4f227`
- Production file: `v2/shared/logger.py`
- Fix: `Logger.exception()` now uses `sys.exc_info()` to obtain the active
  exception context; accepts an explicit `exc_info` override parameter.

## ✅ ChannelManager Empty Sessions Time Out

- Status: **FIXED** — commit `fb5d2a3`
- Production file: `v2/modules/channel_manager/main.py`
- Fix: `ChannelManagerSession.wait_all_ready()` now treats an empty `_expected`
  set as immediately ready and publishes `channel_manager.channels_ready`.
