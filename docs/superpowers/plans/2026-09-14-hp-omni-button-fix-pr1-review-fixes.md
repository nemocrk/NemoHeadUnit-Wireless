# HP Omni 10 Button Fix PR #1 Review Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Resolve all 8 code review findings for `hp-omni-button-fix` PR #1 by repairing driver lifecycle and devres cleanup on probe failure, preventing GPIO descriptor leaks, supporting kernel versions < 6.7, replacing intrusive debug logs, and hardening installation scripts.

**Architecture:** Refactor `soc_button_array.c` to use `devm_add_action_or_reset()` for `omni_pmic_guard` lifecycle, moving the guard pointer from a global variable into device private data (`soc_button_data`). Encapsulate PMIC register operations in reusable helpers, guard descriptor allocations with proper `gpiod_put()` error handling, add preprocessor `#if LINUX_VERSION_CODE` checks for backward kernel compatibility (<6.7), and correct filesystem operations in `install.sh`.

**Tech Stack:** C (Linux Kernel Driver API, GPIOLIB, Regmap, Input Subsystem), Bash, Make, DKMS.

**Spec:** Review findings from `https://github.com/nemocrk/hp-omni-button-fix/pull/1`:
- `soc_button_array.c:L953: 🔴 bug`: Probe exit without cleanup leaves devm-allocated `g_omni_guard` IRQs registered to freed memory.
- `soc_button_array.c:L912-933: 🟡 risk`: `gpiod_put(desc)` omitted when `virq <= 0` or `request_threaded_irq()` fails.
- `soc_button_array.c:L522: 🟡 risk`: `gpio_device_find_by_label()` requires Linux >= 6.7, breaking builds on 6.1/6.6 LTS kernels.
- `install.sh:L197: 🟡 risk`: Destination directory `/lib/modules/$(uname -r)/updates/` may not exist on non-DKMS fallback.
- `soc_button_array.c:L305-307: 🔵 nit`: `pr_info()` in threaded IRQ handler floods dmesg on every key event.
- `soc_button_array.c:L480: 🔵 nit`: Global static `g_omni_guard` breaks multi-device encapsulation.
- `soc_button_array.c:L509-511: 🔵 nit`: CTLO 0x16 restore duplicated at L942-943.
- `install.sh:L194: 🔵 nit`: `cd src 2>/dev/null || true` is leftover from legacy folder layout.

## Global Constraints

- Driver must compile cleanly against Linux kernel headers (targeting kernels 5.15+, 6.1 LTS, 6.6 LTS, and 6.7+).
- Must adhere strictly to Linux kernel device driver model (no global device state, proper devres teardown order).
- Must preserve 100% compatibility with unmodified OEM ACPI tables on HP Omni 10 (`INTCFD9`).

---

### Task 1: Clean Up `install.sh` and Hardened Directory Creation

**Files:**
- Modify: `install.sh`

**Interfaces:**
- Consumes: Standard Linux shell utilities (`mkdir`, `cp`, `depmod`, `make`, `dkms`).
- Produces: Reliable fallback installation path without dead directory changes.

- [ ] **Step 1: Check existing `install.sh` content**

Verify line 194 (`cd src 2>/dev/null || true`) and line 197 (`cp soc_button_array.ko /lib/modules/$(uname -r)/updates/`).

- [ ] **Step 2: Update `install.sh`**

Remove `cd src 2>/dev/null || true` and ensure `mkdir -p` is executed before copying to the `updates/` module directory:

```bash
    echo "DKMS not found. Building and installing module directly..."
    make -C /lib/modules/$(uname -r)/build M=$(pwd) modules
    mkdir -p /lib/modules/$(uname -r)/updates/
    cp soc_button_array.ko /lib/modules/$(uname -r)/updates/
    depmod -a
```

- [ ] **Step 3: Verify script syntax**

Run: `bash -n install.sh`
Expected: Exits with 0 (valid bash syntax).

