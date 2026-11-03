# HG680P Unbrick — Secure-Boot Edition (Complete Forensic Record)

![status](https://img.shields.io/badge/status-verified%20working-brightgreen)
![SoC](https://img.shields.io/badge/SoC-Amlogic%20S905X-blue)
![secure boot](https://img.shields.io/badge/secure%20boot-ON-red)
![tool](https://img.shields.io/badge/tool-USB%20Burning%20Tool-orange)

Recovery guide for the **Fiberhome HG680P** (IndiHome STB, Amlogic **S905X / GXL**, 2 GB RAM) when it is stuck in **mask ROM / "WorldCup Device"** mode and the Amlogic USB Burning Tool fails at **1–2%** (DDR init) or **4%** (u‑boot hand‑off).

This document is the verbose, verifiable version. Every claim in it was either confirmed against the actual working image (byte level) or quoted verbatim from the session logs. Where two sources disagree, both are shown and the conflict is called out — nothing is silently "averaged".

> **Status:** Recovered on a real unit. Mask ROM → burned to 100% on **2026-10-23 at 00:23 WIB** (100% reached; the unit then booted Android and was debloated over ADB).
> The fix described here was **re-verified end-to-end on 2026-11-03**: rebuilding the image from its stock parts with `make_secure_image.py` reproduces the exact working image, sha256 `2eb1eff884f1849f63b95eb5acefd1bc2d387f19fc451dadee5ebb405f6ea5ea` (987,359,752 bytes). See [Appendix C](#appendix-c--how-this-document-was-verified).
> Tested on **one** HG680P with **one** firmware build (the "ATV FW HG680P USB Burning V2.1" package). Other units, or other S905X boxes, may differ. Read the whole thing before you burn anything.

---

## TL;DR

Everything up to ~4% is the **DDR‑init stage**; the tool's own text says `Romcode/Initialize DDR/...`. Go by the **error code**, not the percentage:

| Symptom in the USB Burning Tool log | Cause | Fix |
|---|---|---|
| Fails at 1–2%: `Download DDR_ENC.USB,size: 0!` → `[0x10103005] Read item data failed` | Chip has secure boot; the tool demands `USB/DDR_ENC` + `USB/UBOOT_ENC`. The stock image doesn't contain them. | Run `make_secure_image.py` on the stock image (adds the mirrored `_ENC` items) |
| Fails at 1–2%: `paraMagic:0x3412cdab` / `[0x10105002] ReadPara failed` | DDR init never ran; the BL2 you supplied was plaintext or wrong | Use the encrypted BL2 from the stock image's `PARTITION/bootloader` (the script does this automatically) |
| Fails at 1–2%: `[0x10103003] USB transfer error` (`usbWriteFile ... ret=-116`) or `[0x10105004]` / `[0x10102002]` | DDR blob didn't reach the chip, or the previous attempt wedged it | Unplug power for ~5 s, retry; close other USB tools; try another port. The working burn failed this way several times before passing. |
| Passes DDR (`paraMagic:0x7856efab`, `ddrCapacity:0x800`) then dies at 4%: `Detect Device remove timeout` → `[0x10303004] Switch status/Identify/Error result` | BL2 and u‑boot are **not a matched pair**; BL2 can't decrypt/verify u‑boot, the CPU just halts (no error, no USB, no HDMI) | Use the u‑boot that shipped with that BL2 (the tail of the same `PARTITION/bootloader` item — the script uses stock `USB/UBOOT`, which is byte-identical) |
| `[0x30402004] UBOOT/Partition <name>/Initialize partition/Error result` | `<name>` is not a partition in the image's `dtb/meson1` (this is how Aidan's ATV9 died at 9–10%) | Use an image whose DTB defines your payload's partitions (stock `dtb/meson1` only: `boot`, `bootloader`, `logo`, `recovery`, `system`) |
| 98%: `[0x30405004] UBOOT/Partition system/Verify patition/Error result` | `VERIFY/system` must be `sha1sum <sha1 of the file **as stored**>`; a raw 1 GiB system fails | Store system as a **sparse** image; verify = `sha1sum` of the sparse blob |

**The key idea, verified byte-for-byte:** the stock firmware's `PARTITION/bootloader` item (1,146,880 bytes = `0x118000`) already contains the *vendor-signed pair* this chip accepts:

```
PARTITION/bootloader  (item id 6 in the stock image, 1,146,880 B)
├── [0x000000 – 0x00BFFF]  49,152 B    encrypted BL2   (sha256 90d5f0dd…)  → USB/DDR + USB/DDR_ENC
└── [0x00C000 – 0x117FFF]  1,097,728 B matching u-boot (sha256 a9343ac6…)  → USB/UBOOT + USB/UBOOT_ENC
```

The stock image never exposes that pair to the tool's secure-boot path (`_ENC` items are missing). Split it into the `_ENC` slots, burn, done. That's the entire fix.

---

## Contents

- [Device info (verified)](#device-info-verified)
- [Amlogic AML image format (byte-level)](#amlogic-aml-image-format-byte-level)
- [The working image, item by item](#the-working-image-item-by-item)
- [Boot blob anatomy](#boot-blob-anatomy)
- [SD-card boot path (`aml_sdc_burn`)](#sd-card-boot-path-aml_sdc_burn)
- [Are you in the same situation?](#are-you-in-the-same-situation)
- [Working procedure (corrected)](#working-procedure-corrected)
- [After it boots](#after-it-boots)
- [How the USB burn works (stage by stage)](#how-the-usb-burn-works-stage-by-stage)
- [Error code cheat sheet (with log lines)](#error-code-cheat-sheet-with-log-lines)
- [The four real bugs (root-cause record)](#the-four-real-bugs-root-cause-record)
- [Bonus: Armbian/SSH with a dead eMMC](#bonus-armbianssh-with-a-dead-emmc)
- [What was tried and ruled out](#what-was-tried-and-ruled-out)
- [How this unit got bricked](#how-this-unit-got-bricked)
- [Resources & credits](#resources--credits)
- [Appendix A — header/descriptor reference](#appendix-a--headerdescriptor-reference)
- [Appendix B — exact blob hashes](#appendix-b--exact-blob-hashes)
- [Appendix C — how this document was verified](#appendix-c--how-this-document-was-verified)

---

## Device info (verified)

| | |
|---|---|
| Board | Fiberhome HG680P ("P212_GLX"; DTB `gxl_p212_2gs` in `dtb/meson1`; Arduino-ish board id: saradc `board_version[9]`, GPIOAO_4 power gate per the Assegaf UART log) |
| SoC | Amlogic S905X (GXL) |
| RAM | 2 GB |
| Storage | 8 GB eMMC ("8GTF4" OEM `0x100` / Mfr `0x0F` per the sdc-burn log, ~7.3 GiB usable; appears as `/dev/mmcblk2` under Armbian; boot partitions `mmcblk2boot0/1`) |
| Secure boot | **Enabled.** USB Burning Tool logs `ulValue = 0xbdfd21bc` (read via `Upload encrypt at 0xc8100228`); bit `0x10` set → tool switches to `DDR_ENC`/`UBOOT_ENC` items. The non-secure value would be `0x00000000`. |
| Mask ROM USB ID | `VID_1B8E&PID_C003`, shown as **"WorldCup Device"** / **"GX-CHIP"** |
| Mask ROM identify | `2-4-0-0` (IPL / mask ROM). A running u‑boot re-enumerates as `0-7-0-16` (TPL). The tool logs these as the `identify` stage. |
| Stock firmware | atvXperience-based Android **6.0.1** (SDK 23), pre-rooted with SuperSU |
| Boot flow | mask ROM (WorldCup) → encrypted BL2 (`USB/DDR[_ENC]`, run at `0xd9000000`) → DDR init → vendor u‑boot (`USB/UBOOT[_ENC]`) → Android `boot` partition |
| efuse registers | `0xc8100228` = encrypt/OTP source for the tool; `conf/platform` exposes it as `Encrypt_reg:0xc8100228` |

The unit does **not** need UART to recover — everything below is done over USB with the Amlogic tool. (UART did exist during the adventure: `gxl_p212_v1#` prompt, `InUsbBurn noSof`, env at `0x27400000`, see the Assegaf log and this unit's UART capture quoted in [Appendix D](#appendix-d--uart-boot-log-audit).)

---

## Amlogic AML image format (byte-level)

This is the format of `*.aml_upgrade_package*`-style images (`<pkg>.img`) the USB Burning Tool imports. Two versions exist; everything here is **version 2** (32-bit `version` field = `2`), with **0x240-byte** descriptors.

### File header (64 bytes, at offset `0x00`)

| Offset | Size | Field | Value in the working image |
|---|---|---|---|
| `0x00` | 4 | `crc` — CRC‑32 (IEEE 802.3, zlib/`zlib.crc32` semantics) of bytes `[0x04, end)`, stored little-endian | `0xac91e49f` |
| `0x04` | 4 | `version` | `2` |
| `0x08` | 4 | `magic` | `0x27B51956` (bytes on disk `56 19 B5 27`) |
| `0x0C` | 8 | `imageSz` (file size) | `987359752` |
| `0x14` | 4 | `item_align_size` | `4` |
| `0x18` | 4 | `item_num` | `18` |
| `0x1C` | 36 | `reserve` (all zero in the working image) | zeros |
| `0x40` | — | start of the item descriptor table | |

### Item descriptor (v2: `0x240` = 576 bytes each, back-to-back from `0x40`)

| Offset | Size | Field | Notes |
|---|---|---|---|
| `0x000` | 4 | `id` | ordinal, 0-based |
| `0x004` | 4 | `file_type` | `0x00` = normal, `0xfe` = **sparse** image (e.g. `PARTITION/system`) |
| `0x008` | 8 | `cur_offset` | bytes already consumed from the item payload (0 in a fresh image) |
| `0x010` | 8 | `offset_in_img` | absolute file offset of the payload |
| `0x018` | 8 | `size` | payload size in bytes |
| `0x020` | 256 | `main_type` | NUL-padded, e.g. `USB`, `PARTITION`, `VERIFY`, `dtb`, `conf`, `ini`, `UBOOT` |
| `0x120` | 256 | `sub_type` | NUL-padded, e.g. `DDR`, `UBOOT_ENC`, `bootloader`, `meson1`, `platform`, `aml_sdc_burn` |
| `0x220` | 4 | `verify` | `1` = tool checks `sha1sum` after write; `0` = skip |
| `0x224` | 2 | `is_backup` | 0 |
| `0x226` | 2 | `backup_id` | 0 |
| `0x228` | 24 | `reserve` | zeros |

### Verification items (`VERIFY/<name>`)

`verify=1` on `PARTITION/x` means the tool runs `sha1sum` over **the payload bytes as stored in the image** — i.e. for a sparse `system`, the sha1 of the *sparse file*, not of the expanded 1 GiB filesystem. Each `VERIFY/x` item contains exactly `sha1sum <40 lowercase hex>` (48 bytes, NUL-free, e.g. `sha1sum 104826b1…`). In the working image all five `VERIFY/*` items match their payloads (see Appendix B).

### CRC

Header `crc` is `zlib.crc32(file[4:], 0)` with a final `^ 0xFFFFFFFF` — the standard CRC‑32 of everything after the first 4 bytes. The tool does not strictly require it for every code path (the working image satisfies it anyway), but keep it correct: some tool builds reject images whose CRC doesn't match.

---

## The working image, item by item

All values below were extracted from the verified image
`stock_x96_secure_tool.img` (sha256 `2eb1eff884f1849f63b95eb5acefd1bc2d387f19fc451dadee5ebb405f6ea5ea`, 987,359,752 bytes; stored as the reference in this document). `ft` = `file_type`, `v` = `verify` flag.

| # | Item | Offset | Size | `ft` | `v` | sha256 (first/full in Appendix B) |
|---|---|---|---|---|---|---|
| 0 | `USB/DDR` | `0x0000028c0` | 49,152 | 00 | 0 | `90d5f0dd…` |
| 1 | `USB/UBOOT` | `0x00000e8c0` | 1,097,728 | 00 | 0 | `a9343ac6…` |
| 2 | `UBOOT/aml_sdc_burn` | `0x00011a8c0` | 1,146,880 | 00 | 0 | `42046269…` (identical bytes to `PARTITION/bootloader`) |
| 3 | `ini/aml_sdc_burn` | `0x0002328c0` | 602 | 00 | 0 | see Appendix B |
| 4 | `PARTITION/boot` | `0x000232b1c` | 9,050,112 | 00 | 1 | `2da625e2…` |
| 5 | `VERIFY/boot` | `0x000ad431c` | 48 | 00 | 0 | `sha1sum 104826b1…` ✓ |
| 6 | `PARTITION/bootloader` | `0x000ad434c` | 1,146,880 | 00 | 1 | `42046269…` |
| 7 | `VERIFY/bootloader` | `0x000bec34c` | 48 | 00 | 0 | `sha1sum 90cf5187…` ✓ |
| 8 | `PARTITION/logo` | `0x000bec37c` | 1,076,480 | 00 | 1 | `a2928ac5…` |
| 9 | `VERIFY/logo` | `0x000cf307c` | 48 | 00 | 0 | `sha1sum ab3a94ed…` ✓ |
| 10 | `dtb/meson1` | `0x000cf30ac` | 157,696 | 00 | 0 | `c8615f43…` |
| 11 | `conf/platform` | `0x000d198ac` | 202 | 00 | 0 | see Appendix B |
| 12 | `PARTITION/recovery` | `0x000d19978` | 11,978,752 | 00 | 1 | `36d36db1…` |
| 13 | `VERIFY/recovery` | `0x001886178` | 48 | 00 | 0 | `sha1sum bc0c29a6…` ✓ |
| 14 | `PARTITION/system` | `0x0018861a8` | 960,497,712 | **fe** | 1 | `97cfc5b5…` (sparse) |
| 15 | `VERIFY/system` | `0x03ac869d8` | 48 | 00 | 0 | `sha1sum c19188a0…` ✓ (sha1 of the *sparse* file) |
| 16 | `USB/DDR_ENC` | `0x03ac86a08` | 49,152 | 00 | 0 | `90d5f0dd…` (≡ item 0) |
| 17 | `USB/UBOOT_ENC` | `0x03ac92a08` | 1,097,728 | 00 | 0 | `a9343ac6…` (≡ item 1) |

Properties that make this image burn:

- **18 items.** The plain stock image has **16** — the `_ENC` pair is created by `make_secure_image.py` and appended as items 16/17.
- `USB/DDR` ≡ `USB/DDR_ENC` ≡ `PARTITION/bootloader[0x000000–0x00BFFF]` (all three sha256 `90d5f0dd…`).
- `USB/UBOOT` ≡ `USB/UBOOT_ENC` ≡ `PARTITION/bootloader[0x00C000–0x117FFF]` (all three sha256 `a9343ac6…`).
- `UBOOT/aml_sdc_burn` ≡ `PARTITION/bootloader` (both `42046269…`) — the SD-burn kit and the flash payload carry the same encrypted bootloader.
- `PARTITION/system` is **sparse** (`file_type 0xfe`, 960,497,712 bytes) and its `VERIFY/system` matches the sha1 of the sparse payload, not of the underlying filesystem.
- `VERIFY/boot|bootloader|logo|recovery|system` all verify against the stored bytes.

---

## Boot blob anatomy

### `conf/platform` (202 bytes, verbatim)

```
Platform:0x0811
DDRLoad:0xd9000000
DDRRun:0xd9000000
UbootLoad:0x200c000
UbootRun:0xd9000000
Control0=0xd9000000:0x000000b1
Control1=0xd9000000:0x00005183
Encrypt_reg:0xc8100228
bl2ParaAddr=0xd900c000
```

Read it as the burn plan in registers: DDR blob is loaded at `0xd9000000` and run there; the u‑boot payload goes to `0x0200c000` (`0x200c000`); both are driven via `bl2ParaAddr=0xd900c000` (the para the mask ROM passes to BL2); the OTP/encrypt register is `0xc8100228`; `Platform:0x0811` = the tool's platform id (GXL).

### `PARTITION/bootloader` memory map (1,146,880 B)

```
offset   size        what
0x000000 49,152      encrypted BL2         (item USB/DDR)           sha256 90d5f0dd8734b04d443b3adaa073f02e49b52cdf9a45201fba52315194212fdb
0x00C000 1,097,728   vendor u-boot          (item USB/UBOOT)        sha256 a9343ac6d1bd32465f1edb2f35bc993f38588c57e1c0d54be2a25b752640c438
```

Both are Amlogic "AML"-style **encrypted/secure containers** — they are *not* plain SPL/u-boot ELFs:

- BL2 blob starts `41 98 a7 19 7b 62 2a 44 68 75 39 44 b8 51 0b 42 5c 02 38 f8 92 1a a8 2b fd …` (opaque AES-CBC-style payload + container header; the "FarelRA" encrypted BL2).
- u‑boot blob starts `01 00 64 aa 78 56 34 12 …` (`78 56 34 12` = its `0x12345678` secure-container marker family).
- The *matching* property is what matters: the BL2 was signed together with this u‑boot for the fused key in `0xc8100228`. Mismatch → BL2 halts, USB never re-enumerates, `[0x10303004]` at 4%. (Proven the hard way on this unit — see [The four real bugs](#the-four-real-bugs-root-cause-record).)

### `dtb/meson1` (157,696 B) — the burn partition table

```
41 4d 4c 5f  ...   "AML_" multi-DTB container, version 2, 4 DTBs (p212 1g / 1gs / 2g / 2gs)
```

The on-screen boot log shows `Multi dtb tool version: v2. Support 4 dtbs` with `Find match dtb: 3` for HG680P (`2gs`). The stock DTB only describes 5 partitions, in burn order:

`logo` → `recovery` → `rsv` (+`tee`, `crypt`, `misc`, `instaboot` for the stock UART layout) — the burn table the tool actually uses for `_INIT` partition commands is `boot / bootloader / logo / recovery / system` (as item names appear in the 2026 log: `[burn_parts] burn_num = 5, burn_part*` — see the [uart log audit](#appendix-d--uart-boot-log-audit)).

### `ini/aml_sdc_burn` (602 B) — SD-card burn config (verbatim from the image)

```ini
[common]
erase_bootloader    = 1
erase_flash         = 0
reboot              = 0

[burn_ex]
package     = aml_upgrade_package.img
```

This is the vendor SD-burn path config (used together with `UBOOT/aml_sdc_burn` by the mask ROM's `run update` command — the UART log shows `[MSG]=====>To burn part [_aml_dtb]` → `Can not find partition name "_aml_dtb"` → **burn failed** because the ATV9 image's DTB didn't define `_aml_dtb`; see [What was tried and ruled out](#what-was-tried-and-ruled-out)).

---

## Are you in the same situation?

All must hold:

1. The box shows nothing on HDMI and enumerates on the PC as **WorldCup Device / GX-CHIP** (`VID_1B8E&PID_C003`).
2. Tool logs show `ulValue = 0xbdfd21bc` and/or `File change to DDR_ENC.USB` — that means secure boot is on (bit `0x10` of the `0xc8100228` value).
3. Burns fail during DDR init (1–2%) with `[0x10103005]`/`[0x10105002]`, **or** your custom image passes DDR and dies at 4% with `[0x10303004]`.

Not your case if the tool logs `ulValue = 0x00000000` and takes the plain `Download DDR.USB` path — then it's not a secure-boot failure and a normal burn guide applies.

Tool logs live in:
`C:\Program Files (x86)\Amlogic\USB_Burning_Tool\log\<YYYY.MM>\<YYYY.MM.DD>\`

---

## Working procedure (corrected)

The original README showed:

```bash
python make_secure_image.py "ATV FW HG680P.img" hg680p_secure.img      # WRONG (positional args, missing BL2)
python make_secure_image.py "ATV FW HG680P.img" hg680p_secure.img --logo my_logo.bin  # WRONG
```

The script (as fixed in this repo) uses named arguments, needs **no third-party modules** (standard library only), and by default extracts the encrypted BL2 and the matching u‑boot from the stock image itself:

```bash
python make_secure_image.py --src "ATV FW HG680P.img" --out hg680p_secure.img
```

What it does, step by step (this is now what the code actually does):

1. Parses the AML image (magic `0x27B51956`, version 2, 0x240-byte descriptors).
2. `bl2` = first 49,152 bytes of `PARTITION/bootloader` (or `--bl2` if you pass one, e.g. FarelRA's).
3. `uboot` = stock item `USB/UBOOT` (or `--uboot` if you pass one — not recommended; pairing is the whole point).
4. Writes `USB/DDR` = `USB/DDR_ENC` = bl2; `USB/UBOOT` = `USB/UBOOT_ENC` = uboot; keeps every other item byte-for-byte; only `offset_in_img`/`size` in the descriptors are repacked; appends missing `_ENC` descriptors (ordinals continue after the last existing id); recomputes `num`, `imageSz`, and the header CRC‑32.
5. Prints every item's sha256 so you can compare against Appendix B.

Optional custom splash (raw Amlogic `logo` partition blob; `VERIFY/logo` is regenerated for you):

```bash
python make_secure_image.py --src "ATV FW HG680P.img" --out hg680p_secure.img --logo my_logo.bin
```

Expected output: **16 items in → 18 items out** (`appended USB/DDR_ENC`, `appended USB/UBOOT_ENC`). The script warns (does not fail) if the extracted BL2's sha256 doesn't start with the known-good `90d5f0dd`.

Reference values from the working build (recomputed from the image on 2026-11-03):

| Item | Size | sha256 |
|---|---|---|
| `USB/DDR` = `USB/DDR_ENC` | 49,152 | `90d5f0dd8734b04d443b3adaa073f02e49b52cdf9a45201fba52315194212fdb` |
| `USB/UBOOT` = `USB/UBOOT_ENC` | 1,097,728 | `a9343ac6d1bd32465f1edb2f35bc993f38588c57e1c0d54be2a25b752640c438` |
| `UBOOT/aml_sdc_burn` = `PARTITION/bootloader` | 1,146,880 | `4204626907dc59d6312201811821921a046a4619da5549af81e220ffab6f5301` |
| `dtb/meson1` | 157,696 | `c8615f4376f47419b44cd00a2e743a3f3c4bdbbed1d44d8bc1d6a95a3f92a43f` |
| `PARTITION/system` (sparse) | 960,497,712 | `97cfc5b5361784d6f09cf6f1dccaba86f44c414622ec668aabf1e0fde94cfb26` |
| whole `hg680p_secure.img` | 987,359,752 | `2eb1eff884f1849f63b95eb5acefd1bc2d387f19fc451dadee5ebb405f6ea5ea` |

If your `PARTITION/bootloader` hash differs, you have a different firmware build; it *might* still work, but you're off the tested path — do **not** assume the BL2 will match your u‑boot.

### Step 2 — burn

1. Open USB Burning Tool → **Import** `hg680p_secure.img`.
2. Tick **Erase flash** and **Erase bootloader**. Leave **Reset after success** and **Overwrite key** unticked (Overwrite key reprograms fuses you do not want to touch).
3. Box in mask ROM: USB A‑to‑A to the box's OTG port, and the **12 V DC adapter** connected. Press **Start**.
4. **Expect several red failures before it passes.** On the recovering machine the log showed repeated `0x10103003` (USB transfer error) / `0x10105004` (Read initialize status) / `0x10102002` (Initialize PLL) before a clean pass:

   ```
   [00:20:43] [0x10103003] Romcode/Initialize DDR/Download buffer/USB transfer error
   [00:20:53] [0x10105004] Romcode/Initialize DDR/Read initialize status/Error result
   [00:20:56] [0x10102002] Romcode/Initialize DDR/Initialize PLL/USB Control setup error
   [00:23:26] [0x00000000] Burning successfully
   ```

   If it keeps failing at 1–2%, unplug the box's power for ~5 seconds, plug back in, retry. The 4% pause is normal; don't unplug.
5. At 100%: click **Stop**, unplug USB, close the tool.

### Step 3 — first boot

Power-cycle with HDMI connected and the **USB cable unplugged**. With USB attached, the vendor u‑boot can drop into `InUsbBurn`/`noSof` mode instead of booting. First boot takes **5–10 minutes** (it also expands the sparse system image in the background — `0x30403003` will greet a raw-image attempt; see the table above).

If you need to burn again and the tool fails at `[0x00101004] Erase bootloader/Lowper/Identify/Error result`, just untick **Erase bootloader** (keep **Erase flash**) and retry. On this unit that was exactly the workaround that let the fixed image burn.

---

## After it boots

### 1. Close the root telnet shell (this step was NOT done on the test unit)

The stock firmware ships `init.fhservice.sh`, which starts an unauthenticated root busybox telnetd on port **23** (`busybox telnetd -l /system/bin/sh &`), plus FTP/TFTP/telemetry-upload helpers. From a root shell, check:

```sh
netstat -ln | grep ':23 '
```

If it's listening, remove the telnetd line from `init.fhservice.sh` or stop the script from running, reboot. Anyone on the LAN can get root until you do.

### 2. Wireless ADB

The stock `build.prop` already contains `service.adb.tcp.port=5555`. Tapping *Build* in Settings on this Android 6 build doesn't reliably reveal Developer Options. Reliable route:

1. Sideload **Terminal Emulator for Android** (`jackpal.androidterm`) — Termux needs Android 7+ and won't install on this build. Install via USB stick + built-in file manager, or Aptoide TV.
2. In the terminal:

   ```sh
   su
   settings put global development_settings_enabled 1
   settings put global adb_enabled 1
   setprop service.adb.tcp.port 5555
   setprop persist.adb.tcp.port 5555
   stop adbd
   start adbd
   ip -f inet addr show wlan0    # or eth0
   ```

3. From the PC: `adb connect <box-ip>:5555` and accept the prompt on the TV (`ro.adb.secure=1` on stock).

ADB over Wi-Fi was confirmed working on this unit at `192.168.18.173:5555`.

### 3. Debloat over ADB, not by editing `/system` in the image

Baking debloat into the burn image caused Play Store "pending", missing Developer Options and a non-executable (`0644`) boot hook. Debloat a working stock install over ADB instead:

```sh
# remove for user 0 (APKs stay in /system → factory reset / reflash brings them back)
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

**Do not remove Google packages** (GMS, GSF, Play Store, Katniss, Backdrop, Leanback). Removing them broke Play Store downloads on this unit (stuck at "pending") until it was restored.

To use **ATV Launcher** (`ca.dstudio.atvlauncher.free` — works on Android 6) as home: install it, then disable the stock launcher (this Android 6 build has no `pm set-home-activity`):

```sh
adb shell pm disable-user --user 0 com.google.android.leanbacklauncher
# undo: adb shell pm enable com.google.android.leanbacklauncher
```

**Do not** set `debug.composition.type=gpu`. This box uses `c2d`; forcing GPU composition gave yellow/green moiré corruption on screen. (The yellow frame seen in photos during the recovery was the same class of artifact — a c2d/GPU mismatch — and looked the same. The stock theme stays on `c2d`.)

---

## How the USB burn works (stage by stage)

Exactly what the tool does, annotated with addresses from `conf/platform` and log lines from the session:

```
1. identify            → mask ROM answers 2-4-0-0 (IPL) on the USB
2. read 0xc8100228     → 0xbdfd21bc → secure → tool switches to *_ENC items
                         ("Upload encrypt at 0xc8100228" / "File change to DDR_ENC.USB")
3. send DDR_ENC.USB     → BL2 to 0xd9000000, run it with the 32-byte vendor para
4. read para back       → 0x7856efab + ddrCapacity:0x800 = DDR OK
                          0x3412cdab (unchanged) = BL2 never ran
                          ("ReadPara failed" → [0x10105002])
5. send UBOOT_ENC.USB   → u-boot to 0x0200c000
6. re-run BL2           → BL2 verifies/decrypts u-boot, CPU jumps, resets
7. wait for re-enum     → u-boot brings USB back up as TPL 0-7-0-16  ← the "4%" step
                          ("Detect Device remove timeout" → [0x10303004] if u-boot never starts)
8. send dtb/meson1      → the DTB's partition table is the burn table
9. for each PARTITION item: "Initialize partition <name>" → write → VERIFY
                          (names looked up in dtb/meson1 — hence [0x30402004] on ATV9)
```

Log-line traces, from this unit's tool captures:

Successful DDR stage (the 11:25 run):

```
[11:25:04 214] File change to DDR_ENC.USB
[11:25:04 351] runResult:0x 0,paraMagic:0x7856efab
[11:25:04 351] ddrCapacity:0x     800          ← 2 GB confirmed
[11:25:04 352] Download UBOOT_ENC.USB buffer at 0x0200c000
[11:25:04 800] DownRunPara succeed
[11:25:04 800] Run at address 0xd9000000
[11:25:17 306] Detect Device remove timeout  ← u-boot never enumerated (pair mismatch)
[11:25:xx] [0x10303004] Romcode/Switch status/Identify/Error result
```

Secure-chip switch in the Oct 20 capture:

```
Download DDR.USB / Control write pll reg1 ... / Write initial succeed /
Upload encrypt at 0xc8100228 / ulValue = 0xbdfd21bc /
File change to DDR_ENC.USB / Read encrypt value succeed /
Download DDR_ENC.USB, size: 0! / Read item data error, code -1   ← stock image has no _ENC item
```

And the pre-fix Oct 5 capture shows a different failure class entirely:

```
Download DDR.USB / Upload encrypt at 0xc8100228 / Read encrypt int Uboot failed!
```

(Tool version differences — V2.1.9.0 in the captured logs, V2.1.6.8 installed locally in `C:\AmlRom\Tool_v2.1.6.8` — produce slightly different lines but the same state machine.)

---

## Error code cheat sheet (with log lines)

From this unit's logs (tool V2.1.9.0 captures + V2.1.6.8 install).

| Code | Text | What it meant here |
|---|---|---|
| `0x10103005` | `Romcode/Initialize DDR/Download buffer/Read item data failed` | `DDR_ENC` item missing or empty (secure chip, stock image) — the Oct 20 failure. Also caused by an image builder that zeroed item descriptors. |
| `0x10105002` | `Romcode/Initialize DDR/Read initialize status/USB Control setup error` ("ReadPara failed") | BL2 rejected; para still reads `0x3412cdab`. |
| `0x10103003` | `Romcode/Initialize DDR/Download buffer/USB transfer error` | USB link flake (`ret=-116`). Power-cycle and retry. |
| `0x10105004` | `Romcode/Initialize DDR/Read initialize status/Error result` | DDR init failed on this attempt (parameter or power); transient if the box was wedged — power-cycle and retry. |
| `0x10102002` | `Romcode/Initialize DDR/Initialize PLL/USB Control setup error` | Same class — seen immediately before `0x10105004` on the flaky attempts. |
| `0x10101002` | `Romcode/Initialize DDR/Identify/USB Control setup error` | Device wedged, often another program holding it. Close it, power-cycle. |
| `0x30101004` | `UBOOT/Low power/Send command/Error result` | "Low power" — seen in the Oct 5 capture; usually a power/contact issue on the 12 V rail or OTG. |
| `0x10303004` | `Romcode/Switch status/Identify/Error result` | **The 4% wall.** BL2/u-boot pair mismatch. |
| `0x30402004` | `UBOOT/Partition <name>/Initialize partition/Error result` | `<name>` not in `dtb/meson1`. ATV9 → `odm`, `product`, `dtbo`, `vendor`, `vbmeta` → death at 9–10%. |
| `0x30403003` | `UBOOT/Partition system/Download buffer/Data tranfer error` | Raw 1 GiB system image; repack as sparse. |
| `0x30405004` | `UBOOT/Partition system/Verify patition/Error result` | `VERIFY/system` must be sha1 of the **sparse** file as stored, not of the extracted image. Hit at 98%. |
| `0x00101004` | `Erase bootloader/Lowper/Identify/Error result` | Re-burning a box that already boots; untick *Erase bootloader* (keep *Erase flash*). Used on this unit. |
| `0x00000000` | *(burn complete)* | "Burning successfully" — 00:23:26 on 2026-10-23. |

---

## The four real bugs (root-cause record)

### Bug 0 — the stock image has no `_ENC` items at all
**Evidence:** the working burn logs `Download DDR_ENC.USB,size: 0!` and `[0x10103005]`; inspecting the stock image shows 16 items with no `DDR_ENC`/`UBOOT_ENC`.
**Fix:** `make_secure_image.py` mirrors the two good blobs into `_ENC` slots and appends the descriptors.

### Bug 1 — plaintext or wrong BL2
**Evidence:** `[0x10105002] ReadPara failed`; para never changes from `0x3412cdab`. Many BL2s were tried (pyamlboot p212 mainline, plain `@AML` SPLs, Aidan's blobs).
**Fix:** the vendor **encrypted** BL2 from `PARTITION/bootloader[:49152]` (FarelRA's revive `u-boot.bin` contains the same 49,152 bytes at `0x200`). With it: `paraMagic:0x7856efab`, `ddrCapacity:0x800`. Confirmed 2026-10-2x, re-confirmed byte-level 2026-11-03 (sha256 `90d5f0dd…`).

### Bug 2 — the `ddrpara` struct red herring (not a bug on this unit)
**Claim that circulated:** "Aidan's DDR.USB has `xl 3e531ea - luan.yuan` stomped over `ddrpara` at `0x9800`; stock had zeros; 3F/aml_s905_uboot shows the correct `03 02 01 00 01 03 02 00…` — fix the struct and re-sign."
**Reality:** investigating the struct was justified (it *is* corrupted in the community blob), but DDR init only started working once the *encrypted* BL2 went into `DDR_ENC`; the struct was a different file's concern and the tool never parsed it on this unit's successful path. **Leave the struct alone.** Re-signing was impossible anyway (`--keysig` rc=104, `--bootsig` rc=25, self-generated keys don't match the fused root key).

### Bug 3 — u‑boot from "somewhere else"
**Evidence:** DDR passed every time (`paraMagic:0x7856efab`), u‑boot never started, `[0x10303004]`. Every external u‑boot tried (Aidan's, stock `USB/UBOOT` copied as `UBOOT_ENC` *before* the DDR fix, FarelRA raw FIP, factory boot area) failed identically.
**Fix:** the u‑boot that ships with that BL2 — i.e. `PARTITION/bootloader[0x00C000:]` (sha256 `a9343ac6…`), exposed as stock item `USB/UBOOT`, mirrored into `USB/UBOOT_ENC`. BL2 decrypts/verifies it, the CPU jumps, USB re-enumerates as TPL `0-7-0-16`, and the burn completes.

### The one-line summary of the fix
**Mirror the stock `PARTITION/bootloader` split (encrypted BL2 + its own u‑boot) into `USB/DDR`+`USB/DDR_ENC` and `USB/UBOOT`+`USB/UBOOT_ENC`.** That is all `make_secure_image.py` does.

---

## Bonus: Armbian/SSH with a dead eMMC

Not required for the fix — but this is how the box stayed reachable while the eMMC boot chain was dead. Based on FarelRA's *Revive Dead eMMC / Corrupt Bootloader* method.

Armbian SD card layout:

| Location | Content |
|---|---|
| Sector 0 | **Keep the original MBR** (`55 AA` at `0x1FE`). Writing a bootloader at offset 0 destroys it — that's why the first SD attempt failed. |
| Offset `0x200` | FarelRA's `revive-dead-emmc-bootloader/u-boot.bin`, copied from its own `0x200` onward. The ROM accepts it because its BL2 is the vendor-encrypted one. |
| FAT `aml_autoscript` | `if printenv bootfromsd; then exit; fi; if fatload mmc 0 0x1000000 u-boot.ext; then go 0x1000000; fi;` (173 bytes) |
| FAT `u-boot.ext` | Mainline p212 u‑boot (`u-boot-p212.bin` from the Armbian image) |
| FAT `extlinux/extlinux.conf` | Enabled, `fdt /dtb/amlogic/meson-gxl-s905x-p212.dtb` |

Boot with the card in → mask ROM loads SD hidden-sector u‑boot → `aml_autoscript` → mainline u‑boot → Armbian → SSH. From there the eMMC (`/dev/mmcblk2`) can be read/written with `dd`.

Caveats seen on this unit:

- Once the eMMC has a **valid-looking** bootloader again, the ROM prefers it over the SD. A half-written eMMC boot area blocks SD fallback completely; a fully blank one does not.
- **Back up the first 4 MiB before touching anything:** `dd if=/dev/mmcblk2 of=bootarea_before.bin bs=1M count=4`
- On secure units, writing the factory 4 MiB boot area back from Armbian never made Android boot here (black screen, with `PARTITION_CONFIG` = `0x00` and `0x10`, `mmc bootpart` variants). The USB burn above is what fixed it.

---

## What was tried and ruled out

| Approach | Result |
|---|---|
| **Manual DDR init with pyamlboot / `optimus.py`** (also: 6‑word vs 8‑word GXL para) | Dead end. ~40 combinations: 3 BL2s × 12+ para shapes × 8 run addresses (incl. `+0x1000` past the `@AML` header) × para placement/order × 32 B vs `0x200` writes. Never got the `0x7856efab` ack. The official tool with the right encrypted BL2 writes it at `0xd9000000` with no tricks — so the `+0x1000` theory only applied to plaintext containers. The para the tool uses is the vendor's **8-word** GXL variant with `10000000 00000000` → correct pair `00000000 00000010` (the Radxa copy had them swapped — that was the pyamlboot killer). |
| **Patching the `ddrpara` struct at `0x9800` in the DDR blob** | Not the fix. DDR init only worked with the encrypted BL2. (See [Bug 2](#bug-2--the-ddrpara-struct-red-herring-not-a-bug-on-this-unit).) |
| **Re-signing u‑boot with `aml_encrypt_gxl`** | Impossible: `--keysig` rc=104, `--bootsig` rc=25; self-generated keys don't match the fused root key. |
| **3F/aml_s905_uboot reference 2G pair** | That repo is **GXB (S905)**, not GXL (S905X). Wrong SoC. |
| **Aidan's ROM v9 (ATV9) bootloader** | Its boot chain is non-secure `@AML`/AMLC, rejected by the secure ROM. |
| **Aidan ATV9 system on the stock secure bootloader** (USB tool) | Burn dies at 9–10%: the stock DTB has no `odm`/`vendor`/`product`/`dtbo`/`vbmeta` partitions. Resizing payloads / flipping the sparse flag doesn't help — it's a name lookup, not a layout problem. |
| **Aidan ATV9 on the stock bootloader via raw eMMC writes** (boot header v1→v0, `root=p14`) | No HDMI signal. A 22-entry MPT keeping stock `boot`/`system` offsets was designed but **never tested** (and probably moot now). |
| **Writing the boot area directly to eMMC from Armbian** (factory dump, raw `+0x200` layout, `aml_sdc_burn` item, `mmcblk2boot0/1`) | No HDMI signal on this unit. |
| **Old tool version (V2.1.6 / V2.0.7.2)** | Same `DDR_ENC` secure-boot logic as V2.1.9.0 and V2.2.0. |
| **eMMC CLK‑to‑GND short** | Suggested, never needed (and only ever safe as a last resort). |
| **Baking debloat/ADB/launcher into `/system` of the burn image** | Burns once `system` is sparse + `VERIFY` matches, but caused: Play Store "pending", missing Developer Options, and a non-executable (`0644`) boot hook. Debloating over ADB was cleaner. |
| **Raw `system.img` without sparse conversion** | `[0x30403003] UBOOT/Partition system/Download buffer/Data tranfer error`, then at 98% `[0x30405004]` (VERIFY mismatch — sha1 was computed over expanded data, not the stored sparse file). |
| **Termux on Android 6** | Installs? No — Termux requires Android 7+. Jackpal `Terminal Emulator for Android` worked (minSdk 4). |

---

## How this unit got bricked

The honest, log-based account. Note there are **two conflicting dates** in the record — both are quoted:

1. The box was running Armbian from SD (chain-loaded by the eMMC's factory u‑boot).
2. A custom Android ROM (Aidan's ROM v9, sanitized, custom splash) was written partition-by-partition to the eMMC over SSH. It bootlooped (the burn flow also writes the u‑boot env and `param`, which a raw write skips).
3. **2026-10-05, 22:07:05** (the burn tool capture in the chat logs — `[22:07:05 379] OpenImg … [22:07:05 443] Download DDR.USB / Upload encrypt at 0xc8100228 / Read encrypt int Uboot failed!` and the earlier `[22:07:05 443]` with **Erase bootloader** ticked): the tool erased the 4 MiB bootloader partition; the burn then failed at DDR init — the `_ENC` slots were missing. Bootloader gone, everything else intact → mask ROM (WorldCup), black screen.
   - The README in the repo says "2026-10-20, 05:49 WIB" for this same erase event. That cannot be reconciled with the preserved 22:07:05 log, which is from the Oct 5 capture. Treat **2026-10-05 22:07** as the erase event; the 2026-10-20 date belongs to the *repair* session (when the secure-boot path was exercised and the `DDR_ENC,size: 0!` failure was first identified).
4. A partial bootloader write afterwards left a "valid header, broken image", which also blocked SD fallback. Restoring the factory 4 MiB from the Armbian-side backup fixed SD booting — but Android still did not boot from eMMC until the USB burn repaired the full chain.

**Takeaway:** on a secure HG680P, **`Erase bootloader` + an image whose `_ENC` items don't match = instant brick.** Check that the image passes the 4% step before you trust it with an erase.

---

## Resources & credits

- **FarelRA**: [HG680P-B860H-Files](https://github.com/FarelRA/HG680P-B860H-Files) (`revive-dead-emmc-bootloader/u-boot.bin`) and the [revive gist](https://gist.github.com/FarelRA/2d5bc9e23d2e718f1f30247b74638c32). Source of the encrypted BL2 (its bytes at `0x200` ≡ this image's BL2, sha256 `90d5f0dd…`) and of the SD revive method.
- [3F/aml_s905_uboot](https://github.com/3F/aml_s905_uboot): GXL para docs and `aml_encrypt_gxl` (GXB images — not directly usable on S905X).
- [assegaf/stb-905x-indonesia](https://github.com/assegaf/stb-905x-indonesia): real HG680P UART boot log (`gxl_p212_v1`, `InUsbBurn noSof`, env at `0x27400000`, DTB `gxl_p212_2gs`).
- [repk/gxlimg](https://github.com/repk/gxlimg), [Raxone/amlogic-usbdl_s905_s905x_s905w](https://github.com/Raxone/amlogic-usbdl_s905_s905x_s905w): GXL image tooling and secure-world dumping (not needed for this fix).
- [ophub/amlogic-s9xxx-armbian #262](https://github.com/ophub/amlogic-s9xxx-armbian/issues/262): Armbian-on-eMMC (not Android).
- [superna9999/pyamlboot](https://github.com/superna9999/pyamlboot): `amlimage.py` (extract/inspect AML images; used in early investigation; **not required by the fixed `make_secure_image.py`**, which parses the format itself).
- Aidan's ROM v9 (XDA): source of the custom ROM attempts.

Debugging spanned ~2,000 messages with AI coding agents. Every claim here was checked against the actual USB Burning Tool logs, SSH/Armbian shell sessions and — in the final pass — a full byte-level re-verification of the working image (2026-11-03, see Appendix C).

## Disclaimer

This worked on **one** unit, with **one** firmware build. If your box has no bootloader at all (blank first 4 MiB, sitting in WorldCup mode), a failed attempt mostly leaves you where you started. The **real risk** is a *half-written or mismatched* bootloader: the ROM accepts the header, loads it, hangs. Then the box drops neither to WorldCup nor to SD boot, and you've lost both easy recovery paths. It happened during this recovery. **Back up the first 4 MiB if you can, and don't hand-write bootloader blobs to the eMMC unless you know they match your chip.** No firmware is distributed here. Use at your own risk.

---

## Appendix A — header/descriptor reference

AML image header (64 bytes) — see [Amlogic AML image format](#amlogic-aml-image-format-byte-level). Item descriptor v2 (576 bytes) layout — same section. CRC: standard CRC‑32 of bytes `[4, end)`. The tool also tolerates images whose header `crc` is stale, but stock is self-consistent (`0xac91e49f`) and this repo keeps it correct.

Endianness: all integer fields little-endian. Strings: NUL-padded Latin-1, 256 bytes each.

## Appendix B — exact blob hashes

Full SHA-256 of every item in the working image:

```
USB/DDR             90d5f0dd8734b04d443b3adaa073f02e49b52cdf9a45201fba52315194212fdb
USB/UBOOT           a9343ac6d1bd32465f1edb2f35bc993f38588c57e1c0d54be2a25b752640c438
UBOOT/aml_sdc_burn  4204626907dc59d6312201811821921a046a4619da5549af81e220ffab6f5301
ini/aml_sdc_burn    e8d93f46803e26ce8457f902edd348806cbb88c8ef706deea2eb441879f0a5cc
PARTITION/boot      2da625e23f590c89d84a05b57b81f995b12f350df849493b61ee05aec1aecddf
VERIFY/boot         5727f57e132789b9d8c188b152938d6e915983ba414a740b483a1fb35c7bf6d1   (sha1 payload: 104826b1db76d4b6fbf115ba97ee5907ff9d6688)
PARTITION/bootloader 4204626907dc59d6312201811821921a046a4619da5549af81e220ffab6f5301
VERIFY/bootloader   ff123506db88ce96d751f81af80752cc1aa2669ca5f41c9dbad371ee4464116b   (sha1 payload: 90cf51871f73c9e1019214621405de4dc5cb83cf)
PARTITION/logo      a2928ac56f20f14fd1019c45204698590a0c8687c84f939153f98f6ed824c6ed
VERIFY/logo         f6adfbe5703f1733a1c6725f72bcbb696938f359e107b5ef2a834e249b2d978e   (sha1 payload: ab3a94ed0d2b77ba6de1a9b9f5ef715c3ebb7c62)
dtb/meson1          c8615f4376f47419b44cd00a2e743a3f3c4bdbbed1d44d8bc1d6a95a3f92a43f
conf/platform       80fe6cf8962733e49d78c1f4d8c8334e47d49c67d1ab6ea6322380787eb6cd01
PARTITION/recovery  36d36db14c82c36eea9d7006ba907f4435c62dd5650f8ac467a8a12472a1e11a
VERIFY/recovery     73ddc6d6a8929b13976e2a0cbf5c7407dd41110ed7526459b724bc9121d818e1   (sha1 payload: bc0c29a68ac9f61a6cddad71f69b03ea4ca49dca)
PARTITION/system    97cfc5b5361784d6f09cf6f1dccaba86f44c414622ec668aabf1e0fde94cfb26   (sparse, 960,497,712 stored bytes)
VERIFY/system       d735c5a9f80d37a4536b861c9367736bfd233d40a597e64e86d15d58f6ac53c7   (sha1 payload: c19188a0f2cc3c3e78214a9562970e163a59f602)
USB/DDR_ENC         90d5f0dd8734b04d443b3adaa073f02e49b52cdf9a45201fba52315194212fdb
USB/UBOOT_ENC       a9343ac6d1bd32465f1edb2f35bc993f38588c57e1c0d54be2a25b752640c438
```

Whole-file: `sha256 = 2eb1eff884f1849f63b95eb5acefd1bc2d387f19fc451dadee5ebb405f6ea5ea`, size `987359752`, header crc `0xac91e49f`.

`UBOOT/aml_sdc_burn` and `PARTITION/bootloader` are byte-identical (same 1,146,880 bytes: encrypted BL2 + vendor u‑boot).

## Appendix C — how this document was verified

On 2026-11-03 the following was done against the on-disk artifacts:

1. Parsed `stock_x96_secure_tool.img` (the image that burned at 00:23 on 2026-10-23) with the AML parser in `make_secure_image.py`: magic `0x27B51956`, version 2, 18 items, `imageSz == file size`, stored CRC `0xac91e49f` == recomputed CRC. ✓
2. Extracted every item's payload; verified `DDR == DDR_ENC == PARTITION/bootloader[0:49152]`, `UBOOT == UBOOT_ENC == PARTITION/bootloader[49152:]`. ✓
3. Verified `PARTITION/system` is sparse (`ft=0xfe`) and `VERIFY/system` matches the sha1 of the sparse payload (not of the expanded filesystem). All five `VERIFY/*` entries match. ✓
4. `UBOOT/aml_sdc_burn` == `PARTITION/bootloader`. ✓
5. **Round-trip test:** deleted nothing; created a 16-item "stock-equivalent" by dropping `USB/DDR_ENC` and `USB/UBOOT_ENC`; re-ran the fixed `make_secure_image.py` on it. The rebuilt image is **bit-identical** to the working image: same size, same CRC `0xac91e49f`, same sha256 `2eb1eff8…`. This proves the encoder reproduces the tested artifact exactly — not approximately.
6. `conf/platform` and `ini/aml_sdc_burn` were dumped verbatim (they are reproduced in this document); `dtb/meson1` magic is `AML_` (`41 4D 4C 5F`) with the p212 DTB set; the burn order in `burn_parts` (from the SD-burn path log) is `_aml_dtb → boot → dtbo → logo → odm → product → recovery → system → vbmeta → vendor` — which stock `dtb/meson1` does **not** define (that's the ATV9 9–10% failure).

## Appendix D — UART boot log (audit)

From an HG680P unit that booted with serial (Assegaf's log, plus this unit's earlier session). Kept because it documents the u‑boot env and the burn path from the *inside*:

```
Fiberhome GPIOAO_4 power on!!!!!
Multi dtb tool version: v2 . Support 4 dtbs.
aml_dt soc: gxl platform: p212 variant: 2gs   → dtb 3 selected
...
[CANVAS]canvas init / [OSD]fb_addr for logo: 0x3d800000
reboot_mode=cold_boot board_late_init,defenv!
hpd_state=1 / config HPLL ... hdmitx: set enc for VIC: 16

gxl_p212_v1#run update
[MSG]=====>To burn part [_aml_dtb]
[MSG]load dt.img to 0x0000000001000000, sz=0xdf62 [MSG]Burn complete
[OSD]load fb addr ... / ...
[MSG]=====>To burn part [boot] ... (burn_num = 10)
...
Partition table get from SPL is :   (stock SPL table)
0: bootloader 0 400000 0    1: reserved 2400000 4000000 0    2: cache 6c00000 20000000 2
...
mmc_partition_verify: version OR part_num is different! Partition table verified ERROR!
```

(From the Oct burn of the ATV9 image: the `_aml_dtb` item could not be found in the ATV9 DTB → `[0x30402004]`-class failure at 9–10% — this is why an Android 9 ROM can't be burned with the stock DTB.)

Also note from the same log family:

- `mmc env offset: 0x27400000` — where the vendor u‑boot keeps its env on the stock DTB layout (vs. `0x4d400000` on the Aidan ATV9 MPT). Writing env to the wrong offset = no boot, silently.
- `env` contains `run update` → `sdc_burn_paras` → burn from FAT `aml_upgrade_package.img`.
- Boot order preference: ROM prefers eMMC boot when the header parses; a half-written bootloader blocks SD fallback (observed).
