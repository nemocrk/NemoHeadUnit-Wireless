from protos.oaa.control.ControlMessageIdsEnum_pb2 import ControlMessage
from protos.oaa.av.AVChannelMessageIdsEnum_pb2 import AVChannelMessage

CHANNEL_MESSAGES_DEBUG_LEVELS = {
    "CONTROL": {
        ControlMessage.Enum.PING_REQUEST: "debug",
        ControlMessage.Enum.PING_RESPONSE: "debug",
    },
    "INPUT": {
    },
    "SENSOR": {
    },
    "VIDEO": {
        AVChannelMessage.Enum.AV_MEDIA_WITH_TIMESTAMP_INDICATION: "None",
        AVChannelMessage.Enum.AV_MEDIA_INDICATION: "None",
    },
    "AUDIO": {
        AVChannelMessage.Enum.AV_MEDIA_WITH_TIMESTAMP_INDICATION: "None",
        AVChannelMessage.Enum.AV_MEDIA_INDICATION: "None",
    },
    "AUDIO_MIC": {
        AVChannelMessage.Enum.AV_MEDIA_WITH_TIMESTAMP_INDICATION: "None",
        AVChannelMessage.Enum.AV_MEDIA_INDICATION: "None",
        AVChannelMessage.Enum.AV_MEDIA_ACK_INDICATION: "None",
    },
}