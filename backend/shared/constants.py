from enum import Enum, auto


class ChannelType(Enum):
    CONTROL   = auto()  # Channel 0 (Control)
    INPUT     = auto()  # Touch / Key events
    SENSOR    = auto()  # Night mode, Driving status, Parking brake
    VIDEO     = auto()  # H.264 / VP9 Video stream
    AUDIO     = auto()  # Media / Speech / System Audio output stream
    AUDIO_MIC = auto()  # Microphone Audio input stream (av_input_channel)
    BLUETOOTH      = auto()  # BT pairing
    WIFI           = auto()  # WiFi credentials
    NAVIGATION     = auto()  # Turn-by-turn navigation data
    MEDIA_PLAYBACK = auto()  # Music track metadata and status
    PHONE_STATUS   = auto()  # Cellular signal, battery, in-call state
    NOTIFICATION   = auto()  # Push notifications & heads-up alerts
    MEDIA_BROWSER  = auto()  # Music library folder / playlist browsing
    UNKNOWN        = auto()
