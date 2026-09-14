# Patch `soc_button_array` for Nested Threaded Interrupts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Patch `soc_button_array` so it directly handles nested threaded PMIC interrupts using `request_threaded_irq` and `gpiod_get_value_cansleep` instead of delegating to `gpio-keys`, allowing volume buttons on the HP Omni 10 to function reliably on 100% stock OEM BIOS without any DSDT override.

**Architecture:** 
The standard Linux `soc_button_array` driver instantiates `gpio-keys` platform devices for all ACPI-defined buttons. However, `gpio-keys` uses a hardirq handler (`gpio_keys_gpio_isr`) and is fundamentally incompatible with I2C-backed PMICs like Intel Crystal Cove, which use nested threaded interrupts (`handle_nested_irq`).
We patch `soc_button_array` to detect nested threaded GPIO lines (specifically the Crystal Cove PMIC on the HP Omni 10). For these buttons, `soc_button_array` bypasses `gpio-keys`, sets `FCOT=1` in PMIC register 0x52, guards unmapped PMIC lines against the upstream `handle_nested_irq(0)` kernel crash, and attaches a native threaded IRQ handler that safely reads GPIO states with `gpiod_get_value_cansleep()` and emits input events.

**Tech Stack:** C (Linux Kernel Module, ACPI, GPIO Subsystem, Input Subsystem), DKMS, Bash, evtest.

