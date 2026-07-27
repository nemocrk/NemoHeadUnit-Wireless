import abc
from typing import Callable, Optional

class BaseBluetoothAdapter(abc.ABC):
    """
    Abstract Hardware Adapter Interface for Bluetooth functionality.
    """

    @abc.abstractmethod
    async def setup(self, adapter_name: str, discoverable: bool, discoverable_timeout: int) -> None:
        """Initialize Bluetooth adapter settings."""
        pass

    @abc.abstractmethod
    async def start_discovery(self, duration_sec: int, on_device_found_cb: Callable[[dict], None]) -> None:
        """Start active Bluetooth device discovery."""
        pass

    @abc.abstractmethod
    async def stop_discovery(self) -> None:
        """Stop device discovery."""
        pass

    @abc.abstractmethod
    async def pair_device(self, address: str, on_pin_cb: Callable[[str, str], None]) -> tuple[bool, str]:
        """Initiate pairing with a remote device."""
        pass

    @abc.abstractmethod
    async def confirm_pairing(self, address: str, confirm: bool) -> bool:
        """Confirm pairing code."""
        pass

    @abc.abstractmethod
    async def connect_device(self, address: str) -> tuple[bool, str]:
        """Connect profiles for a paired device."""
        pass

    @abc.abstractmethod
    async def disconnect_device(self, address: str) -> bool:
        """Disconnect profiles for a device."""
        pass

    @abc.abstractmethod
    async def remove_paired_device(self, address: str) -> bool:
        """Unpair a device."""
        pass

    @abc.abstractmethod
    async def get_paired_devices(self) -> list[dict]:
        """Get list of paired devices."""
        pass

    @abc.abstractmethod
    def register_rfcomm_server(self, on_connection_cb: Callable[[object, str], None]) -> bool:
        """Register the Android Auto RFCOMM service profile and listen for connections."""
        pass

    def get_adapter_address(self) -> str:
        """Get the local Bluetooth adapter MAC address."""
        return ""

    @abc.abstractmethod
    async def teardown(self) -> None:
        """Clean up Bluetooth adapter resources."""
        pass

