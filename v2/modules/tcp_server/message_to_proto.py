
import sys
from pathlib import Path


_HERE         = Path(__file__).parent
_MODULES      = _HERE.parent
_V2           = _MODULES.parent
_PROTOS       = _V2 / "protos"

for _p in (_V2, _MODULES, _PROTOS):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from oaa.av.AVChannelMessageIdsEnum_pb2 import AVChannelMessage
from oaa.bluetooth.BluetoothChannelMessageIdsEnum_pb2 import BluetoothChannelMessage
from oaa.control.ControlMessageIdsEnum_pb2 import ControlMessage
from oaa.input.InputChannelMessageIdsEnum_pb2 import InputChannelMessage
from oaa.sensor.SensorChannelMessageIdsEnum_pb2 import SensorChannelMessage
from oaa.wifi.WifiChannelMessageIdsEnum_pb2 import WifiChannelMessage
"""
Module for mapping channel descriptors to module types.
This is where we define the mapping from channel descriptors (av_type, bluetooth_type, etc.) to module types (video, audio, input, sensor, etc.) that determine which subprocess to launch for a given channel.
The mapping is based on the channel descriptor keys defined in the protobufs, which are designed to be extensible for future channel types and use cases. The current mapping focuses on AV channels, which are the most complex and have the most variation across car models
"""

def message_id_to_proto_name(message_id: int):
    """Map a message_id to the corresponding protobuf message class."""
    if message_id in AVChannelMessage.Enum.values():
        return AVChannelMessage.Enum.Name(message_id)
    elif message_id in BluetoothChannelMessage.Enum.values():
        return BluetoothChannelMessage.Enum.Name(message_id)
    elif message_id in ControlMessage.Enum.values():
        return ControlMessage.Enum.Name(message_id)
    elif message_id in InputChannelMessage.Enum.values():
        return InputChannelMessage.Enum.Name(message_id)
    elif message_id in SensorChannelMessage.Enum.values():
        return SensorChannelMessage.Enum.Name(message_id)
    elif message_id in WifiChannelMessage.Enum.values():
        return WifiChannelMessage.Enum.Name(message_id)
    else:
        return f"UnknownMessageId {message_id}"
    