- [ ] **Step 4: Commit**

```bash
git add install.sh
git commit -m "fix(install): ensure updates directory exists and remove legacy cd src"
```

---

### Task 2: Encapsulate PMIC CTLO Register Restore in Helper Function

**Files:**
- Modify: `soc_button_array.c`

**Interfaces:**
- Consumes: `struct gpio_chip *chip`, `struct device *dev`, `regmap_write()`.
- Produces: `static void soc_hp_omni10_restore_ctlo(struct gpio_chip *chip, struct device *dev)`.

- [ ] **Step 1: Define `soc_hp_omni10_restore_ctlo`**

In `soc_button_array.c`, define the shared helper function above `soc_hp_omni10_init_pmic`:

```c
static void soc_hp_omni10_restore_ctlo(struct gpio_chip *chip, struct device *dev)
{
	struct regmap *regmap;

	if (!chip || !chip->parent)
		return;

	regmap = dev_get_regmap(chip->parent, NULL);
	if (regmap) {
		/* Restore BIOS Open-Drain 50k pull-up settings for Vol Down (0x2b) and Vol Up (0x2c) */
		regmap_write(regmap, 0x2b, 0x16);
		regmap_write(regmap, 0x2c, 0x16);
		dev_info(dev, "HP Omni 10: restored PMIC GPIO 0 & 1 CTLO to 0x16 (Open-Drain, 50k pull-up)\n");
	}
}
```

- [ ] **Step 2: Replace duplicated call sites**

Replace lines 505-513 and lines 937-947 with calls to `soc_hp_omni10_restore_ctlo(chip, dev)`.

- [ ] **Step 3: Verify syntax**

Run: `gcc -fsyntax-only -I/usr/include soc_button_array.c 2>&1 || true`
Verify no syntax errors in `soc_hp_omni10_restore_ctlo`.

- [ ] **Step 4: Commit**

```bash
git add soc_button_array.c
git commit -m "refactor: extract PMIC CTLO restore logic into helper function"
```

---

### Task 3: Demote IRQ Handler Log to `dev_dbg` and Silence Spam

**Files:**
- Modify: `soc_button_array.c`

**Interfaces:**
- Consumes: `btn->input->dev`
- Produces: Non-spammy event delivery without `pr_info` on each interrupt.

- [ ] **Step 1: Locate `pr_info` in `soc_nested_button_thread`**

Lines 305-308:
```c
	pr_info("soc_button_array: *** Button %s %s (val=%d, virq=%d) ***\n",
		btn->code == KEY_VOLUMEUP ? "Vol Up" : "Vol Down",
		val == 0 ? "Pressed" : "Released", val, btn->virq);
```

- [ ] **Step 2: Replace with `dev_dbg`**

```c
	dev_dbg(&btn->input->dev, "button %s %s (val=%d, virq=%d)\n",
		btn->code == KEY_VOLUMEUP ? "Vol Up" : "Vol Down",
		val == 0 ? "Pressed" : "Released", val, btn->virq);
```

- [ ] **Step 3: Verify syntax**

Run: `gcc -fsyntax-only -I/usr/include soc_button_array.c 2>&1 || true`

- [ ] **Step 4: Commit**

```bash
git add soc_button_array.c
git commit -m "fix(logging): demote threaded button IRQ log from pr_info to dev_dbg"
```

---

### Task 4: Fix GPIO Descriptor Leaks on Threaded Button Init

**Files:**
- Modify: `soc_button_array.c`

**Interfaces:**
- Consumes: `gpiod_get_index()`, `gpiod_to_irq()`, `request_threaded_irq()`, `gpiod_put()`.
- Produces: Safe error unwinding where every `gpiod_get_index()` is matched with a `gpiod_put()` on failure.

- [ ] **Step 1: Locate loop in `soc_button_probe` (lines 908-935)**

