import abc

class BaseWifiApAdapter(abc.ABC):
    """
    Abstract Hardware Adapter Interface for Wifi Access Point.
    """

    @abc.abstractmethod
    async def setup(self) -> None:
        """Initialize WiFi AP configurations."""
        pass

    @abc.abstractmethod
    async def start_ap(self, config: dict) -> tuple[bool, dict]:
        """
        Starts the WiFi Access Point.
        Returns:
            (success_boolean, active_credentials_dict)
            active_credentials_dict should contain: ssid, key, bssid, interface, gateway_ip
        """
        pass

    @abc.abstractmethod
    async def stop_ap(self) -> bool:
        """Stops the Access Point."""
        pass

    def get_status(self) -> dict:
        """Return current WiFi AP status dict."""
        return {"active": getattr(self, "_active", False), "ssid": getattr(self, "_ssid", "AndroidAutoAP")}

    @abc.abstractmethod
    async def teardown(self) -> None:
        """Clean up Wifi AP resources."""
        pass


def get_wifi_adapter() -> BaseWifiApAdapter:
    """
    Factory to return the appropriate BaseWifiApAdapter for the current OS.
    On Linux, attempts APManagerWifiApAdapter, falling back to Windows/Mock adapter if unavailable.
    On Windows/other, returns WindowsWifiApAdapter.
    """
    import sys
    if sys.platform.startswith("linux"):
        try:
            from .apmanager_wifi_ap import APManagerWifiApAdapter
            return APManagerWifiApAdapter()
        except Exception:
            pass
    from .windows_wifi_ap import WindowsWifiApAdapter
    return WindowsWifiApAdapter()
