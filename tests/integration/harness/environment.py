# tests/integration/harness/environment.py
import os
import yaml
from pathlib import Path
from typing import Any, Dict

class IntegrationEnvironment:
    """Manages an isolated configuration sandbox and dynamic ports for integration tests."""
    def __init__(self, base_dir: Path):
        self.base_dir = Path(base_dir)
        self.config_dir = self.base_dir / "config"
        self.ipc_dir = self.base_dir / "ipc"
        self.config_file = self.config_dir / "config.yaml"
        self.prev_config_dir = None
        self.prev_ipc_dir = None
        self.config_data: Dict[str, Any] = {
            "proxy": {"public_port": 0, "host": "127.0.0.1"},
            "tcp_server": {"port": 0, "host": "127.0.0.1", "enable_ssl": False},
            "bus_broker": {"pub_port": 0, "router_port": 0},
            "channel_manager": {"video_buffer_count": 4}
        }
        # Flattened top-level keys for modules checking direct keys
        self.config_data["public_port"] = 0

    def __enter__(self):
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.ipc_dir.mkdir(parents=True, exist_ok=True)
        with open(self.config_file, "w", encoding="utf-8") as f:
            yaml.safe_dump(self.config_data, f)
        self.prev_config_dir = os.environ.get("NEMO_CONFIG_DIR")
        self.prev_ipc_dir = os.environ.get("NEMO_IPC_DIR")
        os.environ["NEMO_CONFIG_DIR"] = str(self.config_dir)
        os.environ["NEMO_IPC_DIR"] = str(self.ipc_dir)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.prev_config_dir is not None:
            os.environ["NEMO_CONFIG_DIR"] = self.prev_config_dir
        else:
            os.environ.pop("NEMO_CONFIG_DIR", None)
        if self.prev_ipc_dir is not None:
            os.environ["NEMO_IPC_DIR"] = self.prev_ipc_dir
        else:
            os.environ.pop("NEMO_IPC_DIR", None)
