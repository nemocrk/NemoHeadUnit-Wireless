import sys
sys.path.insert(0, "/home/nemo/NemoHeadUnit-Wireless/protos")
sys.path.insert(0, "/home/nemo/NemoHeadUnit-Wireless")
sys.path.insert(0, "/home/nemo/NemoHeadUnit-Wireless/backend")

from shared.nal_utils import is_keyframe, is_header_nal, get_nal_type, get_nal_type_hevc, pack_media_frame, unpack_media_frame
from shared.proto_utils import get_codec_descriptor
from modules.channel_manager.service_discovery import build_service_discovery_response
from modules.media_server.transports.ffmpeg_mjpeg import FFmpegMjpegTransport
from modules.media_server.transports.gstreamer_base import GStreamerBaseTransport
from modules.media_server.transports.gstreamer_rgba import GStreamerRgbaTransport
from modules.qt6_gui.media.audio_handler import QtAudioEngine, DynamicChannelAudioSink

# 1. Test NAL & Keyframe parsing across codecs
# H.264
h264_idr = b"\x00\x00\x00\x01\x65\x88\x84\x00"
h264_sps = b"\x00\x00\x00\x01\x67\x42\x00\x1f"
assert is_keyframe(h264_idr, "H264") is True
assert is_keyframe(h264_sps, "H264") is False
assert is_header_nal(h264_sps, "H264") is True
print("✔ H.264 NAL / Keyframe detection passed")

# H.265 (HEVC)
# NAL type 19 (IDR_W_RADL) -> (19 << 1) = 38 (0x26)
hevc_idr = b"\x00\x00\x00\x01\x26\x01\xaf"
# NAL type 32 (VPS) -> (32 << 1) = 64 (0x40)
hevc_vps = b"\x00\x00\x00\x01\x40\x01\x0c"
assert is_keyframe(hevc_idr, "H265") is True
assert is_keyframe(hevc_vps, "H265") is False
assert is_header_nal(hevc_vps, "H265") is True
print("✔ H.265 (HEVC) NAL / Keyframe detection passed")

# VP9
vp9_key = b"\x80\x00\x00"  # frame_marker=10 (binary), frame_type=0
assert is_keyframe(vp9_key, "VP9") is True
print("✔ VP9 Keyframe detection passed")

# AV1
av1_seq = b"\x0a\x00\x00"  # obu_type=1 (OBU_SEQUENCE_HEADER)
assert is_keyframe(av1_seq, "AV1") is True
assert is_header_nal(av1_seq, "AV1") is True
print("✔ AV1 OBU / Keyframe detection passed")

# 2. Test get_codec_descriptor for all 7 enums
for enum_val in range(1, 8):
    desc = get_codec_descriptor(enum_val)
    assert "media_type" in desc
    assert "codec_name" in desc
    print(f"✔ Enum {enum_val}: {desc['codec_name']} -> {desc.get('codec')} ({desc['media_type']})")

# 3. Test Service Discovery Response with dynamic codec configuration
# Test with HEVC video and PCM audio overrides
raw_sdr, sdr_dict, ch_map = build_service_discovery_response({"video_codec": "H265", "audio_codec": "PCM"})
assert len(raw_sdr) > 0
video_channel = next(ch for ch in sdr_dict["channels"] if "video_configs" in ch.get("av_channel", {}))
assert video_channel["av_channel"]["codec"] == "MEDIA_CODEC_VIDEO_H265"
print(f"✔ SDR dynamic configuration passed: Video codec={video_channel['av_channel']['codec']}")

# 4. Test Transport Demuxer / Parser Selection
ffmpeg_t = FFmpegMjpegTransport(video_codec="H265")
args = ffmpeg_t._build_ffmpeg_args()
assert "-f" in args and "hevc" in args
print(f"✔ FFmpeg HEVC demuxer args verified: {args}")

ffmpeg_vp9 = FFmpegMjpegTransport(video_codec="VP9")
assert "vp9" in ffmpeg_vp9._build_ffmpeg_args()
print("✔ FFmpeg VP9 demuxer args verified")

gst_rgba = GStreamerRgbaTransport(video_codec="H265")
pipe = gst_rgba._build_pipeline_string()
assert "h265parse" in pipe
print(f"✔ GStreamer HEVC pipeline verified: {pipe}")

# 5. Test Audio Engine with explicit codec and sample rate configuration
engine = QtAudioEngine()
engine.configure_channel_codec(4, "MEDIA_CODEC_AUDIO_AAC_LC_ADTS", sample_rate=48000, channel_count=2)
engine.configure_channel_codec(5, "MEDIA_CODEC_AUDIO_PCM", sample_rate=16000, channel_count=1)
assert 4 in engine.sinks and engine.sinks[4]._is_aac is True
assert engine.sinks[4].sample_rate == 48000 and engine.sinks[4].channel_count == 2
assert 5 in engine.sinks and engine.sinks[5]._is_aac is False
assert engine.sinks[5].sample_rate == 16000 and engine.sinks[5].channel_count == 1
print("✔ Dynamic Audio Engine codec and sample rate configuration passed (Ch4: 48kHz 2ch, Ch5: 16kHz 1ch)")
engine.close()

print("🎉 ALL MULTI-CODEC TESTS PASSED SUCCESSFULLY!")
