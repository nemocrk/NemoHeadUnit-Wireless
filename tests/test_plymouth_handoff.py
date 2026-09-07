import shutil
import subprocess
from unittest.mock import patch, MagicMock
from backend.modules.qt6_gui.main import dismiss_boot_splash


def test_dismiss_boot_splash_invokes_retain_splash():
    mock_res = MagicMock(returncode=0)
    with patch("shutil.which", return_value="/usr/bin/plymouth"), \
         patch("subprocess.run", return_value=mock_res) as mock_run:
        dismiss_boot_splash()
        mock_run.assert_called_once_with(
            ["plymouth", "quit", "--retain-splash"],
            check=False,
            timeout=1.0,
        )


def test_dismiss_boot_splash_sudo_fallback():
    mock_fail = MagicMock(returncode=1)
    mock_success = MagicMock(returncode=0)
    with patch("shutil.which", return_value="/usr/bin/plymouth"), \
         patch("subprocess.run", side_effect=[mock_fail, mock_success]) as mock_run:
        dismiss_boot_splash()
        assert mock_run.call_count == 2
        mock_run.assert_called_with(
            ["sudo", "-n", "plymouth", "quit", "--retain-splash"],
            check=False,
            timeout=1.0,
        )


def test_dismiss_boot_splash_noop_when_missing():
    with patch("shutil.which", return_value=None), \
         patch("subprocess.run") as mock_run:
        dismiss_boot_splash()
        mock_run.assert_not_called()


def test_dismiss_boot_splash_handles_exception():
    with patch("shutil.which", return_value="/usr/bin/plymouth"), \
         patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="plymouth", timeout=1.0)):
        # Must not raise
        dismiss_boot_splash()