Notice `gpiod_put(desc)` is skipped if `virq <= 0` or if `request_threaded_irq()` returns non-zero.

- [ ] **Step 2: Add cleanup on error paths**

Update the loop to:

```c
			for (idx = 0; idx < 2; idx++) {
				struct gpio_desc *desc = gpiod_get_index(dev, NULL, indices[idx], GPIOD_ASIS);
				int virq;
				int b_idx;

				if (IS_ERR(desc))
					continue;

				virq = gpiod_to_irq(desc);
				if (virq <= 0) {
					gpiod_put(desc);
					continue;
				}

				b_idx = nested->num_buttons;
				nested->buttons[b_idx].desc = desc;
				nested->buttons[b_idx].virq = virq;
				nested->buttons[b_idx].code = codes[idx];
				nested->buttons[b_idx].input = nested->input;

				error = request_threaded_irq(virq, NULL,
							     soc_nested_button_thread,
							     IRQF_ONESHOT | IRQF_SHARED,
							     "omni_vol_btn",
							     &nested->buttons[b_idx]);
				if (error == 0) {
					nested->num_buttons++;
					dev_info(dev, "HP Omni 10: bound threaded button %s (gpio %d, virq %d)\n",
						 codes[idx] == KEY_VOLUMEUP ? "Vol Up" : "Vol Down",
						 desc_to_gpio(desc), virq);
				} else {
					dev_err(dev, "HP Omni 10: failed to request threaded IRQ %d: %d\n", virq, error);
					nested->buttons[b_idx].desc = NULL;
					gpiod_put(desc);
				}
			}
```

- [ ] **Step 3: Verify syntax**

Run: `gcc -fsyntax-only -I/usr/include soc_button_array.c 2>&1 || true`

- [ ] **Step 4: Commit**

```bash
git add soc_button_array.c
git commit -m "fix: free GPIO descriptor on IRQ resolution and request failures"
```

---

### Task 5: Eliminate Global State and Fix Probe Teardown Bug with `devm_add_action_or_reset`

**Files:**
- Modify: `soc_button_array.c`

**Interfaces:**
- Consumes: `devm_add_action_or_reset()`, `struct soc_button_data`, `free_irq()`.
- Produces: Memory-safe PMIC guard cleanup guaranteed even if `probe()` fails before returning.

- [ ] **Step 1: Move guard into `struct soc_button_data` and remove static global**

Remove `static struct omni_pmic_guard *g_omni_guard;`.
Add `struct omni_pmic_guard *omni_guard;` to `struct soc_button_data`.

- [ ] **Step 2: Implement `devm` teardown action for PMIC guard**

```c
static void soc_hp_omni10_teardown_pmic_action(void *data)
{
	struct omni_pmic_guard *guard = data;
	int i;

	if (!guard)
		return;

	for (i = 2; i < 16; i++) {
		if (guard->virq[i] > 0)
			free_irq(guard->virq[i], guard);
	}
}
```

- [ ] **Step 3: Register devm action in `soc_hp_omni10_init_pmic`**

Update `soc_hp_omni10_init_pmic(struct device *dev, struct soc_button_data *priv)`:

```c
	priv->omni_guard = devm_kzalloc(dev, sizeof(*priv->omni_guard), GFP_KERNEL);
	if (!priv->omni_guard)
		return -ENOMEM;

	int error = devm_add_action_or_reset(dev, soc_hp_omni10_teardown_pmic_action, priv->omni_guard);
	if (error)
		return error;
```

Update lines requesting the guard IRQs to pass `priv->omni_guard`.

- [ ] **Step 4: Clean up `soc_button_remove`**

Remove manual call to `soc_hp_omni10_teardown_pmic` from `soc_button_remove` since `devm` executes it automatically upon unbind.

- [ ] **Step 5: Verify syntax**

Run: `gcc -fsyntax-only -I/usr/include soc_button_array.c 2>&1 || true`

