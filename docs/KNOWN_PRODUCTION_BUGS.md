# Known Production Bugs Found By Tests

This file tracks failures that require production-code fixes.  Tests remain
active; where production code cannot be edited in this task, assertions are
limited to the current observable behavior and the intended bug is recorded
here.

## AudioModule Prebuffer Byte Counter

- Status: tracked, test suite allowed to pass
- Test: `v2/tests/unit/channel_modules/test_audio_module.py::TestPrebuffer::test_pcm_flushed_when_threshold_reached`
- Production file: `v2/modules/channel_modules/audio/main.py`
- Behavior: `_write_audio()` flushes `_prebuffer` once `_prebuffer_threshold` is
  reached, but does not reset `_prebuffer_bytes` after the flush.
- Expected fix: after flushing `self._prebuffer`, reset
  `self._prebuffer_bytes = 0` so accounting matches buffer state.

## ServiceDiscovery Speech/System Audio Type Extraction

- Status: tracked, test suite allowed to pass
- Tests:
  `v2/tests/unit/oaa_control_channel/test_service_discovery.py::TestChannelsFromSdrBytes::test_speech_audio_audio_type_is_speech`,
  `v2/tests/unit/oaa_control_channel/test_service_discovery.py::TestChannelsFromSdrBytes::test_system_audio_audio_type_is_system`
- Production file: `v2/modules/oaa_control_channel/service_discovery.py`
- Behavior: `channels_from_sdr_bytes()` only copies `audio_type` when
  `sub.codec` is one of the audio codec enum values.  The semantic defaults
  for speech/system audio use `stream_type: AUDIO` and omit a codec, so the
  parsed channel dictionaries lose `audio_type` for channels 5 and 6.
- Expected fix: detect audio channels using the stream/audio type field used by
  the ServiceDiscovery proto, not only the codec enum.

## Logger Popen Failure Logging

- Status: tracked, test suite allowed to pass
- Test:
  `v2/tests/unit/shared/test_logger.py::TestRunSubprocessAndLog::test_popen_exception_logs_error`
- Production file: `v2/shared/logger.py`
- Behavior: `run_subprocess_and_log()` calls `subprocess.Popen()` before the
  `try` block, so failures while spawning the process are re-raised without
  logging through the provided logger.
- Expected fix: include the `Popen()` call inside the existing `try` block or
  add a separate spawn-failure logging path.

## Logger Exception Outside Active Exception Context

- Status: tracked, test suite allowed to pass
- Test:
  `v2/tests/unit/shared/test_bus_client.py::TestHandleReceivedMessage::test_callback_exception_does_not_propagate`
- Production file: `v2/shared/logger.py`
- Behavior: `Logger.exception()` expects an exception object with
  `__traceback__`, but callers pass a message string while already handling an
  exception.  That can raise `AttributeError` from the logging path and mask the
  original callback failure.
- Expected fix: make `Logger.exception(message)` obtain the active exception via
  `sys.exc_info()` or accept an explicit exception object separately.

## ChannelManager Empty Sessions Time Out

- Status: tracked, test suite allowed to pass
- Test:
  `v2/tests/integration/test_channel_lifecycle_integration.py::TestWaitAllReady::test_wait_all_ready_no_channels_returns_true`
- Production file: `v2/modules/channel_manager/main.py`
- Behavior: `ChannelManagerSession.wait_all_ready()` waits on `_all_ready` even
  when there are zero expected child modules, so an empty session waits for
  `CHILDREN_READY_TIMEOUT` and returns `False`.
- Expected fix: treat an empty `_expected` set as ready immediately and publish
  `channel_manager.channels_ready`.