**Spec:** [`PMIC_VOL_BUTTON_INVESTIGATION.md`](file:///home/nemo/.gemini/antigravity-cli/brain/31fe28e4-7297-4306-9011-b3f5cca7bf32/PMIC_VOL_BUTTON_INVESTIGATION.md)

## Global Constraints

- Must work on factory OEM DSDT without any `/boot/dsdt_override.img` or ACPI table modification.
- Must preserve existing Power (GPO2 pin 16) and Home (GPO0 pin 6) buttons via standard `gpio-keys`.
- Must guard Crystal Cove PMIC lines 2–15 against the upstream `gpio-crystalcove.c` NULL pointer dereference (`handle_nested_irq(0)`).
- Must enable hardware button panel in Crystal Cove register `0x52` (`FCOT=1`) via virtual GPIO `0x5e`.
- Code must be packaged as a drop-in DKMS module replacing or overriding in-tree `soc_button_array.ko`.

---

### Task 1: Create Dedicated Nested Threaded Handler Structure in `soc_button_array.c`

**Files:**
- Modify: `/home/nemo/hp-omni-button-fix/soc_button_array.c`
- Test: `/tmp/soc_test/Makefile`

**Interfaces:**
- Produces: `struct soc_nested_button_data`, `soc_nested_button_thread()`, `soc_create_nested_buttons()`, `soc_destroy_nested_buttons()`
- Consumes: `<linux/gpio/driver.h>`, `<linux/interrupt.h>`, `<linux/input.h>`

- [ ] **Step 1: Write the nested threaded handler definition and data structures**

In `soc_button_array.c`, add the structure to track buttons managed directly via threaded IRQs:

```c
struct soc_nested_button {
	int gpio;
	int virq;
	unsigned int code;
	struct gpio_desc *desc;
	struct input_dev *input;
};

struct soc_nested_button_data {
	struct input_dev *input;
	int num_buttons;
	struct soc_nested_button buttons[4];
};

static irqreturn_t soc_nested_button_thread(int irq, void *data)
{
	struct soc_nested_button *btn = data;
	int val;

	if (!btn || !btn->desc || !btn->input)
		return IRQ_HANDLED;

	val = gpiod_get_value_cansleep(btn->desc);
	if (val < 0)
		return IRQ_HANDLED;

	/* Active-low button logic: 0 = pressed (report 1), 1 = released (report 0) */
	input_report_key(btn->input, btn->code, val == 0 ? 1 : 0);
	input_sync(btn->input);

	return IRQ_HANDLED;
}
```

- [ ] **Step 2: Implement `soc_create_nested_buttons` and `soc_destroy_nested_buttons`**

```c
static struct soc_nested_button_data *soc_create_nested_buttons(struct platform_device *pdev)
{
	struct soc_nested_button_data *nested;
	struct input_dev *input;
	int error;

	nested = devm_kzalloc(&pdev->dev, sizeof(*nested), GFP_KERNEL);
	if (!nested)
		return ERR_PTR(-ENOMEM);

	input = devm_input_allocate_device(&pdev->dev);
	if (!input)
		return ERR_PTR(-ENOMEM);

	input->name = "HP Omni 10 PMIC Volume Buttons";
	input->phys = "soc_button_array/nested0";
	input->id.bustype = BUS_HOST;

	input_set_capability(input, EV_KEY, KEY_VOLUMEDOWN);
	input_set_capability(input, EV_KEY, KEY_VOLUMEUP);
	__set_bit(EV_REP, input->evbit);

	error = input_register_device(input);
	if (error) {
		dev_err(&pdev->dev, "Failed to register nested input device: %d\n", error);
		return ERR_PTR(error);
	}

	nested->input = input;
	return nested;
}

static void soc_destroy_nested_buttons(struct platform_device *pdev, struct soc_nested_button_data *nested)
{
	int i;

	if (!nested)
		return;

	for (i = 0; i < nested->num_buttons; i++) {
		if (nested->buttons[i].virq > 0)
			free_irq(nested->buttons[i].virq, &nested->buttons[i]);
		if (nested->buttons[i].desc)
			gpiod_put(nested->buttons[i].desc);
	}
}
```

- [ ] **Step 3: Compile check out-of-tree against target kernel**

Run: `make -C /usr/lib/modules/$(uname -r)/build M=/home/nemo/hp-omni-button-fix modules`
Expected: Clean compile, producing `soc_button_array.o`.

- [ ] **Step 4: Commit**

```bash
git add soc_button_array.c
git commit -m "feat: implement nested threaded IRQ button handler in soc_button_array"
```

---

### Task 2: Implement PMIC Panel Enablement and Crash Guard for HP Omni 10

**Files:**
- Modify: `/home/nemo/hp-omni-button-fix/soc_button_array.c`

**Interfaces:**
- Produces: `dmi_hp_omni10`, `soc_hp_omni10_init_pmic()`, `soc_hp_omni10_teardown_pmic()`
- Consumes: `<linux/dmi.h>`, `gpiod_to_chip()`, `gpio_device_find_by_label()`

- [ ] **Step 1: Define DMI Match Table and Dummy Handler for Crystal Cove Crash Guard**

```c
static const struct dmi_system_id dmi_hp_omni10[] = {
	{
		.matches = {
			DMI_MATCH(DMI_SYS_VENDOR, "Hewlett-Packard"),
			DMI_MATCH(DMI_PRODUCT_NAME, "HP Omni10"),
		},
	},
	{
		.matches = {
			DMI_MATCH(DMI_SYS_VENDOR, "Hewlett-Packard"),
			DMI_MATCH(DMI_PRODUCT_NAME, "HP Omni 10"),
		},
	},
	{}
};

struct omni_pmic_guard {
	int virq[16];
};
static struct omni_pmic_guard *g_guard;

static irqreturn_t omni_pmic_dummy_handler(int irq, void *data)
{
	return IRQ_HANDLED;
}
```

- [ ] **Step 2: Implement `soc_hp_omni10_init_pmic`**

```c
static int soc_hp_omni10_init_pmic(struct device *dev)
{
	struct gpio_desc *desc;
	struct gpio_chip *chip;
	struct gpio_device *gdev;
	int i, virq;

	if (!dmi_check_system(dmi_hp_omni10))
		return 0;

	/* 1. Turn on FCOT in PMIC register 0x52 (GPIOPANELCTL) via virtual GPIO 0x5e */
	desc = gpiod_get_index(dev, NULL, 2, GPIOD_ASIS);
	if (!IS_ERR(desc)) {
		chip = gpiod_to_chip(desc);
		if (chip && chip->set) {
			chip->set(chip, 0x5e, 1);
			dev_info(dev, "HP Omni 10: Crystal Cove PMIC button panel enabled (FCOT=1)\n");
		}
		gpiod_put(desc);
	}

	/* 2. Guard remaining Crystal Cove GPIOs (2..15) against handle_nested_irq(0) upstream bug */
	g_guard = devm_kzalloc(dev, sizeof(*g_guard), GFP_KERNEL);
	if (!g_guard)
		return 0;

	gdev = gpio_device_find_by_label("gpio_crystalcove");
	if (gdev) {
		for (i = 2; i < 16; i++) {
			struct gpio_desc *d = gpio_device_get_desc(gdev, i);
			if (d) {
				virq = gpiod_to_irq(d);
				if (virq > 0) {
					if (request_threaded_irq(virq, NULL, omni_pmic_dummy_handler,
								IRQF_ONESHOT | IRQF_SHARED,
								"omni_pmic_guard", g_guard) == 0) {
						g_guard->virq[i] = virq;
					}
				}
			}
		}
		gpio_device_put(gdev);
	}

	return 0;
}

static void soc_hp_omni10_teardown_pmic(struct device *dev)
{
	int i;
	if (!g_guard)
		return;

	for (i = 2; i < 16; i++) {
		if (g_guard->virq[i] > 0)
			free_irq(g_guard->virq[i], g_guard);
	}
}
```

- [ ] **Step 3: Compile check out-of-tree**

Run: `make -C /usr/lib/modules/$(uname -r)/build M=/home/nemo/hp-omni-button-fix modules`
Expected: Success with no warnings.

- [ ] **Step 4: Commit**

```bash
git add soc_button_array.c
git commit -m "feat: add PMIC FCOT activation and Crystal Cove crash guard"
```

---

### Task 3: Route PMIC Buttons Away From `gpio-keys` to the Threaded Handler

**Files:**
- Modify: `/home/nemo/hp-omni-button-fix/soc_button_array.c`

**Interfaces:**
- Produces: Integrated `soc_button_probe()`, `soc_button_remove()`
- Consumes: `soc_create_nested_buttons()`, `soc_hp_omni10_init_pmic()`

- [ ] **Step 1: Modify `soc_button_data` to hold nested device pointer**

```c
struct soc_button_data {
	struct platform_device *children[BUTTON_TYPES];
	struct soc_nested_button_data *nested;
};
```

- [ ] **Step 2: Filter out PMIC Volume buttons in `soc_button_device_create` when on Omni 10**

In `soc_button_device_create`:
```c
	for (info = button_info; info->name; info++) {
		if (info->autorepeat != autorepeat)
			continue;

		if (info->acpi_index == invalid_acpi_index)
			continue;

		/* On HP Omni 10, volume buttons (indices 2 & 3) are handled directly via threaded IRQs */
		if (dmi_check_system(dmi_hp_omni10) &&
		    (info->acpi_index == 2 || info->acpi_index == 3))
			continue;
```

- [ ] **Step 3: Wire nested button creation in `soc_button_probe`**

In `soc_button_probe`:
```c
	if (dmi_check_system(dmi_hp_omni10)) {
		struct soc_nested_button_data *nested;
		soc_hp_omni10_init_pmic(dev);

		nested = soc_create_nested_buttons(pdev);
		if (!IS_ERR(nested)) {
			int indices[2] = { 3, 2 }; /* 3 = Vol Down, 2 = Vol Up */
			unsigned int codes[2] = { KEY_VOLUMEDOWN, KEY_VOLUMEUP };
			int idx;

			for (idx = 0; idx < 2; idx++) {
				struct gpio_desc *desc = gpiod_get_index(dev, NULL, indices[idx], GPIOD_IN);
				if (!IS_ERR(desc)) {
					int virq = gpiod_to_irq(desc);
					if (virq > 0) {
						int b_idx = nested->num_buttons;
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
							dev_info(dev, "Bound threaded button %s (virq %d)\n",
								 codes[idx] == KEY_VOLUMEUP ? "Vol Up" : "Vol Down",
								 virq);
						}
					}
				}
			}
			priv->nested = nested;
		}
	}
```

- [ ] **Step 4: Update `soc_button_remove`**

```c
static void soc_button_remove(struct platform_device *pdev)
{
	struct soc_button_data *priv = platform_get_drvdata(pdev);
	int i;

	if (priv->nested)
		soc_destroy_nested_buttons(pdev, priv->nested);

	if (dmi_check_system(dmi_hp_omni10))
		soc_hp_omni10_teardown_pmic(&pdev->dev);

	for (i = 0; i < BUTTON_TYPES; i++)
		if (priv->children[i])
			platform_device_unregister(priv->children[i]);
}
```

- [ ] **Step 5: Compile and verify build artifacts**

Run: `make -C /usr/lib/modules/$(uname -r)/build M=/home/nemo/hp-omni-button-fix modules`
Expected: `soc_button_array.ko` builds without error.

- [ ] **Step 6: Commit**

```bash
git add soc_button_array.c
git commit -m "feat: complete split of nested PMIC volume buttons from gpio-keys in soc_button_array"
```

---

### Task 4: Deploy and Verify on Tablet 2 (`192.168.1.105`) with Factory DSDT

**Files:**
- Deploy to: `nemo@192.168.1.105:/tmp/soc_button_array.ko`
- Test on: Tablet 2 physical hardware

- [ ] **Step 1: Remove DSDT override from Tablet 2 to restore 100% factory BIOS**

```bash
sshpass -p 2532378 ssh -o StrictHostKeyChecking=no nemo@192.168.1.105 "
echo 2532378 | sudo -S rm -f /boot/dsdt_override.img
echo 2532378 | sudo -S grub-mkconfig -o /boot/grub/grub.cfg
"
```

- [ ] **Step 2: Copy and load patched `soc_button_array.ko`**

```bash
sshpass -p 2532378 scp -o StrictHostKeyChecking=no /home/nemo/hp-omni-button-fix/soc_button_array.ko nemo@192.168.1.105:/tmp/soc_button_array.ko
sshpass -p 2532378 ssh -o StrictHostKeyChecking=no nemo@192.168.1.105 "
echo 2532378 | sudo -S rmmod soc_button_array 2>/dev/null || true
echo 2532378 | sudo -S insmod /tmp/soc_button_array.ko
"
```

- [ ] **Step 3: Inspect kernel logs on Tablet 2**

Run:
```bash
sshpass -p 2532378 ssh -o StrictHostKeyChecking=no nemo@192.168.1.105 "echo 2532378 | sudo -S dmesg | tail -n 20"
```
Expected output:
- `HP Omni 10: Crystal Cove PMIC button panel enabled (FCOT=1)`
- `Bound threaded button Vol Up (virq ...)`
- `Bound threaded button Vol Down (virq ...)`
- `input: gpio-keys as ... (Power & Home)`

- [ ] **Step 4: Physical evtest verification on Tablet 2**

Run interactive evtest on the created input device:
```bash
sshpass -p 2532378 ssh -o StrictHostKeyChecking=no nemo@192.168.1.105 "echo 2532378 | sudo -S evtest"
```
Verify that:
- Pressing Volume Up emits `KEY_VOLUMEUP` (value 1 then 0).
- Pressing Volume Down emits `KEY_VOLUMEDOWN` (value 1 then 0).
- Pressing Home emits `KEY_LEFTMETA`.
- Pressing Power emits `KEY_POWER`.

- [ ] **Step 5: Commit verified driver**

```bash
git add soc_button_array.c
git commit -m "test: verify patched soc_button_array on physical HP Omni 10 stock BIOS"
```

---

### Task 5: Package as DKMS and Update `fix_omni10.sh`

**Files:**
- Modify: `/home/nemo/NemoHeadUnit-Wireless/packaging/hardware_fixes/fix_omni10.sh`
- Modify: `/home/nemo/hp-omni-button-fix/dkms.conf`
- Modify: `/home/nemo/hp-omni-button-fix/Makefile`

**Interfaces:**
- Produces: Completely automated single-step fix script without DSDT overrides

- [ ] **Step 1: Update `dkms.conf` and `Makefile` in `hp-omni-button-fix`**

Configure DKMS package name as `soc-button-array-omni` (or override `soc_button_array`).

- [ ] **Step 2: Update `fix_omni10.sh`**

In `fix_omni10.sh`:
- Remove ACPI compilation, iasl disassembly, and `/boot/dsdt_override.img` generation.
- Remove GRUB `initrd` modifications.
- Install and build the patched `soc_button_array` via DKMS to `/updates/soc_button_array.ko`.
- Run `depmod -a`.

- [ ] **Step 3: Test `fix_omni10.sh` idempotency**

Run: `sudo /home/nemo/NemoHeadUnit-Wireless/packaging/hardware_fixes/fix_omni10.sh` on the target unit.
Verify exit code 0 and clean installation.

- [ ] **Step 4: Commit changes in `NemoHeadUnit-Wireless`**

```bash
git add packaging/hardware_fixes/fix_omni10.sh
git commit -m "fix(omni10): replace DSDT override workaround with patched threaded soc_button_array"
```
