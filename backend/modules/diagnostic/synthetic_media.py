"""
synthetic_media.py — Synthetic audio and video generators and analyzers for diagnostic probing.
"""

import math
import struct
from typing import Tuple


def generate_pcm_tone(
    freq_hz: float = 440.0,
    duration_ms: int = 1000,
    sample_rate: int = 48000,
    channels: int = 2,
    amplitude: float = 0.5,
) -> bytes:
    """Generate 16-bit signed little-endian PCM sine wave."""
    total_samples = int(sample_rate * (duration_ms / 1000.0))
    max_amp = int(32767 * max(0.0, min(1.0, amplitude)))
    buffer = bytearray()

    for i in range(total_samples):
        t = float(i) / float(sample_rate)
        val = int(max_amp * math.sin(2.0 * math.pi * freq_hz * t))
        val = max(-32768, min(32767, val))
        sample_bytes = struct.pack("<h", val)
        for _ in range(channels):
            buffer.extend(sample_bytes)

    return bytes(buffer)


def calculate_audio_rms(pcm_bytes: bytes, channels: int = 2) -> Tuple[float, float]:
    """
    Calculate RMS and Peak dB levels for 16-bit signed PCM audio.
    Returns (rms_db, peak_db) relative to full scale (0 dBFS).
    """
    if not pcm_bytes or len(pcm_bytes) < 2:
        return -96.0, -96.0

    sample_count = len(pcm_bytes) // 2
    sum_squares = 0.0
    peak_sample = 0

    for i in range(0, len(pcm_bytes) - 1, 2):
        sample = struct.unpack_from("<h", pcm_bytes, i)[0]
        abs_sample = abs(sample)
        if abs_sample > peak_sample:
            peak_sample = abs_sample
        sum_squares += float(sample * sample)

    mean_square = sum_squares / max(1, sample_count)
    rms = math.sqrt(mean_square)

    # 32768 is full scale
    rms_norm = max(1e-5, rms / 32768.0)
    peak_norm = max(1e-5, peak_sample / 32768.0)

    rms_db = 20.0 * math.log10(rms_norm)
    peak_db = 20.0 * math.log10(peak_norm)

    return round(rms_db, 1), round(peak_db, 1)


def generate_reference_adts_aac(sample_rate: int = 48000, channels: int = 2) -> bytes:
    """
    Generate a minimal valid ADTS AAC frame header + synthetic silence payload.
    Used for AAC stream pipeline parsing validation.
    """
    # ADTS header (7 bytes)
    # Syncword 0xFFF, MPEG-4, Layer 0, No CRC, AAC LC (profile 1 => 01b), 48kHz (index 3 => 0011b), 2 channels (index 2 => 0010b)
    # Payload length = 7 + payload_size
    payload = b"\x21\x10\x04\x60\x8c\x1c"  # Minimal silence spectral data
    frame_len = 7 + len(payload)

    freq_indices = {96000: 0, 88200: 1, 64000: 2, 48000: 3, 44100: 4, 32000: 5, 24000: 6, 22050: 7, 16000: 8}
    freq_idx = freq_indices.get(sample_rate, 3)

    header = bytearray(7)
    header[0] = 0xFF
    header[1] = 0xF1  # MPEG-4, no CRC
    header[2] = ((1 << 6) | (freq_idx << 2) | ((channels >> 2) & 1)) & 0xFF
    header[3] = (((channels & 3) << 6) | ((frame_len >> 11) & 0x03)) & 0xFF
    header[4] = ((frame_len >> 3) & 0xFF)
    header[5] = (((frame_len & 0x07) << 5) | 0x1F) & 0xFF
    header[6] = 0xFC

    return bytes(header) + payload


def generate_synthetic_h264_frame(width: int = 1280, height: int = 720, is_idr: bool = True) -> bytes:
    """
    Generate synthetic H.264 baseline Annex-B NAL unit.
    For IDR: Prepends SPS (NAL 7), PPS (NAL 8), and IDR Slice (NAL 5).
    For non-IDR: Non-IDR Slice (NAL 1).
    """
    start_code = b"\x00\x00\x00\x01"

    if is_idr:
        # Minimal Baseline SPS: Profile 66 (0x42), Level 3.1 (0x1F)
        sps_nal = start_code + b"\x67\x42\x00\x1f\x96\x54\x05\x01\x7f\xc0\x4c\x00\x00\x03\x00\x04\x00\x00\x03\x00\x78\x00"
        # Minimal PPS
        pps_nal = start_code + b"\x68\xce\x3c\x80"
        # Minimal IDR Slice payload
        idr_nal = start_code + b"\x65\x88\x84\x00\x10\x00\x00\x03\x00\x00\x03\x00\x00\xa0\x00"
        return sps_nal + pps_nal + idr_nal
    else:
        # Minimal non-IDR slice
        slice_nal = start_code + b"\x41\x9a\x00\x00\x03\x00\x00\x03\x00\x00\x80"
        return slice_nal
