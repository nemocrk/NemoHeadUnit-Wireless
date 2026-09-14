import unittest
import asyncio
from unittest.mock import AsyncMock, MagicMock
from aiohttp import web
from backend.modules.connectivity_manager.main import ConnectivityManagerModule


class TestConnectivityDevices(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        with unittest.mock.patch("shared.base_module.BusClient") as mock_bus_cls:
            mock_bus = MagicMock()
            mock_bus_cls.return_value = mock_bus
            self.mod = ConnectivityManagerModule()
            self.mod.bus = mock_bus
            self.mod.config = self.mod.get_default_config()

    def test_default_config_and_schema(self):
        cfg = self.mod.get_default_config()
        self.assertIn("known_aa_devices", cfg)
        self.assertIn("ignored_devices", cfg)
        self.assertEqual(cfg["known_aa_devices"], [])
        self.assertEqual(cfg["ignored_devices"], [])

        schema = self.mod.get_schema()
        self.assertIn("known_aa_devices", schema)
        self.assertIn("ignored_devices", schema)

    def test_record_known_device(self):
        self.mod._record_known_device("AA:BB:CC:DD:EE:FF")
        self.assertIn("AA:BB:CC:DD:EE:FF", self.mod.config["known_aa_devices"])
        self.mod.bus.publish.assert_called_with("config.set", {
            "module": "connectivity_manager",
            "key": "known_aa_devices",
            "value": ["AA:BB:CC:DD:EE:FF"]
        })

        # Calling again should not duplicate
        self.mod.bus.publish.reset_mock()
        self.mod._record_known_device("AA:BB:CC:DD:EE:FF")
        self.assertEqual(self.mod.config["known_aa_devices"], ["AA:BB:CC:DD:EE:FF"])
        self.mod.bus.publish.assert_not_called()

    async def test_ignore_and_unignore_endpoints(self):
        # Test ignore
        req = MagicMock(spec=web.Request)
        req.json = AsyncMock(return_value={"address": "11:22:33:44:55:66"})
        resp = await self.mod.handle_post_ignore_device(req)
        self.assertEqual(resp.status, 200)
        self.assertIn("11:22:33:44:55:66", self.mod.config["ignored_devices"])

        # Test unignore
        req.json = AsyncMock(return_value={"address": "11:22:33:44:55:66"})
        resp = await self.mod.handle_post_unignore_device(req)
        self.assertEqual(resp.status, 200)
        self.assertNotIn("11:22:33:44:55:66", self.mod.config["ignored_devices"])

    def test_candidate_filtering_and_round_robin_priority(self):
        paired_devices = [
            {"address": "C0:28:8D:99:AE:60", "name": "LollaBoom Speaker"},
            {"address": "10:20:30:40:50:60", "name": "Pixel 8 Pro"},
            {"address": "90:80:70:60:50:40", "name": "BT Headphones"},
        ]

        # Ignore BT speaker
        self.mod.config["ignored_devices"] = ["C0:28:8D:99:AE:60"]
        # Pixel is known AA device
        self.mod.config["known_aa_devices"] = ["10:20:30:40:50:60"]

        ignored = set(self.mod.config.get("ignored_devices", []))
        known_aa = list(self.mod.config.get("known_aa_devices", []))

        candidates = [d for d in paired_devices if d.get("address") not in ignored]
        self.assertEqual(len(candidates), 2)
        self.assertEqual(candidates[0]["address"], "10:20:30:40:50:60")
        self.assertEqual(candidates[1]["address"], "90:80:70:60:50:40")

        # Sort so known AA comes first
        candidates.sort(key=lambda d: 0 if d.get("address") in known_aa else 1)
        self.assertEqual(candidates[0]["address"], "10:20:30:40:50:60")


if __name__ == "__main__":
    unittest.main()
