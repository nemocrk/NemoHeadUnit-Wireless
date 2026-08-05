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
            import site
            prefix = sys.prefix
            
            # Step 1: Locate PyQt6\Qt6\bin FIRST
            qt6_dirs = []
            site_dirs = []
            try:
                site_dirs.extend(site.getsitepackages())
            except Exception:
                pass
            try:
                site_dirs.append(site.getusersitepackages())
            except Exception:
                pass

            for d in site_dirs:
                qt6_bin = os.path.join(d, "PyQt6", "Qt6", "bin")
                qt6_root = os.path.join(d, "PyQt6")
                if os.path.exists(qt6_bin):
                    qt6_dirs.append(qt6_bin)
                if os.path.exists(qt6_root):
                    qt6_dirs.append(qt6_root)

            # Step 2: Build priority list (PyQt6 bin FIRST)
            priority_dirs = qt6_dirs + [
                os.path.join(prefix, "DLLs"),
                os.path.join(prefix, "Library", "bin"),
            ]

            # Register priority directories
            for d in priority_dirs:
                if os.path.exists(d):
                    if d not in os.environ.get("PATH", ""):
                        os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")
                    if hasattr(os, "add_dll_directory"):
                        try:
                            os.add_dll_directory(d)
                            print(f"[verify_windows_qt6] Added DLL directory: {d}")
                        except Exception as err:
                            print(f"[verify_windows_qt6] add_dll_directory notice ({d}): {err}")

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
