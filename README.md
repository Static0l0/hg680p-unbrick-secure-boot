# HG680P Unbrick — Secure-Boot Edition

![status](https://img.shields.io/badge/status-verified%20working-brightgreen)
![SoC](https://img.shields.io/badge/SoC-Amlogic%20S905X-blue)
![secure boot](https://img.shields.io/badge/secure%20boot-ON-red)
![tool](https://img.shields.io/badge/tool-USB%20Burning%20Tool%20v2.x-orange)

Recovery guide for the **Fiberhome HG680P** (IndiHome STB, Amlogic **S905X / GXL**, 2 GB) when it's stuck in **mask ROM / "WorldCup Device"** mode, and the Amlogic USB Burning Tool keeps failing at **1–2%** (DDR init) or **4%** (u-boot hand-off).

The usual "load the .img into USB Burning Tool and press Start" guides don't work on units with **secure boot fused on**. This repo explains why and gives a fix that works: **a one-script patch to the stock firmware image**. You don't need vendor keys, UART or a soldering iron.

> **Status:** ✅ Recovered on a real unit (bricked 2026-10-20, burned to 100% on 2026-10-23 at 00:23 WIB).
> Tested on **one** HG680P. Other units, or other S905X boxes, may differ. Read the whole thing before you burn anything.

---

## TL;DR

Everything before 4% is the **DDR-init stage** (`Romcode/Initialize DDR/...`), and it can die at 1% or 2%. Go by the error code, not the percentage:

| Symptom in USB Burning Tool log | Cause | Fix |
|---|---|---|
| Fails at **1–2%**: `Download DDR_ENC.USB,size: 0!` → `[0x10103005] Read item data failed` | Chip has secure boot, so the tool asks for `USB/DDR_ENC` + `USB/UBOOT_ENC`. The stock image doesn't have them. | Run [`make_secure_image.py`](make_secure_image.py) on the stock image |
| Fails at **1–2%**: `paraMagic:0x3412cdab` / `[0x10105002] ReadPara failed` | DDR init never ran: the BL2 you supplied was rejected (it's plaintext or for another board) | Use the encrypted BL2 from the stock `PARTITION/bootloader` (the script does this) |
| Fails at **1–2%**: `[0x10103003] USB transfer error` (`usbWriteFile ... ret=-116`), or `[0x10105004]` / `[0x10102002]` | DDR blob didn't reach the chip, or init failed on a bad attempt. Happens even with the right image. | Unplug the box's power for 5 s, retry. Close other USB tools, try another port. The working burn failed this way six times before passing. |
| Passes DDR (`paraMagic:0x7856efab`, `ddrCapacity:0x800`), then fails at **4%**: `Detect Device remove timeout` → `[0x10303004] Switch status/Identify/Error` | BL2 and u-boot are **not a matched pair**, so u-boot never starts | Use the u-boot that belongs to that BL2. It's the rest of the same `PARTITION/bootloader` blob (the script does this) |

**The key idea:** the stock firmware's `PARTITION/bootloader` item (1,146,880 bytes) already contains the vendor-signed pair this chip accepts:

```
PARTITION/bootloader
├── [0x00000 – 0x0BFFF]  49,152 B     encrypted BL2   → goes into USB/DDR + USB/DDR_ENC
└── [0x0C000 – end    ]  1,097,728 B  matching u-boot → goes into USB/UBOOT + USB/UBOOT_ENC
```

The stock image just never exposes that pair to the tool's secure-boot path. Split it into the `_ENC` slots and the burn goes to 100%.

---

## Contents

- [Device info](#device-info)
- [Are you in the same situation?](#are-you-in-the-same-situation)
- [Working procedure](#working-procedure)
- [After it boots](#after-it-boots)
- [Why it fails: how the USB burn works](#why-it-fails-how-the-usb-burn-works)
- [Error code cheat sheet](#error-code-cheat-sheet)
- [Bonus: get Armbian/SSH back with a dead eMMC](#bonus-get-armbianssh-back-with-a-dead-emmc)
- [What was tried and ruled out](#what-was-tried-and-ruled-out)
- [How this unit got bricked](#how-this-unit-got-bricked)
- [Resources & credits](#resources--credits)

---

## Device info

| | |
|---|---|
| Board | Fiberhome HG680P, DTB `gxl_p212_2gs` |
| SoC | Amlogic S905X (GXL) |
| RAM | 2 GB (`ddrCapacity:0x800`) |
| Storage | 8 GB eMMC (`8GTF4R`, appears as `/dev/mmcblk2` under Armbian) |
| Secure boot | **Enabled.** Reg `0xc8100228` reads `0xbdfd21bc` (bit `0x10` set). The tool logs `File change to DDR_ENC.USB`. |
| Mask ROM USB ID | `VID_1B8E&PID_C003`, shown as **"WorldCup Device"** or **"GX-CHIP"** |
| Mask ROM identify | `2-4-0-0` (IPL). A running u-boot identifies as `0-7-0-16` (TPL). |
| Stock firmware | atvXperience-based Android **6.0.1** (SDK 23), pre-rooted with SuperSU |

---

## Are you in the same situation?

This guide is for you if **all** of these are true:

1. The box shows nothing on HDMI and enumerates on the PC as **WorldCup Device / GX-CHIP**.
2. The USB Burning Tool log shows `ulValue = 0xbdfd21bc` and/or `File change to DDR_ENC.USB`. That line means secure boot is on.
3. Burning the stock/YouTube image fails during DDR init (**1–2%**) with `[0x10103005]` or `[0x10105002]`, or your custom image gets past DDR and fails at **4%** with `[0x10303004]`.

If the tool logs show `ulValue = 0x00000000` and takes the plain `Download DDR.USB` path, your chip isn't in secure mode. A normal burn guide should work for you.

The tool's logs are in:
`C:\Program Files (x86)\Amlogic\USB_Burning_Tool\log\<YYYY.MM>\<YYYY.MM.DD>\`

---

## Working procedure

### What you need

- Windows PC with **Amlogic USB Burning Tool v2.1.6.8** (the version in the *"ATV FW HG680P USB Burning V2.1"* package). v2.1.6 and v2.2.0 use the same secure-boot logic, so the tool version doesn't fix this.
- The stock firmware **`ATV FW HG680P.img`**, from the same *"ATV FW HG680P USB Burning V2.1"* package. This repo does not host firmware.
- Python 3 (standard library only).
- USB A-to-A cable to the box's OTG port, plus the **12 V DC adapter**.

> ⚠️ **About the stock firmware:** it is pre-rooted, and an init script (`init.fhservice.sh`) starts an **unauthenticated root telnet shell on port 23** (`busybox telnetd -l /system/bin/sh &`). It also includes FTP/TFTP/telemetry-upload scripts. It contains no malware as such, but anyone on your LAN can get a root shell. See [After it boots](#after-it-boots) to close it.

### Step 1: Build the patched image

```bash
python make_secure_image.py "ATV FW HG680P.img" hg680p_secure.img
```

Optional: add a custom boot splash (raw Amlogic logo partition blob). `VERIFY/logo` is regenerated for you:

```bash
python make_secure_image.py "ATV FW HG680P.img" hg680p_secure.img --logo my_logo.bin
```

Expected output: 16 items in, **18 items out**. `USB/DDR` and `USB/DDR_ENC` should both be the same 49,152-byte blob, and `USB/UBOOT` and `USB/UBOOT_ENC` the same 1,097,728-byte blob.

Reference values from the working build:

| Item | Size | sha256 (first 8) |
|---|---|---|
| `USB/DDR` = `USB/DDR_ENC` | 49,152 | `90d5f0dd` |
| `USB/UBOOT` = `USB/UBOOT_ENC` | 1,097,728 | `a9343ac6` |
| `PARTITION/bootloader` (unchanged) | 1,146,880 | `42046269` |
| `dtb/meson1` (unchanged) | 157,696 | `c8615f43` |
| `PARTITION/system` (unchanged) | 960,497,712 | `97cfc5b5` |

If your `bootloader` hash differs, you have a different firmware build. It might still work, but you're off the tested path.

### Step 2: Burn

1. Open USB Burning Tool and **Import** `hg680p_secure.img`.
2. Tick **Erase flash** and **Erase bootloader**. Leave **Reset after success** and **Overwrite key** unticked.
3. Box in mask ROM, USB connected, 12 V connected. Press **Start**.
4. **Expect a few red failures before it works.** On the working run the log shows six failed attempts (USB transfer errors, DDR init and PLL errors) before a clean pass:
   ```
   [00:20:43] [0x10103003] Romcode/Initialize DDR/Download buffer/USB transfer error
   [00:20:53] [0x10105004] Romcode/Initialize DDR/Read initialize status/Error result
   [00:20:56] [0x10102002] Romcode/Initialize DDR/Initialize PLL/USB Control setup error
   [00:23:26] [0x00000000] Burning successfully
   ```
   If it keeps failing at 1–2%, unplug the box's power for about 5 seconds, plug back in and retry. The 4% pause is normal; don't unplug.
5. At 100%: click **Stop**, unplug USB, close the tool.

### Step 3: First boot

Power-cycle with HDMI connected and **the USB cable unplugged**. With USB connected, u-boot can drop into USB-burn mode (`InUsbBurn` / `noSof`) instead of booting. The first boot takes **5–10 minutes**.

If you need to burn again later and the tool fails at `[0x00101004] Erase bootloader/Lowper/Identify/Error`, untick **Erase bootloader** (keep **Erase flash**) and retry. That was the workaround used here; the burn went through after it.

---

## After it boots

### 1. Close the root telnet shell

From a root shell (see below), check whether port 23 is open:

```sh
netstat -ln | grep ':23 '
```

If it is, remove the line that starts telnetd from `init.fhservice.sh` (or stop that script from running), then reboot. *This step wasn't done on the test unit. It's a recommendation based on the firmware audit.*

### 2. Wireless ADB

The stock `build.prop` already has `service.adb.tcp.port=5555`. On this build, tapping *Build* in Settings may not reveal Developer Options. The reliable route:

1. Sideload **Terminal Emulator for Android** (`jackpal.androidterm`). Current Termux needs Android 7+, so it won't install on this Android 6 build. Install from a USB stick with the built-in file manager or through Aptoide TV.
2. In the terminal:
   ```sh
   su
   settings put global development_settings_enabled 1
   settings put global adb_enabled 1
   setprop service.adb.tcp.port 5555
   setprop persist.adb.tcp.port 5555
   stop adbd
   start adbd
   ip -f inet addr show wlan0     # or eth0
   ```
3. From the PC: `adb connect <box-ip>:5555` and accept the prompt on the TV (`ro.adb.secure=1` on stock).

### 3. Debloat over ADB, not in the image

Editing `/system` inside the burn image caused more problems than it solved: Play Store stuck on "pending", Developer Options missing, broken boot hooks. Debloating a working stock install over ADB was clean:

```sh
# remove for user 0 (the APKs stay in /system, so a factory reset or reflash brings them back)
adb shell pm uninstall --user 0 com.atvxperience.webxperience
adb shell pm uninstall --user 0 com.atvxperience.appdrawer
adb shell pm uninstall --user 0 com.cmcm.cleanmaster.tv
adb shell pm uninstall --user 0 iflix.play
adb shell pm uninstall --user 0 com.player.zaltv
adb shell pm uninstall --user 0 news.androidtv.tvapprepo.playstore
adb shell pm uninstall --user 0 com.android.htmlviewer
adb shell pm uninstall --user 0 com.android.dreams.phototable

# faster UI
adb shell settings put global window_animation_scale 0.5
adb shell settings put global transition_animation_scale 0.5
adb shell settings put global animator_duration_scale 0.5
```

**Don't remove Google packages** (GMS, GSF, Play Store, Katniss, Backdrop, Leanback). Removing them broke Play Store downloads here.

To use **ATV Launcher** (`ca.dstudio.atvlauncher.free`, works on Android 6) as home, install it, then disable the stock launcher. This Android 6 build has no `pm set-home-activity`:

```sh
adb shell pm disable-user --user 0 com.google.android.leanbacklauncher
# undo: adb shell pm enable com.google.android.leanbacklauncher
```

**Don't** set `debug.composition.type=gpu`. This box uses `c2d`, and forcing GPU composition gave yellow/green moiré corruption on screen.

---

## Why it fails: how the USB burn works

The USB Burning Tool does this, in order:

```
1. identify           → ROM answers 2-4-0-0 (mask ROM / IPL)
2. read 0xc8100228    → 0xbdfd21bc = secure → switch to *_ENC items
3. send DDR_ENC.USB   → BL2 to 0xd9000000, run it with a 32-byte para
4. read para back     → 0x7856efab + ddrCapacity = DDR OK
                        0x3412cdab (unchanged) = BL2 never ran
5. send UBOOT_ENC.USB → u-boot to 0x0200c000
6. re-run BL2         → BL2 verifies/decrypts u-boot and jumps to it
7. wait for re-enum   → u-boot brings USB back up (TPL, 0-7-0-16)  ← the "4%" step
8. send dtb/meson1    → this DTB's partition table is the BURN table
9. for each PARTITION item: "Initialize partition <name>" → write → VERIFY
```

What went wrong at each stage on this unit:

- **2% (`0x10103005`):** the stock image has no `_ENC` items at all, so the tool logs `Download DDR_ENC.USB,size: 0!`. Early custom images had `_ENC` slots filled with copies of the *plaintext* `@AML` BL2, which the secure ROM refuses.
- **2% (`0x10105002`, para still `0x3412cdab`):** plaintext or wrong-board BL2s. Of every BL2 tested, only the encrypted one (sha `90d5f0dd…`) ever completed DDR init on this chip. The same blob sits at `0x200` in FarelRA's revive `u-boot.bin` and in the first 48 KiB of the stock `PARTITION/bootloader`.
- **4% (`0x10303004`):** with the working BL2 plus a u-boot from another source (Aidan's ROM, the factory boot area, the stock `USB/UBOOT` item, FarelRA's raw FIP), DDR passed every time. Then nothing came back:
  ```
  runResult:0x 0,paraMagic:0x7856efab   ddrCapacity:0x 800    ← DDR OK
  Download UBOOT_ENC.USB buffer at 0x0200c000
  DownCheckPara succeed / DownRunPara succeed
  Run at address 0xd9000000
  Detect Device remove timeout                                ← u-boot never starts
  [0x10303004] Romcode/Switch status/Identify/Error result
  ```
  BL2 and u-boot are encrypted/signed as a pair. If the BL2 can't verify the u-boot, the CPU just halts: no error, no USB, no HDMI. Swapping in "the right HG680P u-boot" from somewhere else doesn't help. It has to be **the** u-boot that shipped next to that BL2.
- **After 4%:** partition names are looked up in the image's `dtb/meson1`. The stock DTB only defines `boot / bootloader / logo / recovery / system` (no `odm`, `vendor`, `product`, `dtbo`, `vbmeta`). That's why porting an Android 9 ROM onto the stock bootloader died at 9–10% with `[0x30402004] UBOOT/Partition odm/Initialize partition/Error result`, however the payload was sized or flagged.

---

## Error code cheat sheet

From this unit's logs, tool v2.1.6.8.

| Code | Text | Meaning here |
|---|---|---|
| `0x10103005` | `Romcode/Initialize DDR/Download buffer/Read item data failed` | `DDR_ENC` item missing or empty (secure chip, stock image). Also caused by an image builder that zeroed item descriptors. |
| `0x10105002` | `Romcode/Initialize DDR/Read initialize status/USB Control setup error` (`ReadPara failed`) | BL2 rejected; para still reads `0x3412cdab` |
| `0x10103003` | `Romcode/Initialize DDR/Download buffer/USB transfer error` | USB link flake. Power-cycle and retry. |
| `0x10101002` | `Romcode/Initialize DDR/Identify/USB Control setup error` | Device wedged, often by another program holding it. Close it and power-cycle. |
| `0x10303004` | `Romcode/Switch status/Identify/Error result` | **The 4% wall.** BL2/u-boot pair mismatch. |
| `0x30402004` | `UBOOT/Partition <name>/Initialize partition/Error result` | `<name>` not in the image's `dtb/meson1` partition table |
| `0x30403003` | `UBOOT/Partition system/Download buffer/Data tranfer error` | Raw 1 GiB system image; repack it as a sparse image |
| `0x30405004` | `UBOOT/Partition system/Verify patition/Error result` (at 98%) | `VERIFY/<name>` must be `sha1sum <sha1 of the payload as stored>`, i.e. of the **sparse** file, not the expanded image |
| `0x00101004` | `Erase bootloader/Lowper/Identify/Error result` | Seen when re-burning a box that already boots. Untick *Erase bootloader*. |

---

## Bonus: get Armbian/SSH back with a dead eMMC

This isn't needed for the fix above, but it's how this unit stayed accessible while the eMMC bootloader was dead. It's based on FarelRA's *Revive Dead eMMC / Corrupt Bootloader* method.

On an Armbian SD card:

| Location | Content |
|---|---|
| Sector 0 | **Keep the original MBR** (`55 AA`). Writing a bootloader at offset 0 destroys it, and that's why the first SD attempt failed. |
| Offset `0x200` (sector 1) | FarelRA's `revive-dead-emmc-bootloader/u-boot.bin`, copied from its `0x200` onward. The ROM accepts it because its BL2 is the vendor-encrypted one. |
| FAT `aml_autoscript` | FarelRA's 173-byte script: `if printenv bootfromsd; then exit; fi; if fatload mmc 0 0x1000000 u-boot.ext; then go 0x1000000; fi;` |
| FAT `u-boot.ext` | Mainline p212 u-boot (`u-boot-p212.bin` from the Armbian image) |
| FAT `extlinux/extlinux.conf` | Enabled, `fdt /dtb/amlogic/meson-gxl-s905x-p212.dtb` |

Boot with the card in → mask ROM loads the SD's hidden-sector u-boot → `aml_autoscript` → mainline u-boot → Armbian → SSH. From there the eMMC (`/dev/mmcblk2`) can be read and written with `dd`.

**Caveats seen here:**
- Once the eMMC has a *valid-looking* bootloader again, the ROM prefers it over the SD. A half-written eMMC boot area blocks SD fallback completely, while a fully blank one does not.
- **Back up the first 4 MiB of eMMC before you touch anything:**
  `dd if=/dev/mmcblk2 of=bootarea_before.bin bs=1M count=4`
- On secure units, writing the factory 4 MiB boot area back from Armbian did **not** make Android boot here (black screen, with various layouts and with `mmc bootpart` / `PARTITION_CONFIG` set to `0x00` and `0x10`). The USB burn above is what actually fixed it.

---

## What was tried and ruled out

| Approach | Result |
|---|---|
| **Manual DDR init with pyamlboot / `optimus.py`** | Dead end. About 40 combinations of 3 BL2s × para shapes (6-word vs the 8-word GXL para) × 8 run addresses (incl. `+0x1000` past the `@AML` header) × para placement/order × 32 B vs `0x200` writes. Never got the `0x7856efab` ack. The official tool with the right encrypted BL2 runs it at `0xd9000000` with no tricks, so the `+0x1000` theory only applies to plaintext containers. |
| **Patching the `ddrpara` struct at `0x9800` in the DDR blob** (idea from 3F/aml_s905_uboot) | Investigated: the Aidan blob has a build string (`xl 3e531ea - luan.yuan`) there, the stock blob has zeros. **Not the fix.** DDR init only started working once the *encrypted* BL2 went into `DDR_ENC`. |
| **Re-signing u-boot with `aml_encrypt_gxl`** | Not possible without vendor keys (`--keysig` rc=104, `--bootsig` rc=25, self-generated keys don't match the fused root key) |
| **3F/aml_s905_uboot reference 2G pair** | That repo is **GXB (S905)**, not GXL (S905X). Wrong SoC. |
| **Aidan's ROM v9 (ATV9) bootloader** | Its boot chain is non-secure `@AML`/AMLC, so the secure ROM rejects it |
| **Aidan ATV9 system on the stock secure bootloader** (via USB tool) | Burn dies at 9–10%: stock DTB has no `dtbo`/`odm`/`vendor`/`product`. Resizing payloads or changing the sparse flag doesn't help (it's a name lookup). |
| **Aidan ATV9 on the stock bootloader** (raw eMMC writes, boot header v1→v0, `root=p14`) | No HDMI signal. A 22-entry MPT keeping stock `boot`/`system` offsets was designed but **never tested**. |
| **Writing the boot area directly to eMMC from Armbian** (factory dump, raw `+0x200` layout, `aml_sdc_burn` item, `mmcblk2boot0/1`) | No HDMI signal on this unit |
| **Old tool version (v2.1.6 / 2.0.7.2)** | Same `DDR_ENC` logic as v2.1.6.8 and v2.2.0 |
| **eMMC CLK-to-GND short** | Suggested, never needed |
| **Baking debloat/ADB/launcher into `/system` of the burn image** | Burns fine once the system image is sparse and VERIFY matches, but caused Play Store "pending", missing Developer Options, and a non-executable (`0644`) boot hook. Debloating over ADB afterwards was cleaner. |

---

## How this unit got bricked

1. The box was running Armbian from SD (chain-loaded by the eMMC's factory u-boot).
2. A custom Android ROM (Aidan's ROM v9, sanitized, with a custom splash) was written partition-by-partition to the eMMC over SSH. It bootlooped, because the burn flow also writes the u-boot env and `param`, which a raw write skips.
3. **2026-10-20, around 05:49 WIB:** the USB Burning Tool was run with **Erase bootloader** ticked. The log shows `ERASE BOOTLOADER → Erase command success`. That wiped the first 4 MiB of eMMC. The burn then failed at DDR init (secure chip, no valid `DDR_ENC`), so the bootloader was never rewritten. Result: mask ROM, black screen.
4. A partial bootloader write afterwards left a "valid header, broken image", which also blocked the SD fallback. Restoring the full factory 4 MiB from a backup fixed SD booting.

**Takeaway:** on a secure HG680P, **Erase bootloader** + an image whose `_ENC` items don't match = instant brick. Check that your image passes the 4% step before you trust it with an erase.

---

## Resources & credits

- **FarelRA**: [HG680P-B860H-Files](https://github.com/FarelRA/HG680P-B860H-Files) (`revive-dead-emmc-bootloader/u-boot.bin`) and the [revive gist](https://gist.github.com/FarelRA/2d5bc9e23d2e718f1f30247b74638c32). This was the source of the encrypted BL2 that first passed DDR, and of the SD revive method.
- [3F/aml_s905_uboot](https://github.com/3F/aml_s905_uboot): GXL para docs and `aml_encrypt_gxl` (GXB images, so not directly usable on S905X)
- [assegaf/stb-905x-indonesia](https://github.com/assegaf/stb-905x-indonesia): real HG680P UART boot log (`gxl_p212_v1`, `InUsbBurn`, env at `0x27400000`)
- [repk/gxlimg](https://github.com/repk/gxlimg), [Raxone/amlogic-usbdl_s905_s905x_s905w](https://github.com/Raxone/amlogic-usbdl_s905_s905x_s905w): GXL image tooling and secure-world dumping (not needed for this fix)
- [ophub/amlogic-s9xxx-armbian #262](https://github.com/ophub/amlogic-s9xxx-armbian/issues/262): Armbian-on-eMMC (not Android)
- [superna9999/pyamlboot](https://github.com/superna9999/pyamlboot): `AmlImagePack` was used to inspect images
- Aidan's ROM v9 (XDA): source of the custom ROM attempts

Debugging was done across about 2,000 messages with AI coding agents. Every claim here was checked against the actual USB Burning Tool and SSH logs from those sessions.

## Disclaimer

This worked on one unit, with one firmware build. If your box has no bootloader at all (blank first 4 MiB, sitting in WorldCup mode), a failed attempt mostly just leaves you where you started. The real risk is a **half-written or mismatched** bootloader: the ROM accepts the header, loads it, and hangs. Then the box doesn't drop back to WorldCup or SD boot, and you've lost both easy recovery paths. That happened during this recovery. Back up the first 4 MiB if you can, and don't hand-write bootloader blobs to the eMMC unless you know they match your chip. No firmware is distributed here. Use at your own risk.