def proto_name_to_class(proto_name: str):
    """Map a protobuf message name to the corresponding protobuf message class."""
    # This function would need to import all the relevant protobuf message classes and map their names to the classes themselves.
    # For example:
    from oaa.av.AVChannelSetupRequestMessage_pb2 import AVChannelSetupRequest
    from oaa.av.AVChannelStartIndicationMessage_pb2 import AVChannelStartIndication
    from oaa.av.AVChannelStopIndicationMessage_pb2 import AVChannelStopIndication
    from oaa.av.AVChannelSetupResponseMessage_pb2 import AVChannelSetupResponse
    from oaa.av.AVMediaAckIndicationMessage_pb2 import AVMediaAckIndication
    from oaa.av.AVInputOpenRequestMessage_pb2 import AVInputOpenRequest
    from oaa.av.AVInputOpenResponseMessage_pb2 import AVInputOpenResponse
    from oaa.video.VideoFocusRequestMessage_pb2 import VideoFocusRequest
    from oaa.video.VideoFocusIndicationMessage_pb2 import VideoFocusIndication
    from oaa.video.VideoFocusNotificationMessage_pb2 import VideoFocusNotification
    from oaa.video.UpdateUiConfigRequestMessage_pb2 import UpdateUiConfigRequest
    from oaa.av.UiConfigMessages_pb2 import UpdateUiConfigReply
    from oaa.media.MediaPlaybackCommandMessage_pb2 import MediaPlaybackCommand
    from oaa.video.IntegratedOverlayStartNotification_pb2 import IntegratedOverlayStartNotification
    from oaa.video.IntegratedOverlayStopNotification_pb2 import IntegratedOverlayStopNotification
    from oaa.av.UiConfigMessages_pb2 import UpdateHuUiConfigRequest
    from oaa.video.UpdateHuUiConfigResponse_pb2 import UpdateHuUiConfigResponse
    from oaa.av.AVChannelMediaStatsMessage_pb2 import AVChannelMediaStats
    from oaa.av.AVChannelMediaOptionsMessage_pb2 import AVChannelMediaOptions
    av_switcher = {
        """
            [Module Descriptor]: oaa/av/AVChannelMessageIdsEnum.proto
            └─ AVChannelMessage.Enum (Enum)
                ├── AV_MEDIA_WITH_TIMESTAMP_INDICATION = 0
                ├── AV_MEDIA_INDICATION = 1
                ├── SETUP_REQUEST = 32768
                ├── START_INDICATION = 32769
                ├── STOP_INDICATION = 32770
                ├── SETUP_RESPONSE = 32771
                ├── AV_MEDIA_ACK_INDICATION = 32772
                ├── AV_INPUT_OPEN_REQUEST = 32773
                ├── AV_INPUT_OPEN_RESPONSE = 32774
                ├── VIDEO_FOCUS_REQUEST = 32775
                ├── VIDEO_FOCUS_INDICATION = 32776
                ├── VIDEO_FOCUS_NOTIFICATION = 32777
                ├── UPDATE_UI_CONFIG_REQUEST = 32778
                ├── UPDATE_UI_CONFIG_REPLY = 32779
                ├── AUDIO_UNDERFLOW = 32780
                ├── ACTION_TAKEN = 32781
                ├── OVERLAY_PARAMETERS = 32782
                ├── OVERLAY_START = 32783
                ├── OVERLAY_STOP = 32784
                ├── OVERLAY_SESSION_UPDATE = 32785
                ├── UPDATE_HU_UI_CONFIG_REQUEST = 32786
                ├── UPDATE_HU_UI_CONFIG_RESPONSE = 32787
                ├── MEDIA_STATS = 32788
                ├── MEDIA_OPTIONS = 32789
        """
        "AV_MEDIA_WITH_TIMESTAMP_INDICATION": bytes,
        "AV_MEDIA_INDICATION": bytes,
        "SETUP_REQUEST": AVChannelSetupRequest,
        "START_INDICATION": AVChannelStartIndication,
        "STOP_INDICATION": AVChannelStopIndication,
        "SETUP_RESPONSE": AVChannelSetupResponse,
        "AV_MEDIA_ACK_INDICATION": AVMediaAckIndication,
        "AV_INPUT_OPEN_REQUEST": AVInputOpenRequest,
        "AV_INPUT_OPEN_RESPONSE": AVInputOpenResponse,
        "VIDEO_FOCUS_REQUEST": VideoFocusRequest,
        "VIDEO_FOCUS_INDICATION": VideoFocusIndication,
        "VIDEO_FOCUS_NOTIFICATION": VideoFocusNotification,
        "UPDATE_UI_CONFIG_REQUEST": UpdateUiConfigRequest,
        "UPDATE_UI_CONFIG_REPLY": UpdateUiConfigReply,
        "AUDIO_UNDERFLOW": bytes, # Placeholder, replace with actual protobuf class when defined
        "ACTION_TAKEN": MediaPlaybackCommand,
        "OVERLAY_PARAMETERS": bytes, # Placeholder, replace with actual protobuf class when defined
        "OVERLAY_START": IntegratedOverlayStartNotification,
        "OVERLAY_STOP": IntegratedOverlayStopNotification,
        "OVERLAY_SESSION_UPDATE": bytes, # Placeholder, replace with actual protobuf class when defined
        "UPDATE_HU_UI_CONFIG_REQUEST": UpdateHuUiConfigRequest,
        "UPDATE_HU_UI_CONFIG_RESPONSE": UpdateHuUiConfigResponse,
        "MEDIA_STATS": AVChannelMediaStats,
        "MEDIA_OPTIONS": AVChannelMediaOptions,
    }
    """
        [Module Descriptor]: oaa/bluetooth/BluetoothChannelMessageIdsEnum.proto
        └─ BluetoothChannelMessage.Enum (Enum)
            ├── NONE = 0
            ├── PAIRING_REQUEST = 32769
            ├── PAIRING_RESPONSE = 32770
            ├── AUTH_DATA = 32771
            ├── AUTH_RESULT = 32772
    """
    from oaa.bluetooth.BluetoothPairingRequestMessage_pb2 import BluetoothPairingRequest
    from oaa.bluetooth.BluetoothPairingResponseMessage_pb2 import BluetoothPairingResponse
    from oaa.bluetooth.BluetoothAuthenticationDataMessage_pb2 import BluetoothAuthenticationData
    from oaa.bluetooth.BluetoothAuthenticationResultMessage_pb2 import BluetoothAuthenticationResult
    bluetooth_switcher = {
        "NONE": bytes, # Placeholder, replace with actual protobuf class when defined
        "PAIRING_REQUEST": BluetoothPairingRequest,
        "PAIRING_RESPONSE": BluetoothPairingResponse,
        "AUTH_DATA": BluetoothAuthenticationData,
        "AUTH_RESULT": BluetoothAuthenticationResult,
    }
    """
        [Module Descriptor]: oaa/control/ControlMessageIdsEnum.proto
        └─ ControlMessage.Enum (Enum)
            ├── NONE = 0
            ├── VERSION_REQUEST = 1
            ├── VERSION_RESPONSE = 2
            ├── SSL_HANDSHAKE = 3
            ├── AUTH_COMPLETE = 4
            ├── SERVICE_DISCOVERY_REQUEST = 5
            ├── SERVICE_DISCOVERY_RESPONSE = 6
            ├── CHANNEL_OPEN_REQUEST = 7
            ├── CHANNEL_OPEN_RESPONSE = 8
            ├── PING_REQUEST = 11
            ├── PING_RESPONSE = 12
            ├── NAVIGATION_FOCUS_REQUEST = 13
            ├── NAVIGATION_FOCUS_RESPONSE = 14
            ├── SHUTDOWN_REQUEST = 15
            ├── SHUTDOWN_RESPONSE = 16
            ├── VOICE_SESSION_REQUEST = 17
            ├── AUDIO_FOCUS_REQUEST = 18
            ├── AUDIO_FOCUS_RESPONSE = 19
            ├── CAR_CONNECTED_DEVICES_REQUEST = 20
            ├── CAR_CONNECTED_DEVICES_RESPONSE = 21
            ├── USER_SWITCH_REQUEST = 22
            ├── BATTERY_STATUS_NOTIFICATION = 23
            ├── CALL_AVAILABILITY_STATUS = 24
            ├── USER_SWITCH_RESPONSE = 25
            ├── SERVICE_DISCOVERY_UPDATE = 26
            ├── CHANNEL_CLOSE_NOTIFICATION = 9
    """
    from oaa.control.AuthCompleteIndicationMessage_pb2 import AuthCompleteIndication
    from oaa.control.ServiceDiscoveryRequestMessage_pb2 import ServiceDiscoveryRequest
    from oaa.control.ServiceDiscoveryResponseMessage_pb2 import ServiceDiscoveryResponse
    from oaa.control.ChannelOpenRequestMessage_pb2 import ChannelOpenRequest
    from oaa.control.ChannelOpenResponseMessage_pb2 import ChannelOpenResponse
    from oaa.control.PingRequestMessage_pb2 import PingRequest
    from oaa.control.PingResponseMessage_pb2 import PingResponse
    from oaa.navigation.NavigationFocusRequestMessage_pb2 import NavigationFocusRequest
    from oaa.navigation.NavigationFocusResponseMessage_pb2 import NavigationFocusResponse
    from oaa.control.ShutdownRequestMessage_pb2 import ShutdownRequest
    from oaa.control.ByeByeResponseMessage_pb2 import ByeByeResponse
    from oaa.control.VoiceSessionRequestMessage_pb2 import VoiceSessionRequest
    from oaa.audio.AudioFocusRequestMessage_pb2 import AudioFocusRequest
    from oaa.audio.AudioFocusResponseMessage_pb2 import AudioFocusResponse
    from oaa.control.ConnectedDevicesMessages_pb2 import CarConnectedDevicesRequest
    from oaa.control.ConnectedDevicesMessages_pb2 import CarConnectedDevicesResponse
    from oaa.control.ConnectedDevicesMessages_pb2 import UserSwitchRequest
    from oaa.control.ConnectedDevicesMessages_pb2 import UserSwitchResponse
    from oaa.control.BatteryStatusMessage_pb2 import BatteryStatusNotification
    from oaa.control.CallAvailabilityMessage_pb2 import CallAvailabilityStatus
    from oaa.control.ServiceDiscoveryUpdateMessage_pb2 import ServiceDiscoveryUpdate
    from oaa.control.ChannelCloseNotificationMessage_pb2 import ChannelCloseNotification
    control_switcher = {
        "NONE": bytes, # Placeholder, replace with actual protobuf class when defined
        "VERSION_REQUEST": bytes, # Placeholder, replace with actual protobuf class when defined
        "VERSION_RESPONSE": bytes, # Placeholder, replace with actual protobuf class when defined
        "SSL_HANDSHAKE": bytes, # Placeholder, replace with actual protobuf class when defined
        "AUTH_COMPLETE": AuthCompleteIndication, 
        "SERVICE_DISCOVERY_REQUEST": ServiceDiscoveryRequest,
        "SERVICE_DISCOVERY_RESPONSE": ServiceDiscoveryResponse, 
        "CHANNEL_OPEN_REQUEST": ChannelOpenRequest,
        "CHANNEL_OPEN_RESPONSE": ChannelOpenResponse,
        "PING_REQUEST": PingRequest,
        "PING_RESPONSE": PingResponse,
        "NAVIGATION_FOCUS_REQUEST": NavigationFocusRequest,
        "NAVIGATION_FOCUS_RESPONSE": NavigationFocusResponse,
        "SHUTDOWN_REQUEST": ShutdownRequest,
        "SHUTDOWN_RESPONSE": ByeByeResponse,
        "VOICE_SESSION_REQUEST": VoiceSessionRequest,
        "AUDIO_FOCUS_REQUEST": AudioFocusRequest,
        "AUDIO_FOCUS_RESPONSE": AudioFocusResponse,
        "CAR_CONNECTED_DEVICES_REQUEST": CarConnectedDevicesRequest,
        "CAR_CONNECTED_DEVICES_RESPONSE": CarConnectedDevicesResponse,
        "USER_SWITCH_REQUEST": UserSwitchRequest,
        "BATTERY_STATUS_NOTIFICATION": BatteryStatusNotification,
        "CALL_AVAILABILITY_STATUS": CallAvailabilityStatus,
        "USER_SWITCH_RESPONSE": UserSwitchResponse,
        "SERVICE_DISCOVERY_UPDATE": ServiceDiscoveryUpdate,
        "CHANNEL_CLOSE_NOTIFICATION": ChannelCloseNotification,
    }
    """
        [Module Descriptor]: oaa/input/InputChannelMessageIdsEnum.proto
        └─ InputChannelMessage.Enum (Enum)
            ├── NONE = 0
            ├── INPUT_EVENT_INDICATION = 32769
            ├── BINDING_REQUEST = 32770
            ├── BINDING_RESPONSE = 32771
            ├── BINDING_NOTIFICATION = 32772
    """
    from oaa.input.InputEventIndicationMessage_pb2 import InputEventIndication
    from oaa.control.BindingRequestMessage_pb2 import BindingRequest
    from oaa.control.BindingResponseMessage_pb2 import BindingResponse
    from oaa.input.InputBindingNotificationMessage_pb2 import InputBindingNotification
    input_switcher = {
        "NONE": bytes, # Placeholder for Input channel message name to protobuf class mapping
        "INPUT_EVENT_INDICATION": InputEventIndication,
        "BINDING_REQUEST": BindingRequest,
        "BINDING_RESPONSE": BindingResponse,
        "BINDING_NOTIFICATION": InputBindingNotification,
    }
    """
        [Module Descriptor]: oaa/sensor/SensorChannelMessageIdsEnum.proto
        └─ SensorChannelMessage.Enum (Enum)
            ├── NONE = 0
            ├── SENSOR_REQUEST = 32769
            ├── SENSOR_START_RESPONSE = 32770
            ├── SENSOR_EVENT_INDICATION = 32771
            ├── SENSOR_ERROR = 32772
    """
    from oaa.sensor.SensorRequestMessage_pb2 import SensorRequest
    from oaa.sensor.SensorStartResponseMessage_pb2 import SensorStartResponseMessage
    from oaa.sensor.SensorEventIndicationMessage_pb2 import SensorEventIndication
    from oaa.sensor.SensorErrorMessage_pb2 import SensorError
    sensor_switcher = {
        "NONE": bytes, # Placeholder for Sensor channel message name to protobuf class mapping
        "SENSOR_REQUEST": SensorRequest,
        "SENSOR_START_RESPONSE": SensorStartResponseMessage,
        "SENSOR_EVENT_INDICATION": SensorEventIndication,
        "SENSOR_ERROR": SensorError, 
    }
    """
        [Module Descriptor]: oaa/wifi/WifiChannelMessageIdsEnum.proto
        └─ WifiChannelMessage.Enum (Enum)
            ├── NONE = 0
            ├── CREDENTIALS_REQUEST = 32769
            ├── CREDENTIALS_RESPONSE = 32770
    """
    from oaa.wifi.WifiSecurityRequestMessage_pb2 import WifiSecurityRequest
    from oaa.wifi.WifiSecurityResponseMessage_pb2 import WifiSecurityResponse
    wifi_switcher = {
        "NONE": bytes, # Placeholder for Wifi channel message name to protobuf class mapping
        "CREDENTIALS_REQUEST": WifiSecurityRequest,
        "CREDENTIALS_RESPONSE": WifiSecurityResponse,
    }

    if proto_name in av_switcher:
        return av_switcher[proto_name]
    elif proto_name in bluetooth_switcher:
        return bluetooth_switcher[proto_name]
    elif proto_name in control_switcher:
        return control_switcher[proto_name]
    elif proto_name in input_switcher:
        return input_switcher[proto_name]
    elif proto_name in sensor_switcher:
        return sensor_switcher[proto_name]
    elif proto_name in wifi_switcher:
        return wifi_switcher[proto_name]
    else:
        raise ValueError(f"Unknown proto_name: {proto_name}")

