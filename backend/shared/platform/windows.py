"""
backend/shared/platform/windows.py — Windows DLL resolution and platform bootstrap.

Ensures PyQt6, GStreamer, C-extensions, and runtime libraries resolve cleanly on Windows
by dynamically populating PATH and registering Win32 DLL search directories via os.add_dll_directory().
"""

import os
import sys
import logging
from pathlib import Path
from typing import List

logger = logging.getLogger("platform.windows")


def setup_windows_dll_directories() -> List[str]:
    """
    Configure DLL search paths for Windows.
    Prepend PyQt6, Qt6/bin, Conda Library/bin, and GStreamer bin directories
    to os.environ['PATH'] and invoke os.add_dll_directory() for Win32 security compatibility.
    """
    if sys.platform != "win32":
        return []

    registered_dirs: List[str] = []
    try:
        import site

        prefix = sys.prefix
        candidate_dirs: List[str] = []

        # 1. PyQt6 / Qt6 directories from site-packages
        site_dirs: List[str] = []
        try:
            site_dirs.extend(site.getsitepackages())
        except Exception:
            pass
        try:
            site_dirs.append(site.getusersitepackages())
        except Exception:
            pass
        for path_entry in sys.path:
            if "site-packages" in str(path_entry):
                site_dirs.append(str(path_entry))

        for d in set(site_dirs):
            qt6_bin = os.path.join(d, "PyQt6", "Qt6", "bin")
            qt6_root = os.path.join(d, "PyQt6")
            if os.path.isdir(qt6_bin):
                candidate_dirs.append(qt6_bin)
            if os.path.isdir(qt6_root):
                candidate_dirs.append(qt6_root)

        # 2. Python environment DLL directories
        candidate_dirs.extend([
            os.path.join(prefix, "DLLs"),
            os.path.join(prefix, "Library", "bin"),
        ])

        # 3. GStreamer Windows root paths
        for env_var in ("GSTREAMER_1_0_ROOT_MSVC_X86_64", "GSTREAMER_1_0_ROOT_X86_64", "GST_ROOT"):
            val = os.environ.get(env_var)
            if val:
                gst_bin = os.path.join(val, "bin")
                if os.path.isdir(gst_bin):
                    candidate_dirs.append(gst_bin)

        for default_gst in (
            r"C:\gstreamer\1.0\msvc_x86_64\bin",
            r"C:\gstreamer\1.0\x86_64\bin",
            r"C:\gstreamer\1.0\mingw_x86_64\bin",
        ):
            if os.path.isdir(default_gst):
                candidate_dirs.append(default_gst)

        # 4. Register unique valid directories
        seen = set()
        for d in candidate_dirs:
            norm_d = os.path.normpath(d)
            if norm_d in seen or not os.path.isdir(norm_d):
                continue
            seen.add(norm_d)

            # Prepend to PATH
            current_path = os.environ.get("PATH", "")
            if norm_d not in current_path:
                os.environ["PATH"] = norm_d + os.pathsep + current_path

            # Add via os.add_dll_directory (Python 3.8+ on Windows)
            if hasattr(os, "add_dll_directory"):
                try:
                    os.add_dll_directory(norm_d)
                    registered_dirs.append(norm_d)
                except Exception as e:
                    logger.debug("os.add_dll_directory(%s) notice: %s", norm_d, e)

        # 5. Pre-load Qt6Core.dll using LOAD_WITH_ALTERED_SEARCH_PATH if found
        import ctypes
        LOAD_WITH_ALTERED_SEARCH_PATH = 0x00000008
        for d in candidate_dirs:
            qt6_core = os.path.join(d, "Qt6Core.dll")
            if os.path.isfile(qt6_core):
                try:
                    ctypes.windll.kernel32.LoadLibraryExW(qt6_core, None, LOAD_WITH_ALTERED_SEARCH_PATH)
                except Exception:
                    pass

    except Exception as exc:
        logger.debug("setup_windows_dll_directories notice: %s", exc)

    return registered_dirs
