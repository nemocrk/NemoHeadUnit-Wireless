"""
launcher.py — spawn / wait / kill channel_module subprocesses.

Each channel module lives at:
    v2/modules/channel_modules/channel_{module_type}/main.py

It is launched with CLI args so that no runtime config needs to
circulate on the message bus:

    python channel_{module_type}/main.py \\
        --module-name   channel_video_3 \\
        --channel-id    3 \\
        --sdr-bytes-hex <hex>

The launcher does NOT know about ZMQ — it is a pure process manager.
All readiness signalling happens via the bus (channel_manager.module_ready).
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

_HERE    = Path(__file__).parent        # v2/modules/channel_manager/
_MODULES = _HERE.parent                 # v2/modules/
_V2      = _MODULES.parent              # v2/

if str(_V2) not in sys.path:
    sys.path.insert(0, str(_V2))
if str(_MODULES) not in sys.path:
    sys.path.insert(0, str(_MODULES))

from shared.logger import get_logger    # noqa: E402

log = get_logger("channel_manager.launcher")

# Root of the v2 tree: three parents up from this file
CHANNEL_MODULES = _V2 / "modules" / "channel_modules"

# How long to wait for a module to self-exit before SIGTERM
GRACE_PERIOD = 5.0  # seconds


class ChannelProcess:
    """Wrapper around a single channel_module subprocess."""

    def __init__(
        self,
        module_name: str,
        module_type: str,
        channel_id:  int,
        sdr_bytes_hex: str,
    ) -> None:
        self.module_name   = module_name
        self.module_type   = module_type
        self.channel_id    = channel_id
        self.sdr_bytes_hex = sdr_bytes_hex
        self._proc: subprocess.Popen | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Resolve script path and spawn the subprocess.

        Raises:
            FileNotFoundError: if channel_module script does not exist on disk.
        """
        script = CHANNEL_MODULES / f"{self.module_type}" / "main.py"
        if not script.exists():
            raise FileNotFoundError(
                f"channel_manager: no script for module_type='{self.module_type}' "
                f"(expected {script}). WIP — module not yet implemented."
            )

        args = [
            sys.executable, str(script),
            "--module-name",   self.module_name,
            "--channel-id",    str(self.channel_id),
            "--sdr-bytes-hex", self.sdr_bytes_hex,
        ]
        self._proc = subprocess.Popen(
            args,
            stdout=sys.stdout,
            stderr=sys.stderr,
        )
        log.info(
            "Started %s (PID %d) — ch%d type=%s",
            self.module_name, self._proc.pid, self.channel_id, self.module_type,
        )

    def stop(self) -> None:
        """Orderly stop: wait for self-exit, then SIGTERM, then SIGKILL."""
        if self._proc is None or self._proc.poll() is not None:
            return

        # Give the module a chance to handle channel_manager.shutdown on its own
        deadline = time.monotonic() + GRACE_PERIOD
        while time.monotonic() < deadline:
            if self._proc.poll() is not None:
                log.info("%s exited (code %d)", self.module_name, self._proc.returncode)
                return
            time.sleep(0.1)

        log.info("Terminating %s (PID %d)...", self.module_name, self._proc.pid)
        self._proc.terminate()
        try:
            self._proc.wait(timeout=5)
            log.info("%s terminated (code %d)", self.module_name, self._proc.returncode)
        except subprocess.TimeoutExpired:
            log.warning("%s did not terminate — killing", self.module_name)
            self._proc.kill()
            self._proc.wait()

    @property
    def pid(self) -> int | None:
        return self._proc.pid if self._proc else None

    def poll(self) -> int | None:
        """Return exit code if process has ended, else None."""
        return self._proc.poll() if self._proc else None


class Launcher:
    """Manages the full set of ChannelProcess instances for one AA session."""

    def __init__(self) -> None:
        self._procs: dict[str, ChannelProcess] = {}  # module_name → ChannelProcess

    # ------------------------------------------------------------------
    # Session management
    # ------------------------------------------------------------------

    def start_all(self, channels: list[dict]) -> list[str]:
        """
        Spawn one subprocess per channel entry.

        Args:
            channels: list of dicts, each with keys:
                        module_name    (str)  e.g. "channel_video_3"
                        module_type    (str)  e.g. "video"
                        channel_id     (int)
                        sdr_bytes_hex  (str)

        Returns:
            list of module_name strings that were successfully started.

        Raises:
            FileNotFoundError: if any channel module script is missing.
        """
        # Kill any stale processes from a previous session
        if self._procs:
            log.warning("Stale channel processes found — killing before new session")
            self.stop_all()

        started: list[str] = []
        for ch in channels:
            cp = ChannelProcess(
                module_name=ch["module_name"],
                module_type=ch["module_type"],
                channel_id=ch["channel_id"],
                sdr_bytes_hex=ch["sdr_bytes_hex"],
            )
            cp.start()  # raises FileNotFoundError if script missing
            self._procs[cp.module_name] = cp
            started.append(cp.module_name)

        return started

    def stop_all(self) -> None:
        """Stop all channel subprocesses in reverse start order."""
        for cp in reversed(list(self._procs.values())):
            cp.stop()
        self._procs.clear()

    def check_crashes(self) -> list[str]:
        """Return module_names of processes that have exited unexpectedly."""
        return [
            name
            for name, cp in self._procs.items()
            if cp.poll() is not None
        ]

    @property
    def running(self) -> dict[str, ChannelProcess]:
        return dict(self._procs)