def frame_data_to_dict(frame_data: dict) -> dict:
    """Convert a protobuf FrameData message (as dict) to a more convenient format.

    frame_data contains message_id that we map to protobuf message classes, and payload that we parse with the appropriate class.
    Returns a dict with keys:
      - message_id: the original message_id from frame_data
      - message_name: the name of the protobuf message class corresponding to message_id
      - payload: the parsed protobuf message as a dict (or None if parsing failed)
    """
    from google.protobuf.json_format import MessageToDict  # lazy import
    message_id = frame_data.get("message_id")
    payload_hex = frame_data.get("payload_hex")  # Assuming payload is in hex format and needs to be parsed
    message_name = None
    parsed_payload = None
    try:
        message_name = message_id_to_proto_name(message_id)
        proto_class = proto_name_to_class(message_name)
        parsed_message = proto_class()
        parsed_message.ParseFromString(bytes.fromhex(payload_hex))
        parsed_payload = MessageToDict(parsed_message)
    except Exception as exc:
        # If we fail to parse the message, we can log the error and return the original payload as hex
        print(f"Failed to parse message_id {message_id} with payload {payload_hex}: {exc}")
        parsed_payload = payload_hex  # Fallback to raw hex if parsing fails
    return {
        "message_id": message_id,
        "message_name": message_name,
        "payload_as_dict": parsed_payload,
    }


print (frame_data_to_dict({
    "message_id": 0x8001, 
    "payload_hex": "0802120d596f7554756265204d75736963183f200028003000"  # This is a hex representation of a protobuf message, which we will attempt to parse
}))