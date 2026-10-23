# HG680P Unbrick Guide
### Fiberhome HG680P · Amlogic S905X (GXL) · Secure Boot

[![Status](https://img.shields.io/badge/status-verified%20working-brightgreen)](#)
[![SoC](https://img.shields.io/badge/SoC-Amlogic%20S905X-blue)](#)
[![Boot](https://img.shields.io/badge/secure%20boot-ON-red)](#)
[![Tool](https://img.shields.io/badge/tool-USB%20Burning%20Tool%20v2.x-orange)](#)

> **tl;dr** — The Amlogic USB Burning Tool erased the bootloader partition and couldn't write it back.  
> Getting it back requires **two specific files**: the DDR blob from the 3F repo (community DDR.USB files are missing a critical struct) and the UBOOT_ENC.USB from stock HG680P firmware (generic builds don't have the security chip signing this board expects).  
> Use **v2.x only** — v3 does not work for this.

---

## Table of Contents

1. [Device Info](#1-device-info)
2. [How It Got Bricked — Root Cause](#2-how-it-got-bricked--root-cause)
3. [Boot Chain Primer](#3-boot-chain-primer-s905x--gxl)
4. [WorldCup / IPL Mode](#4-what-the-worldcup--ipl-mode-means)
5. [What Was Still Intact on the eMMC](#5-what-was-still-intact-on-the-emmc)
6. [PATH A — pyamlboot (Closed for Secure-Boot Devices)](#6-path-a--manual--pyamlboot-closed-for-secure-boot-devices)
7. [PATH B — Official Burning Tool (What Worked)](#7-path-b--official-amlogic-usb-burning-tool-what-actually-worked)
8. [Bug 1 — ddrpara Struct Missing at 0x9800](#8-bug-1--ddrpara-struct-missing-at-0x9800-in-community-ddrusb)
9. [Bug 2 — Wrong UBOOT_ENC.USB](#9-bug-2--wrong-uboot_encusb-generic-vs-security-chip-signed)
10. [**The Final Working Procedure**](#10-the-final-working-procedure)
11. [Things That Were Tried and Failed](#11-things-that-were-tried-and-failed)
12. [Key Evidence From Logs](#12-key-evidence-from-logs)
13. [Tools and Resources](#13-tools-and-resources)
14. [For Developers / Researchers](#14-for-developers--researchers)
- [Appendix: Error Code Quick Reference](#appendix-quick-reference-error-codes)

---

## 1. Device Info

| Field | Value |
|---|---|
| Model | Fiberhome HG680P |
| SoC | Amlogic S905X (GXL family) |
| RAM | 2 GB LPDDR3 |
| Storage | eMMC (internal) |
| OS | Android TV 9 |
| USB VID:PID (WorldCup mode) | `1b8e:c003` |
| Secure boot efuse | `0xc8100228 = 0xbdfd21bc` (bit 0x10 set → **secure boot is ON**) |

**Related models:** B860H shares a similar hardware platform and some of the same firmware files apply. The 3F repo's files target S905X generically.

---

## 2. How It Got Bricked — Root Cause

On **2026-10-05 at 22:07:05**, the Amlogic USB Burning Tool was used to flash a custom ROM. The tool's first action is always `ERASE BOOTLOADER`. The `bootloader` partition is the first 4 MiB of eMMC, starting at offset 0x0.

The tool logged:
```
[22:07:05][HUB2-2][Inf]--ERASE BOOTLOADER
```

Then the USB burn stalled and timed out before writing the new bootloader. The 4 MiB was wiped. Nothing was written back.

**From that point on, the mask ROM found no valid BL2 → the box enumerated in WorldCup mode (stage `2-4-0-0`) on every power-on.**

Everything else on the eMMC survived:
- ROM MPT @ 0x2400000
- System, vendor, odm, product, vbmeta, dtbo, recovery partitions — all intact
- Boot image @ 0x59600000
- DTB store @ 0x2800000

The *only* missing piece was the 4 MiB boot area at eMMC offset 0x0.

### Pre-brick boot area layout (`bootarea_before.bin`)
```
md5:  9c9362c0507e1cb2bcf383b83ec83ba0
size: 4 MiB (0x400000)

0x000000–0x0CBFFF  vendor u-boot (@AML container, encrypted, signed)
0x0CC000–0x0CFF3F  16 KB gap (zeros)
0x0CFF40–0x0ECF3F  AML_ multi-DTB store
0x0ECF40–0x1B92FB  aml_sdc_burn u-boot + ini + boot.img prefix
```

---

## 3. Boot Chain Primer (S905X / GXL)

Understanding this chain is essential for any recovery attempt:

```
Power on
  └─ Mask ROM (hardcoded in silicon, cannot be erased)
       └─ Reads eMMC @ 0x0 → looks for valid BL2 (@AML container)
            └─ BL2 = DDR init stage (ddrpara struct → trains DRAM)
                 └─ BL33 = U-Boot proper (main bootloader)
                      └─ Android / Armbian
```

**In WorldCup mode** the mask ROM has found no valid BL2 on eMMC, so it waits on USB for a host to push BL2 over the wire. This is the recovery entry point.

**The @AML container format:**
```
Offset  Content
0x00    16-byte SHA2 hash (of what follows)
0x10    "@AML" magic bytes
0x14    Header fields (size, version, etc.)
0x1000  Executable code starts here  ← CRITICAL: entry point is +0x1000, NOT 0x0
```

**ddrpara struct** at offset `0x9800` inside the DDR.USB blob: the DRAM config table the BL2 code reads before training the memory. Without it, DDR init fails regardless of everything else.

---

## 4. What the WorldCup / IPL Mode Means

When the box is in WorldCup mode:
- USB enumerates as `VID 1b8e PID c003`
- The Amlogic Burning Tool detects it as stage `2-4-0-0`
- The mask ROM is waiting to receive `DDR.USB` (or `DDR_ENC.USB` on secure-boot devices) over USB
- Once DDR is initialized, it waits for `UBOOT_ENC.USB` (the main bootloader)
- Once U-Boot is running, the tool proceeds with normal partition flashing

If either step fails, the box stays in WorldCup mode indefinitely.

---

## 5. What Was Still Intact on the eMMC

```
factory MPT (15 entries, crc 0xb803c1fd):
  p1  bootloader   size 0x00400000  off 0x000000000  ← THIS WAS ERASED
  p2  reserved     size 0x04000000  off 0x002000000
  ...

ROM MPT (20 entries, crc 0xf55a57fc):
  p1  bootloader   size 0x00400000  off 0x000000000  ← SAME
  p18 system       off 0x7A600000
```

**Only the first 4 MiB was gone.** If you have a backup, restoring it is trivial once you have a running U-Boot. If you don't, you need the files from the right sources (see [Section 10](#10-the-final-working-procedure)).

---

## 6. PATH A — Manual / pyamlboot (Closed for Secure-Boot Devices)

This path was explored thoroughly and **does not work** on secure-boot HG680P units. Documented here so you don't waste the same weeks.

### What PATH A attempts

Use open-source tools (pyamlboot, optimus.py, or custom Python scripts) to manually push the DDR.USB blob over USB and initialize DRAM without the official tool.

### Bug A1 — Wrong entry point in all known tools

Every open-source tool jumps to address `0xd9000000` to run the DDR blob. But `0xd9000000` is the **@AML container header** — 16 bytes of SHA hash followed by `@AML`. That is not executable code.

The real entry point is `0xd9000000 + 0x1000`.

```
run at 0xd9000000  → CPU executes the 16-byte container header as instructions → dies
paraMagic remains = our sent value (never updated by ROM)  → no ack

run at 0xd9001000  → SPL starts, reads para, writes 8-word hash-of-para response
                   → DRAM training begins but fails
                   → No DDR ack (paraMagic never becomes 0x7856efab)
```

The corrected entry point gets further — but still hits the secure-boot wall below.

### Bug A2 — Wrong run-para format

The genuine Amlogic run-para is **8 words / 32 bytes**:
```
3412cdab 00000200 0000c0df 00000001 00400000 0082c0cc 00000000 00000010
```

Known broken implementations:
- `pyamlboot` / `optimus.py` stock: only writes 6 words, zeroes the rest
- Radxa copy of pyamlboot: last two words **swapped** — sends `10000000 00000000` instead of `00000000 00000010`

### The secure-boot wall (cannot be bypassed)

```
0xc8100228 = 0xbdfd21bc
bit 0x10 is set → secure boot ENABLED
```

When secure boot is set, the mask ROM requires a **vendor-encrypted and vendor-signed** `DDR_ENC.USB`. The signing keys are Amlogic's private secrets — not in the tool install, not in any community ROM, cannot be extracted or forged.

**Full sweep performed, never acked:**
- 3 different BL2 blobs (community / stock / 3F reference)
- 2 para layouts, 12 different DRAM size/cmd word values
- 8 run addresses (base, +0x10, +0x40, +0x1000, ddr_set_addr variants)
- 4 para placements in memory
- para-first vs blob-first ordering
- 32-byte vs 0x200-byte para writes, 64-byte vs 0x1000-byte USB transfers
- response-echo experiments, AMLC FIP polling

**If your box has bit 0x10 set at `0xc8100228`, PATH A is closed. Use PATH B.**

---

## 7. PATH B — Official Amlogic USB Burning Tool (What Actually Worked)

The official tool (v2.x — **not v3**) takes a secure-boot branch when it reads the efuse and switches to `DDR_ENC.USB`. When fed the right files, it works.

**The two files that must be correct:**

| File | Wrong source | Right source |
|---|---|---|
| `DDR_ENC.USB` | Any community build whose source `DDR.USB` has zeros or garbage at 0x9800 | Built from `github.com/3F/aml_s905_uboot` DDR.USB (has valid ddrpara) |
| `UBOOT_ENC.USB` | Generic community U-Boot (missing security chip signing) | Extracted from stock HG680P firmware, or X96 firmware from the same security chip variant |

Both bugs are described in detail below. Fix both — neither alone is sufficient.

---

## 8. Bug 1 — ddrpara Struct Missing at 0x9800 in Community DDR.USB

**This is the root cause of DDR init failure on PATH B.**

At offset `0x9800` inside the DDR.USB blob lives a struct called `ddrpara`. It contains DRAM configuration — timing parameters, width, rank, capacity — that BL2 reads before training the memory. Without a valid struct, DDR init fails silently.

### What's at 0x9800 in different DDR.USB files

| Source | 0x9800 contents |
|---|---|
| "Aidan" community DDR.USB | Build string `xl 3e531ea - luan.yuan` — stomped over 0x9800 |
| Stock/OEM DDR.USB | All zeros |
| 3F/aml_s905_uboot DDR.USB | ✅ Correct struct: `03 02 01 00 01 03 02 00 ...` |

### How to check

> ⚠️ Check the **unencrypted `DDR.USB`**, not `DDR_ENC.USB`. The encrypted blob's 0x9800 region is ciphertext — it won't be readable plaintext. Verify the source file before it gets packaged into your image.

```python
with open('DDR.USB', 'rb') as f:
    f.seek(0x9800)
    data = f.read(16)
    print(data.hex())

# ✅ Good:  03020100 01030200 ...
# ❌ Bad:   00000000 00000000 00000000 00000000
# ❌ Bad:   786c2033 65353331 65612020 2d206c75  ("xl 3e531ea - lu")
```

### Fix

Get `DDR.USB` from **https://github.com/3F/aml_s905_uboot**

> ⚠️ **Pin to a specific commit.** The ddrpara contents can change between commits and the 0x9800 validation depends on the version that worked here.

### What successful DDR init looks like

```
[HUB2-2][Inf]--Download DDR.USB
[HUB2-2][Inf]--Control write pll reg1 0xd9000000:0x000000b1
[HUB2-2][Inf]--Control write pll reg1 0xd9000000:0x00005183
(×2 more pll writes)
[HUB2-2][Inf]--Write initial succeed
[HUB2-2][Inf]--Upload encrypt at 0xc8100228
[HUB2-2][Inf]--ulValue = 0xbdfd21bc
[HUB2-2][Inf]--File change to DDR_ENC.USB
[HUB2-2][Inf]--Read encrypt value succeed
```

Then DDR init acks with paraMagic `0x7856efab`, ddrCapacity `0x800` (= 2 GB detected).

---

## 9. Bug 2 — Wrong UBOOT_ENC.USB (Generic vs Security-Chip-Signed)

After DDR init succeeds, the tool pushes `UBOOT_ENC.USB` to `0x200c000`. If the device never re-enumerates as ADFU (Amlogic Download Firmware Update), U-Boot crashed.

**Symptom:**
```
Download UBOOT_ENC.USB → 0x200c000 ✓
DownCheckPara succeed
ReadCheckPara succeed
DownRunPara succeed
Run at 0xd9000000
... (12 seconds pass) ...
Detect Device remove timeout
[0x10303004] Switch status/Identify/Error
```

The HG680P has a **security chip** that signs communication with U-Boot. A generic UBOOT_ENC.USB doesn't have the matching signing this board expects — U-Boot panics before USB init.

### Fix

Extract `UBOOT_ENC.USB` from the **stock HG680P firmware** (`ATV FW HG680P.img`). Stock firmware for compatible devices (X96 and similar S905X boards that share the same security chip variant) also works.

Open the stock `.img` in the Amlogic USB Burning Tool or a hex editor — look for the `UBOOT_ENC.USB` partition item, or extract from the `items/` directory if the firmware is unpacked.

**After switching to the stock HG680P UBOOT_ENC.USB, U-Boot came up, USB re-enumerated as ADFU, and the burn completed normally.**

---

## 10. The Final Working Procedure

### Prerequisites

- Windows PC with **Amlogic USB Burning Tool v2.x** (v3 does not work)
- A firmware `.img` you want to flash
- Correct `DDR_ENC.USB` — built from `3F/aml_s905_uboot` DDR.USB
- Correct `UBOOT_ENC.USB` — from stock HG680P firmware, or X96 from the same security chip variant
- USB Type-A to Type-A cable (or USB-A to micro/USB-C depending on your HG680P variant)
- Optional: toothpick/paperclip for the recovery pinhole (inside the AV jack on some units)

### Step 1 — Verify your DDR.USB at 0x9800

Check offset `0x9800` in the hex editor or the Python snippet above. Must not be all zeros or a build string. If wrong, replace with the 3F repo version.

### Step 2 — Prepare your firmware image

Replace the `DDR_ENC.USB` and `UBOOT_ENC.USB` items in your burn image with the verified files. For directory-format (unpacked) firmware, overwrite the two files in `items/`.

**Important:** The normal burn image only writes 835,584 bytes for the bootloader partition, but the partition is 4 MiB. The DTB store at 0x0CFF40 is also inside the bootloader partition — if you only write the u-boot portion, the DTB store is lost and the box won't find its device tree. Make sure your image covers the full 4 MiB if you want a proper restore.

```
0x000000–0x0CBFFF  vendor u-boot (@AML encrypted, 835,584 bytes)
0x0CC000–0x0CFF3F  gap (zeros)
0x0CFF40–0x0ECF3F  AML_ multi-DTB store
0x0ECF40+          aml_sdc_burn + boot.img prefix
```

### Step 3 — Enter WorldCup mode

The box is already in WorldCup mode if bricked the same way. Connect via USB. Should enumerate as `USB\VID_1B8E&PID_C003`.

If it doesn't enumerate:
- Hold the recovery pinhole button while connecting power
- Some units: hold reset button while plugging in USB

### Step 4 — Launch the tool and start the burn

Open the tool — it should detect `2-4-0-0`. Load your image, click Start.

Watch for:
```
File change to DDR_ENC.USB    ← secure boot detected, switched to encrypted blob
Read encrypt value succeed
Write initial succeed
```

If DDR init fails → `DDR_ENC.USB` is still wrong.

### Step 5 — Wait for U-Boot handoff

```
Download UBOOT_ENC.USB → 0x200c000  [OK]
DownCheckPara succeed
```

The device should disconnect and reconnect as ADFU. If it times out → `UBOOT_ENC.USB` is wrong.

### Step 6 — Burn completes

Once ADFU is active, all partitions flash normally. Bootloader partition gets the full 4 MiB.

### Step 7 — Power cycle and boot

The box should boot into Android TV. If it boots but something is wrong, check the DTB store at eMMC offset 0x2800000 (the `dtb` partition).

---

## 11. Things That Were Tried and Failed

### SD card — Layout A (wrong offset)
**Tried:** Write the 4 MiB boot area to SD at offset 0x0.  
**Result:** Still `2-4-0-0`.  
**Why:** S905X needs the bootloader at SD offset **0x200** (sector 1, not 0x0). Offset 0x0 is the MBR — overwriting it also breaks the SD card's own boot.  
**Note:** The 3F repo documents this: *"s905 bootloader will be stored with 0-offset in emmc (s905x +512 bytes)"*.

### SD card — Layout B (correct offset, still failed)
**Tried:** Bootloader at SD 0x200, MBR preserved, DTB store at correct gap offset.  
**Result:** Still didn't boot from SD.  
**Why:** This unit appears to prioritize eMMC over SD in the mask ROM. eMMC CLK isolation (shorting pin 29/30) would force SD boot, but wasn't attempted.

### pyamlboot with corrected entry point
**Tried:** Modified pyamlboot to jump to 0xd9001000.  
**Result:** SPL started and read the para. ROM produced a hash-of-para response. DRAM never came up.  
**Why:** Secure boot requires vendor-encrypted BL2. The ROM's para-check step refuses without the correct keys.

### Community/Aidan DDR.USB with official tool
**Tried:** Community DDR.USB files on the secure-boot path.  
**Result:** `ReadPara failed [0x10105002]`.  
**Why:** ddrpara at 0x9800 is missing or corrupted.

### USB transport error `0x10103003` / `usbWriteFile` timeout

> ⚠️ This is a **distinct failure mode** from DDR init failure — commonly confused because the error code overlaps.

**Symptom:**
```
usbWriteFile len=4096,ret=-116 error_msg=libusb0-dll:err [_usb_reap_async] timeout error
```
or `ret=-5 [device not functioning]`

The burn fails immediately at 0%, before DDR init even starts. **The blob never transferred. This is a USB transport problem on the host side, not a firmware problem.**

**Root cause:** Parsec virtual USB adapter (or any USB-over-network/virtualization software) intercepting libusb. Also: bad cable, USB hub, or power glitch.

**Fix:**
1. Power cycle the box (5 seconds off)
2. Unplug and replug the USB cable
3. If using Parsec or any USB virtualization software — **bypass it entirely**, use a direct USB port
4. Try a different USB port (USB 2.0, directly on the motherboard, no hub)
5. Retry

`0x10103003` has two distinct meanings:
- `Need_Write=4096, Actual_Write=0` → **USB transport failure** (fix above)
- Same code but after blob transfer → **secure-boot plain-path rejection** (ROM refuses unsigned DDR.USB)

### Generic UBOOT_ENC.USB after correct DDR init
**Tried:** DDR from 3F repo (correct), UBOOT_ENC.USB from generic community source.  
**Result:** Tool uploaded U-Boot, told it to run, then 12-second timeout → `[0x10303004]`. Device never re-enumerated as ADFU.  
**Why:** Generic U-Boot lacks security chip signing → crashes before USB init.

### eMMC CLK isolation
**Not attempted** — requires opening the box and identifying eMMC pins. A valid recovery path if all USB approaches fail.

---

## 12. Key Evidence From Logs

### Secure boot detection
```
[HUB2-2][Inf]--Upload encrypt at 0xc8100228
[HUB2-2][Inf]--ulValue = 0xbdfd21bc
[HUB2-2][Inf]--File change to DDR_ENC.USB
```
`0xbdfd21bc` with bit `0x10` set = secure boot enabled. Tool correctly switches to the encrypted DDR path.

### DDR init failure (bad DDR.USB)
```
[HUB2-2][Err]--Download file failed, sequence 0
[HUB2-2][Err]--usbWriteFile len=4096,ret=-116 error_msg=libusb0-dll:err [_usb_reap_async] timeout error
```
or: `ReadPara failed [0x10105002]`

### Successful DDR init
- Sent para with magic `0x3412cdab`
- ROM responds with magic `0x7856efab` ← **DDR init success**
- `ddrCapacity: 0x800` ← 2 GB detected correctly

### U-Boot handoff failure (wrong UBOOT_ENC.USB)
```
Download UBOOT_ENC.USB → 0x200c000 ✓
DownCheckPara succeed
ReadCheckPara succeed
DownRunPara succeed
Run at 0xd9000000
[... 12 seconds ...]
Detect Device remove timeout
[0x10303004] Switch status/Identify/Error
```

### USB transport failure (Parsec)
```
usbWriteFile len=4096,ret=-116 error_msg=libusb0-dll:err [_usb_reap_async] timeout error
usbWriteFile len=4096,ret=-5 [device not functioning]
```
These appeared on 2026-10-22 at 20:55–20:57 after DDR init had already been proven working. Root cause: Parsec virtual USB adapter. Fixed by power cycling and connecting directly.

### Bricking event (2026-10-05)
```
[22:07:05][HUB2-2][Inf]--ERASE BOOTLOADER
[22:07:05 ...][HUB2-2][Err]--USB timeout
```

---

## 13. Tools and Resources

### Critical repos

| Resource | URL | Notes |
|---|---|---|
| **3F/aml_s905_uboot** | https://github.com/3F/aml_s905_uboot | DDR.USB with correct ddrpara @ 0x9800, `aml_encrypt_gxl`, S905X recovery docs. ⚠️ Pin to a specific commit — ddrpara can change between commits. |
| **FarelRA/HG680P-B860H-Files** | https://github.com/FarelRA/HG680P-B860H-Files | Stock firmware, ROM files, partition layouts for HG680P and B860H |
| **pyamlboot** | https://github.com/superna9999/pyamlboot | Python Amlogic USB recovery tool (has known bugs for secure-boot; useful for research) |

### Software

| Tool | Purpose |
|---|---|
| Amlogic USB Burning Tool **v2.x** | The tool that completed the burn — use v2, not v3 |
| Python 3 + pyusb + capstone | Protocol research and blob disassembly |
| binwalk | Blob analysis |
| WSL | Required to run `aml_encrypt_gxl` (Linux binary) |

### `aml_encrypt_gxl`

Amlogic's GXL signing tool, included in the 3F repo. Can re-sign BL2 if you have the key package — but the keys are vendor secrets only licensed board makers have. Without them, this tool is not useful for recovery.

---

## 14. For Developers / Researchers

### USB protocol in WorldCup mode

1. Host writes PLL control registers: `0xd9000000 ← 0xb1`, `← 0x5183` (×2 each)
2. Host reads efuse at `0xc8100228` → determines secure vs plain path
3. **Secure path:** Host uploads `DDR_ENC.USB` (vendor-signed, encrypted)
4. Host writes run-para (8 words) to scratch window
5. Host sends "run" command to `0xd9000000` (real code is at `+0x1000`)
6. ROM initializes DDR using ddrpara from blob
7. On success: paraMagic in scratch window → `0x7856efab`
8. Host uploads `UBOOT_ENC.USB` to `0x200c000`
9. Host sends run command
10. Device disconnects, re-enumerates as ADFU
11. Normal partition flashing via ADFU protocol

### @AML container format

```
0x00  [16 bytes]  SHA2 hash
0x10  [4 bytes]   "@AML" magic
0x14  [varies]    Header fields
...
0x1000             Executable code begins HERE (not at 0x0)
```

The entry point bug (all open-source tools jump to 0x0 = the hash, not 0x1000 = the code) is present in pyamlboot, optimus.py, and likely the Burning Tool on the plain branch. The official tool on the secure-boot branch handles this differently internally.

### Genuine run-para (8 words, little-endian)

```
3412cdab  ← magic (ROM uses this to find the para in the scratch window)
00000200  ← size
0000c0df  ← command
00000001  ← word3 (DRAM config related)
00400000  ← word4
0082c0cc  ← word5
00000000  ← word6
00000010  ← word7
```

The Radxa pyamlboot copy has words 6 and 7 **swapped** (`10000000 00000000` instead of `00000000 00000010`).

### ddrpara struct at 0x9800

Known-good start bytes (from 3F repo):
```
03 02 01 00 01 03 02 00 ...
```
Encodes DRAM timing, width, rank count, and capacity. All zeros = DDR init always fails.

### Recovery timeline

| Date/Time | Event |
|---|---|
| 2026-10-05 22:07:05 | Bootloader erased by USB Burning Tool; burn failed → box bricked |
| 2026-10-21 ~11:25 | First DDR init success (`aidan_hazel_final.img` — 3F DDR + stock UBOOT) — DDR passed, U-Boot handoff failed |
| 2026-10-21 ~12:26 | Second DDR init success (`stockddr_enc_v2`) — confirmed repeatable |
| 2026-10-21 ~12:29 | `ATV FW HG680P.img` (stock HG680P firmware) opened — correct UBOOT_ENC.USB source identified |
| 2026-10-22 20:55–20:57 | USB transport failures (`ret=-116`/`ret=-5`) from Parsec — fixed by power cycle + direct USB |
| 2026-10-22 (late) | Full burn completed |
| 2026-10-22 (late) | Box booted Android TV. ADB accessible. ✅ |

### Verified image checksums (sha256 prefix)

| Image | DDR_ENC.USB | UBOOT_ENC.USB |
|---|---|---|
| `aidan_hazel_finalboot_v2.img` | `90d5f0dd...` | `a59cd538...` |
| `aidan_hazel_final.img` | `90d5f0dd...` | `8d4927e7...` |

`bootarea_before.bin` (pre-brick 4 MiB boot area, md5 `9c9362c0507e1cb2bcf383b83ec83ba0`) is the gold standard for a factory-state restore.

### Research scripts

| Script | Purpose |
|---|---|
| `ddr_read.py` | Read back para from scratch window |
| `ddr_sweep.py` | Sweep multiple para shapes and run addresses |
| `ddr_monitor.py` | Continuously poll for paraMagic change |
| `rawdisk.py` | Raw SD card read/write helper |
| `sd_write_layoutB.py` | Write SD card with correct S905X offset (0x200) |
| `build_fullboot.py` | Assemble full 4 MiB boot area from parts |
| `sd_restore_mbr.py` | Restore MBR after accidental overwrite |

---

## Appendix: Quick Reference Error Codes

| Code / Value | Meaning |
|---|---|
| Stage `2-4-0-0` | WorldCup / IPL mode — mask ROM waiting for USB DDR blob |
| `0x10103005` | Read item data failed — `DDR_ENC.USB` not found in image |
| `0x10103003` | **Two meanings:** (1) `Need_Write=4096, Actual_Write=0` = USB transport failure (Parsec/bad cable — power cycle + replug); (2) plain DDR path rejected by secure-boot ROM after blob transfer |
| `0x10105002` | ReadPara failed — DDR init failed, usually bad ddrpara at 0x9800 |
| `0x10303004` | Switch status/Identify/Error — U-Boot never came up, ADFU timeout |
| `0x7856efab` (paraMagic) | ✅ **DDR init SUCCESS** |
| `0x3412cdab` (paraMagic) | Initial magic sent by host — ROM hasn't responded yet |
| `0xbdfd21bc` (efuse) | Secure boot enabled (bit 0x10 set) |

---

## Acknowledgements

Technical research, protocol sweeps, and recovery work by **Hazel** (2026-10).

If this helped you, please contribute your findings back — especially if you have a different S905X variant that required different files, or if you can confirm a specific 3F repo commit hash that carries the correct ddrpara.

If something here is wrong or outdated, open an issue or PR.
