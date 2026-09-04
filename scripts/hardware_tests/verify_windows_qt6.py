#!/usr/bin/env python3
"""
verify_windows_qt6.py — Comprehensive Windows PyQt6 DLL Resolution & Diagnostic Tool.
"""

import os
import sys
import ctypes

def verify():
    print(f"[verify_windows_qt6] Python executable: {sys.executable}")
    print(f"[verify_windows_qt6] Platform: {sys.platform}")

    if sys.platform == "win32":
        try:
            from pathlib import Path
            repo_root = Path(__file__).resolve().parent.parent.parent
            sys.path.insert(0, str(repo_root / "backend"))
            from shared.platform.windows import setup_windows_dll_directories
            added = setup_windows_dll_directories()
            print(f"[verify_windows_qt6] Registered DLL directories: {added}")
        except Exception as e:
            print(f"[verify_windows_qt6] setup_windows_dll_directories notice: {e}")

            # Step 3: Inspect loaded VC++ Runtime DLL paths
            print("\n[verify_windows_qt6] Checking C++ Runtime DLL handles...")
            kernel32 = ctypes.windll.kernel32
            for vc_dll in ("vcruntime140.dll", "msvcp140.dll", "vcruntime140_1.dll"):
                h_mod = kernel32.GetModuleHandleW(vc_dll)
                if not h_mod:
                    h_mod = kernel32.LoadLibraryW(vc_dll)
                buf = ctypes.create_unicode_buffer(512)
                if h_mod and kernel32.GetModuleFileNameW(h_mod, buf, 512):
                    print(f"  → {vc_dll} loaded from: {buf.value}")
                else:
                    print(f"  → ⚠️ {vc_dll} handle: {h_mod}")

            # Step 4: Probing LoadLibraryExW with LOAD_WITH_ALTERED_SEARCH_PATH (0x00000008)
            LOAD_WITH_ALTERED_SEARCH_PATH = 0x00000008
            for base_dir in qt6_dirs:
                qt6_core_dll = os.path.join(base_dir, "Qt6Core.dll")
                if os.path.exists(qt6_core_dll):
                    print(f"\n[verify_windows_qt6] Probing Qt6Core.dll at: {qt6_core_dll}")
                    
                    # Direct WinDLL
                    try:
                        handle = ctypes.WinDLL(qt6_core_dll)
                        print(f"  → ✅ Qt6Core.dll loaded successfully via WinDLL: {handle}")
                    except Exception as exc:
                        print(f"  → ❌ Qt6Core.dll direct WinDLL load error: {exc}")

                    # LoadLibraryExW with ALTERED_SEARCH_PATH
                    try:
                        h_qt6 = kernel32.LoadLibraryExW(qt6_core_dll, None, LOAD_WITH_ALTERED_SEARCH_PATH)
                        if h_qt6:
                            print(f"  → ✅ LoadLibraryExW(ALTERED_SEARCH_PATH) succeeded: handle={h_qt6}")
                        else:
                            last_err = kernel32.GetLastError()
                            print(f"  → ❌ LoadLibraryExW failed with WinError: {last_err}")
                    except Exception as exc:
                        print(f"  → ❌ LoadLibraryExW exception: {exc}")

        except Exception as e:
            print(f"[verify_windows_qt6] Preload warning: {e}")

    try:
        import PyQt6.QtCore as QtCore
        print(f"\n✅ SUCCESS: PyQt6.QtCore imported cleanly! Qt Version: {QtCore.QT_VERSION_STR}")
    except ImportError as exc:
        print(f"\n❌ FAIL: PyQt6 import error: {exc}")
        print("\nDiagnostic Advice:")
        print("  1. Update Microsoft Visual C++ 2015-2022 Redistributable x64:")
        print("     https://aka.ms/vs/17/release/vc_redist.x64.exe")
        print("  2. If running from WSL UNC path (\\\\wsl.localhost\\...), copy or map repo to local Windows drive (C:\\...)")
        sys.exit(1)

if __name__ == "__main__":
    verify()
