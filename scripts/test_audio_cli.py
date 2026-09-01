#!/usr/bin/env python3
"""
test_audio_cli.py — Standalone CLI Audio Diagnostics Tool for NemoHeadUnit.

Modes:
  1. system / gst: Play a tone using system audio pipelines (GStreamer / pw-play / paplay / aplay).
  2. qaudiosink: Play a synthetic tone using a standalone PyQt6 QAudioSink process (both Push and Pull mode).
  3. list: List all audio devices detected by Qt6 and system.

Usage:
  python scripts/test_audio_cli.py --mode gst
  python scripts/test_audio_cli.py --mode qaudiosink [--push] [--device "Audio interno Speakers"] [--freq 440] [--duration 2.0]
  python scripts/test_audio_cli.py --mode list
"""

import argparse
import math
import os
import struct
import subprocess
import sys
import time


def generate_sine_pcm(freq_hz: float = 440.0, duration_sec: float = 2.0, sample_rate: int = 48000, channels: int = 2, amplitude: float = 0.8) -> bytes:
    """Generate 16-bit signed PCM sine wave."""
    total_samples = int(sample_rate * duration_sec)
    max_amp = int(32767 * max(0.0, min(1.0, amplitude)))
    buf = bytearray()
    for i in range(total_samples):
        t = float(i) / float(sample_rate)
        val = int(max_amp * math.sin(2.0 * math.pi * freq_hz * t))
        val = max(-32768, min(32767, val))
        sample = struct.pack("<h", val)
        for _ in range(channels):
            buf.extend(sample)
    return bytes(buf)


def test_system_player(freq: float, duration: float):
    """Play tone using available system utilities (GStreamer, pw-play, paplay, aplay)."""
    print(f"\n[System Player Test] Generating {freq}Hz tone for {duration}s...")

    # 1. Try GStreamer (installed with full plugins on target)
    num_buffers = int(duration * 50)  # audiotestsrc default is 100 samples per buffer or ~20ms
    gst_cmd = [
        "gst-launch-1.0",
        "-q",
        "audiotestsrc", f"freq={int(freq)}", f"num-buffers={num_buffers}",
        "!", "audioconvert",
        "!", "audioresample",
        "!", "autoaudiosink"
    ]
    print(f"Executing: {' '.join(gst_cmd)}")
    try:
        res = subprocess.run(gst_cmd, timeout=duration + 3.0)
        if res.returncode == 0:
            print("✔ GStreamer autoaudiosink played successfully.")
            return True
        else:
            print(f"GStreamer returned exit code {res.returncode}")
    except FileNotFoundError:
        print("gst-launch-1.0 not found in PATH.")
    except Exception as e:
        print(f"GStreamer error: {e}")

    # 2. Try paplay / pw-play with temporary WAV
    import tempfile
    import wave

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tf:
        wav_path = tf.name

    try:
        pcm = generate_sine_pcm(freq, duration, 48000, 2, 0.8)
        with wave.open(wav_path, "wb") as wf:
            wf.setnchannels(2)
            wf.setsampwidth(2)
            wf.setframerate(48000)
            wf.writeframes(pcm)

        for player in ["pw-play", "paplay", "aplay"]:
            try:
                print(f"Trying {player} {wav_path}...")
                res = subprocess.run([player, wav_path], timeout=duration + 3.0)
                if res.returncode == 0:
                    print(f"✔ {player} played successfully.")
                    return True
            except FileNotFoundError:
                continue
            except Exception as e:
                print(f"{player} failed: {e}")
    finally:
        if os.path.exists(wav_path):
            os.remove(wav_path)

    print("❌ No system player succeeded.")
    return False


