# Seamless Plymouth Boot Splash to EGLFS (HP Omni 10 Fix) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Provide a 100% silent, seamless boot transition on HP Omni 10 from UEFI POST logo through Plymouth animated spinner directly into Qt6 EGLFS without GRUB timeouts, failed boot delays, ramdisk loading text, or VT cursor flicker.

**Architecture:** Integrate all changes into the centralized HP Omni 10 hardware fix system (`packaging/hardware_fixes/fix_omni10.sh`):
1. Configure GRUB for zero timeout (`GRUB_TIMEOUT=0`, `GRUB_TIMEOUT_STYLE=hidden`, `GRUB_RECORDFAIL_TIMEOUT=0`).
2. Silence the GRUB `Loading Linux ...` and `Loading initial ramdisk ...` text messages in `/etc/grub.d/10_linux`.
3. Add quiet splash kernel flags (`quiet splash vt.global_cursor_default=0 loglevel=3 rd.systemd.show_status=false systemd.show_status=false`).
4. Mask `plymouth-quit.service` and `plymouth-quit-wait.service` so Plymouth persists across all backend boot waves.
5. In `backend/modules/qt6_gui/main.py`, issue `plymouth quit --retain-splash` immediately before EGLFS window presentation to drop DRM master and flip directly into Qt6 GUI.

**Tech Stack:** GRUB2 (`00_header`, `10_linux`, `/etc/default/grub`), Plymouth (bgrt/spinner), systemd, Linux DRM/KMS, PyQt6 (EGLFS), Bash.

**Spec:** HP Omni 10 Platform Fix Specification (`packaging/hardware_fixes/fix_omni10.sh`).

## Global Constraints

- Must be managed entirely within `packaging/hardware_fixes/fix_omni10.sh` and `backend/modules/qt6_gui/main.py`.
- No disruption on normal desktop X11/Wayland/Windows development machines (safe runtime checks).
- Re-running `fix_omni10.sh` must be idempotent.

---

### Task 1: GRUB Silent Boot & Zero Timeout in `fix_omni10.sh`

**Files:**
- Modify: `packaging/hardware_fixes/fix_omni10.sh`
- Test: `tests/test_omni10_grub_fixes.py`

**Interfaces:**
- Consumes: `/etc/default/grub`, `/etc/grub.d/10_linux`
- Produces: Zero-timeout GRUB configuration that silences `Loading initial ramdisk ...`

- [ ] **Step 1: Write test verifying GRUB config rules**

```python
import re

def test_grub_config_logic():
    sample_grub = (
        'GRUB_DEFAULT=0\n'
        'GRUB_TIMEOUT_STYLE=hidden\n'
        'GRUB_TIMEOUT=10\n'
        'GRUB_CMDLINE_LINUX_DEFAULT="quiet splash"\n'
    )
    # Validate timeout replace/insert
    if "GRUB_RECORDFAIL_TIMEOUT" not in sample_grub:
        sample_grub += "GRUB_RECORDFAIL_TIMEOUT=0\n"
    sample_grub = re.sub(r"^GRUB_TIMEOUT=.*", "GRUB_TIMEOUT=0", sample_grub, flags=re.M)
    assert "GRUB_RECORDFAIL_TIMEOUT=0" in sample_grub
    assert "GRUB_TIMEOUT=0" in sample_grub
```

- [ ] **Step 2: Run test to verify it passes**

Run: `micromamba run -n NemoHeadUnit-Wireless pytest tests/test_omni10_grub_fixes.py -v`

- [ ] **Step 3: Update `packaging/hardware_fixes/fix_omni10.sh`**

Add to Fix 2 in `fix_omni10.sh`:
- Set `GRUB_TIMEOUT=0`, `GRUB_TIMEOUT_STYLE=hidden`, `GRUB_RECORDFAIL_TIMEOUT=0`.
- Append kernel parameters: `vt.global_cursor_default=0 loglevel=3 rd.systemd.show_status=false systemd.show_status=false`.
- Silence `Loading Linux ...` and `Loading initial ramdisk ...` in `/etc/grub.d/10_linux`:
```bash
if [ -f "/etc/grub.d/10_linux" ]; then
    if grep -q "echo.*echo \"\$message\"" /etc/grub.d/10_linux; then
        sed -i 's/^[ \t]*echo[ \t]*\x27\$(echo "\$message"/    # echo \x27\$(echo "\$message"/g' /etc/grub.d/10_linux
        GRUB_CHANGED=1
    fi
fi
```
- Mask early `plymouth-quit.service`:
```bash
systemctl mask plymouth-quit.service plymouth-quit-wait.service >/dev/null 2>&1 || true
```

- [ ] **Step 4: Run test to verify**

Run: `micromamba run -n NemoHeadUnit-Wireless pytest tests/test_omni10_grub_fixes.py -v`

- [ ] **Step 5: Commit**

```bash
git add packaging/hardware_fixes/fix_omni10.sh tests/test_omni10_grub_fixes.py
git commit -m "fix(omni10): silence GRUB ramdisk message, zero recordfail timeout, and mask plymouth-quit"
```

---

### Task 2: Plymouth Retain-Splash Dismissal in `qt6_gui`

**Files:**
- Modify: `backend/modules/qt6_gui/main.py`
- Test: `tests/test_plymouth_handoff.py`

**Interfaces:**
- Consumes: `/usr/bin/plymouth`
- Produces: Seamless handoff dropping DRM master while keeping scanout framebuffer intact

- [ ] **Step 1: Write test for `dismiss_boot_splash()`**

```python
from unittest.mock import patch
from backend.modules.qt6_gui.main import dismiss_boot_splash

def test_dismiss_boot_splash():
    with patch("shutil.which", return_value="/usr/bin/plymouth"), \
         patch("subprocess.run") as mock_run:
        dismiss_boot_splash()
        mock_run.assert_called_once_with(
            ["plymouth", "quit", "--retain-splash"],
            check=False,
            timeout=1.5,
        )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `micromamba run -n NemoHeadUnit-Wireless pytest tests/test_plymouth_handoff.py -v`
Expected: FAIL (dismiss_boot_splash not implemented)

- [ ] **Step 3: Implement `dismiss_boot_splash()` in `backend/modules/qt6_gui/main.py`**

```python
def dismiss_boot_splash() -> None:
    if shutil.which("plymouth"):
        try:
            subprocess.run(["plymouth", "quit", "--retain-splash"], check=False, timeout=1.5)
        except Exception:
            pass
```
Call `dismiss_boot_splash()` inside `Qt6GuiModule.setup()` immediately before `main_window.set_fullscreen(is_fs)`.

- [ ] **Step 4: Run test to verify it passes**

Run: `micromamba run -n NemoHeadUnit-Wireless pytest tests/test_plymouth_handoff.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/modules/qt6_gui/main.py tests/test_plymouth_handoff.py
git commit -m "feat(qt6_gui): release Plymouth boot splash with retain-splash before EGLFS window display"
```