- [ ] **Step 6: Commit**

```bash
git add soc_button_array.c
git commit -m "fix(driver): encapsulate PMIC guard in priv data and ensure cleanup with devm action"
```

---

### Task 6: Add Kernel Compatibility Fallback for Linux < 6.7

**Files:**
- Modify: `soc_button_array.c`

**Interfaces:**
- Consumes: `<linux/version.h>`, `gpiochip_find()`, `gpiochip_get_desc()`.
- Produces: Dual compatibility for kernel >= 6.7 (`gpio_device_find_by_label`) and kernel < 6.7 (`gpiochip_find`).

- [ ] **Step 1: Include `<linux/version.h>`**

Add `#include <linux/version.h>` to headers in `soc_button_array.c`.

- [ ] **Step 2: Add preprocessor fallback in PMIC guard device discovery**

```c
#if LINUX_VERSION_CODE < KERNEL_VERSION(6, 7, 0)
static int omni_gpiochip_match_label(struct gpio_chip *chip, void *data)
{
	return !strcmp(chip->label, data);
}
#endif
```

And in `soc_hp_omni10_init_pmic`:

```c
#if LINUX_VERSION_CODE >= KERNEL_VERSION(6, 7, 0)
	struct gpio_device *gdev = gpio_device_find_by_label("gpio_crystalcove");
	if (gdev) {
		for (i = 2; i < 16; i++) {
			struct gpio_desc *d = gpio_device_get_desc(gdev, i);
			if (d) {
				virq = gpiod_to_irq(d);
				if (virq > 0) {
					if (request_threaded_irq(virq, NULL, omni_pmic_dummy_handler,
								 IRQF_ONESHOT | IRQF_SHARED,
								 "omni_pmic_guard", priv->omni_guard) == 0) {
						priv->omni_guard->virq[i] = virq;
					}
				}
			}
		}
		gpio_device_put(gdev);
	}
#else
	struct gpio_chip *chip = gpiochip_find("gpio_crystalcove", omni_gpiochip_match_label);
	if (chip) {
		for (i = 2; i < 16; i++) {
			struct gpio_desc *d = gpiochip_get_desc(chip, i);
			if (d) {
				virq = gpiod_to_irq(d);
				if (virq > 0) {
					if (request_threaded_irq(virq, NULL, omni_pmic_dummy_handler,
								 IRQF_ONESHOT | IRQF_SHARED,
								 "omni_pmic_guard", priv->omni_guard) == 0) {
						priv->omni_guard->virq[i] = virq;
					}
				}
			}
		}
	}
#endif
```

- [ ] **Step 3: Verify syntax**

Run: `gcc -fsyntax-only -I/usr/include soc_button_array.c 2>&1 || true`

- [ ] **Step 4: Commit**

```bash
git add soc_button_array.c
git commit -m "feat(compat): add gpiochip_find fallback for Linux kernels < 6.7"
```

---

### Task 7: Full DKMS Build and Sanity Check

**Files:**
- Test: `soc_button_array.c`, `Makefile`, `dkms.conf`, `install.sh`

**Interfaces:**
- Consumes: Kernel build system `make -C /lib/modules/$(uname -r)/build M=$(pwd) modules`
- Produces: Verified compilable kernel module `soc_button_array.ko` without warnings.

- [ ] **Step 1: Check compile prerequisites**

Verify kernel build directory or compile readiness.

- [ ] **Step 2: Build module**

Run: `make` (or `make -C /lib/modules/$(uname -r)/build M=$(pwd) clean && make -C /lib/modules/$(uname -r)/build M=$(pwd) modules` if headers installed).

- [ ] **Step 3: Verify git status and diff**

Run: `git diff main...HEAD`
Ensure all review findings are resolved cleanly with zero leftover debug artifacts.

- [ ] **Step 4: Final commit and branch check**

Ensure all changes are cleanly committed on the review branch.