def test_qaudiosink(device_name: str, freq: float, duration: float, push_mode: bool):
    """Play tone using standalone PyQt6 QAudioSink."""
    print(f"\n[PyQt6 QAudioSink Test] Initializing (mode={'PUSH' if push_mode else 'PULL'})...")
    
    from PyQt6.QtCore import QCoreApplication, QIODevice, QTimer
    from PyQt6.QtMultimedia import QAudioFormat, QAudioSink, QMediaDevices, QAudioDevice, QAudio

    app = QCoreApplication.instance()
    created_app = False
    if app is None:
        app = QCoreApplication(sys.argv)
        created_app = True

    devices = QMediaDevices.audioOutputs()
    print(f"Detected {len(devices)} Qt6 audio output devices:")
    target_dev = QMediaDevices.defaultAudioOutput()
    for idx, d in enumerate(devices):
        desc = d.description()
        is_default = (d.id() == target_dev.id())
        default_tag = " [DEFAULT]" if is_default else ""
        print(f"  [{idx}] '{desc}' (id={d.id().data().decode('utf-8', errors='ignore')}){default_tag}")
        if device_name and (device_name.lower() in desc.lower()):
            target_dev = d

    print(f"\nSelected device: '{target_dev.description()}' (isNull={target_dev.isNull()})")

    fmt = QAudioFormat()
    fmt.setSampleRate(48000)
    fmt.setChannelCount(2)
    fmt.setSampleFormat(QAudioFormat.SampleFormat.Int16)

    if not target_dev.isFormatSupported(fmt):
        print("⚠️ Warning: Target device reports 48kHz Stereo Int16 is not preferred/supported format!")
        pref = target_dev.preferredFormat()
        print(f"   Preferred format: {pref.sampleRate()}Hz, {pref.channelCount()}ch, format={pref.sampleFormat()}")
    else:
        print("✔ Format 48000Hz 2ch Int16 is supported by device.")

    sink = QAudioSink(target_dev, fmt)
    sink.setVolume(1.0)
    print(f"QAudioSink created. Buffer size: {sink.bufferSize()} bytes. Volume: {sink.volume()}")

    def on_state_changed(state):
        print(f"  [QAudioSink State Changed] -> {state} (error={sink.error()})")
        if state == QAudio.State.IdleState and not push_mode:
            print("  Sink is Idle (finished or underrun).")

    sink.stateChanged.connect(on_state_changed)

    pcm_data = generate_sine_pcm(freq, duration, 48000, 2, 0.8)
    print(f"Generated PCM payload: {len(pcm_data)} bytes ({len(pcm_data) / (48000 * 4):.2f}s)")

    if push_mode:
        # Push mode: write chunks to QIODevice returned by sink.start()
        io_dev = sink.start()
        if not io_dev:
            print("❌ sink.start() returned None in Push Mode!")
            return False

        written_total = [0]
        def push_chunks():
            if written_total[0] < len(pcm_data):
                chunk = pcm_data[written_total[0] : written_total[0] + 4096]
                w = io_dev.write(chunk)
                if w > 0:
                    written_total[0] += w

        push_timer = QTimer()
        push_timer.timeout.connect(push_chunks)
        push_timer.start(20)  # write 4096 bytes every 20ms (~DAC rate)
        push_chunks()
        print("Push mode: Started pushing PCM chunks via QTimer.")
    else:
        # Pull mode: custom QIODevice subclass feeding samples
        class PullStream(QIODevice):
            def __init__(self, data: bytes):
                super().__init__()
                self._data = bytearray(data)
                self._offset = 0

            def isSequential(self) -> bool:
                return True

            def bytesAvailable(self) -> int:
                return max(0, len(self._data) - self._offset) + super().bytesAvailable()

            def readData(self, max_len: int) -> bytes:
                if max_len <= 0 or self._offset >= len(self._data):
                    return b""
                take = min(len(self._data) - self._offset, max_len)
                take = (take // 4) * 4
                if take == 0:
                    return b""
                chunk = bytes(self._data[self._offset : self._offset + take])
                self._offset += len(chunk)
                return chunk

            def writeData(self, data: bytes) -> int:
                return -1

        pull_stream = PullStream(pcm_data)
        pull_stream.open(QIODevice.OpenModeFlag.ReadOnly)
        sink.start(pull_stream)
        print("Pull mode: Started QAudioSink with custom PullStream.")

    if created_app:
        # Timer to quit app after duration + 1 second
        QTimer.singleShot(int((duration + 1.0) * 1000), app.quit)
        app.exec()
        sink.stop()
        print("✔ QAudioSink test completed.")
    else:
        # Non-blocking async timer for existing Qt event loop; keep objects alive
        test_qaudiosink._active_sink = sink
        test_qaudiosink._active_stream = pull_stream if not push_mode else None

        def _cleanup():
            try:
                sink.stop()
            except Exception:
                pass
            test_qaudiosink._active_sink = None
            test_qaudiosink._active_stream = None
            print("✔ In-process QAudioSink test completed.")

        QTimer.singleShot(int((duration + 0.5) * 1000), _cleanup)

    return True


def list_devices():
    """List system and Qt devices."""
    print("=== Qt6 Audio Outputs ===")
    from PyQt6.QtCore import QCoreApplication
    from PyQt6.QtMultimedia import QMediaDevices
    app = QCoreApplication(sys.argv)
    default_dev = QMediaDevices.defaultAudioOutput()
    for idx, d in enumerate(QMediaDevices.audioOutputs()):
        is_def = (d.id() == default_dev.id())
        print(f"[{idx}] {d.description()}{' [DEFAULT]' if is_def else ''}")
    
    print("\n=== ALSA / PipeWire Devices (pw-cli / aplay) ===")
    for cmd in [["pw-cli", "list-objects", "Node"], ["aplay", "-l"]]:
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=2.0)
            if res.returncode == 0:
                print(f"Output of {' '.join(cmd)}:\n{res.stdout[:500]}...")
                break
        except Exception:
            pass


def main():
    parser = argparse.ArgumentParser(description="Standalone CLI Audio Diagnostics Tool")
    parser.add_argument("--mode", choices=["gst", "system", "qaudiosink", "list"], default="qaudiosink", help="Test mode to execute")
    parser.add_argument("--freq", type=float, default=440.0, help="Tone frequency in Hz (default: 440)")
    parser.add_argument("--duration", type=float, default=2.0, help="Duration in seconds (default: 2.0)")
    parser.add_argument("--device", type=str, default="", help="Target audio output device name substring")
    parser.add_argument("--push", action="store_true", help="Use QAudioSink Push-Mode instead of Pull-Mode")
    args = parser.parse_args()

    if args.mode == "list":
        list_devices()
    elif args.mode in ("gst", "system"):
        test_system_player(args.freq, args.duration)
    elif args.mode == "qaudiosink":
        test_qaudiosink(args.device, args.freq, args.duration, args.push)


if __name__ == "__main__":
    main()
