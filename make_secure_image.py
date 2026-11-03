#!/usr/bin/env python3
"""Turn a plain stock Amlogic USB-Burning-Tool image into the secure-boot
image that a fused HG680P (secure boot ON) will accept.

What it does
------------
  * Reads the stock image's item table (Amlogic "AML" format, v2 descriptors).
  * Takes the encrypted 49,152-byte BL2 (by default it extracts the first
    49,152 bytes of the stock PARTITION/bootloader item; override with --bl2)
    and installs it as USB/DDR and mirrors it into USB/DDR_ENC.
  * Takes the matching u-boot (by default the stock USB/UBOOT item; override
    with --uboot) and mirrors it into USB/UBOOT_ENC.
  * Optionally replaces PARTITION/logo and regenerates VERIFY/logo.
  * Descriptors are preserved byte-for-byte (ordinal, file_type, flags);
    only offset_in_img and size are patched. num, imageSz and the header
    CRC-32 are recomputed.

No third-party dependencies. Pure Python 3 standard library.

Usage
-----
  python make_secure_image.py --src "ATV FW HG680P.img" --out hg680p_secure.img
  python make_secure_image.py --src "ATV FW HG680P.img" --out hg680p_secure.img \\
                               --bl2 final_DDR_ENC.bin --logo logo_new_blob.bin

Where the BL2 comes from (if not passed with --bl2)
----------------------------------------------------
  PARTITION/bootloader of the stock image is 1,146,880 bytes:

      [0x000000 – 0x00BFFF]  49,152 B   encrypted BL2   -> USB/DDR + USB/DDR_ENC
      [0x00C000 – end     ]  1,097,728 B  matching u-boot -> USB/UBOOT + USB/UBOOT_ENC

  The script extracts both automatically. The BL2 sha256 should start with
  90d5f0dd and the u-boot with a9343ac6 for the known-good HG680P ATV FW
  HG680P build; a mismatch prints a warning but is not fatal, because
  different firmware builds may differ (then you're off the tested path).
  Alternatively, FarelRA's revive-dead-emmc-bootloader/u-boot.bin at offset
  0x200 also contains the same encrypted BL2.
"""
import argparse
import hashlib
import os
import struct
import sys
import zlib

MAGIC = 0x27B51956
HEADER = 0x40
DESC = 0x240          # v2 item descriptor size
OFF_OFF, OFF_SIZE = 0x10, 0x18
MAIN, SUB = 0x20, 0x120
MAIN_LEN = 0x100
ALIGN = 4
BL2_SIZE = 49152
KNOWN_GOOD_BL2 = '90d5f0dd'
KNOWN_GOOD_UBOOT = 'a9343ac6'


def read_image(path):
    """Parse an Amlogic AML image. Returns (head_bytes, align, items).

    items: list of dicts with keys main, sub, off, size, descriptor (bytearray), data (bytes).
    """
    f = open(path, 'rb')
    head = bytearray(f.read(HEADER))
    assert len(head) == HEADER, 'truncated image header'
    magic, = struct.unpack_from('<I', head, 0x08)
    assert magic == MAGIC, 'not an Amlogic item container (magic 0x%08x)' % magic
    isz, align, num = struct.unpack_from('<QII', head, 0x0C)
    ver, = struct.unpack_from('<I', head, 0x04)
    assert ver == 2, 'unsupported image version %d (only v2, 0x240-byte descriptors)' % ver
    assert align == ALIGN, 'unexpected alignment %d' % align
    assert isz == os.path.getsize(path), 'imageSz (%d) does not match file size (%d)' % (isz, os.path.getsize(path))
    tbl = f.read(DESC * num)
    assert len(tbl) == DESC * num, 'truncated descriptor table'
    items = []
    for i in range(num):
        d = bytearray(tbl[i * DESC:(i + 1) * DESC])
        off, sz = struct.unpack_from('<2Q', d, OFF_OFF)
        m = d[MAIN:MAIN + MAIN_LEN].rstrip(b'\x00').decode('latin1')
        s = d[SUB:SUB + MAIN_LEN].rstrip(b'\x00').decode('latin1')
        f.seek(off)
        data = f.read(sz)
        assert len(data) == sz, 'short read at %s/%s' % (m, s)
        items.append({'main': m, 'sub': s, 'off': off, 'size': sz,
                      'desc': d, 'data': data})
    f.close()
    return head, align, items


def pick(path, what):
    if not path:
        return None
    if not os.path.isfile(path):
        sys.exit('%s not found: %s' % (what, path))
    with open(path, 'rb') as fobj:
        return fobj.read()


def main():
    ap = argparse.ArgumentParser(
        description='Patch a stock Amlogic image for secure-boot HG680P')
    ap.add_argument('--src', required=True,
                    help='Source .img (stock HG680P firmware)')
    ap.add_argument('--out',
                    help='Output path (default: <src-dir>/hg680p_secure.img)')
    ap.add_argument('--bl2',
                    help='Encrypted BL2 blob, exactly 49152 bytes '
                         '(default: first 49152 bytes of src PARTITION/bootloader)')
    ap.add_argument('--logo',
                    help='Optional raw logo partition blob to inject')
    ap.add_argument('--uboot',
                    help='Override u-boot blob (default: USB/UBOOT from src)')
    ap.add_argument('--force', action='store_true',
                    help='Overwrite output if it exists')
    a = ap.parse_args()

    src_path = os.path.abspath(a.src)
    if not os.path.isfile(src_path):
        sys.exit('src not found: %s' % src_path)
    out_path = os.path.abspath(a.out or os.path.join(
        os.path.dirname(src_path), 'hg680p_secure.img'))
    if out_path == src_path:
        sys.exit('refusing to overwrite the source image; pass a different --out')
    if os.path.exists(out_path) and not a.force:
        sys.exit('%s exists (use --force to replace)' % out_path)

    print('SRC  : %s (%d bytes)' % (src_path, os.path.getsize(src_path)))
    head, align, items = read_image(src_path)
    src_items = {(it['main'], it['sub']): it['data'] for it in items}
    print('SRC  : %d items' % len(items))

    # --- BL2 ---------------------------------------------------------------
    if a.bl2:
        bl2 = pick(a.bl2, 'BL2')
        bl2_src = a.bl2
    else:
        if ('PARTITION', 'bootloader') not in src_items:
            sys.exit('src has no PARTITION/bootloader item; pass --bl2 <path>')
        bootloader = src_items[('PARTITION', 'bootloader')]
        if len(bootloader) < BL2_SIZE:
            sys.exit('PARTITION/bootloader too small (%d bytes)' % len(bootloader))
        bl2 = bootloader[:BL2_SIZE]
        bl2_src = 'src PARTITION/bootloader[:49152]'
    if len(bl2) != BL2_SIZE:
        sys.exit('BL2 must be 49152 bytes, got %d' % len(bl2))
    bl2_sha = hashlib.sha256(bl2).hexdigest()
    if not bl2_sha.startswith(KNOWN_GOOD_BL2):
        print('WARNING: BL2 sha256 %s does not match the known-good value '
              '(%s...)' % (bl2_sha, KNOWN_GOOD_BL2), file=sys.stderr)

    # --- u-boot ------------------------------------------------------------
    if ('USB', 'UBOOT') not in src_items and not a.uboot:
        sys.exit('source image has no USB/UBOOT item; pass --uboot <path>')
    uboot = pick(a.uboot, 'u-boot') if a.uboot else src_items[('USB', 'UBOOT')]
    ub_sha = hashlib.sha256(uboot).hexdigest()
    if not ub_sha.startswith(KNOWN_GOOD_UBOOT):
        print('WARNING: u-boot sha256 %s does not match the known-good value '
              '(%s...)' % (ub_sha, KNOWN_GOOD_UBOOT), file=sys.stderr)

    logo = pick(a.logo, 'logo') if a.logo else None

    print('BL2  : %d  sha256=%s  (from %s)' % (len(bl2), bl2_sha, bl2_src))
    print('UBOOT: %d  sha256=%s  (from %s)' % (
        len(uboot), ub_sha, a.uboot or 'src USB/UBOOT'))
    if logo is not None:
        print('LOGO : %d  sha256=%s' % (len(logo), hashlib.sha256(logo).hexdigest()))
    else:
        print('LOGO : unchanged')

    overrides = {
        ('USB', 'DDR'):       bl2,
        ('USB', 'DDR_ENC'):   bl2,
        ('USB', 'UBOOT'):     uboot,
        ('USB', 'UBOOT_ENC'): uboot,
    }
    if logo is not None:
        overrides[('PARTITION', 'logo')] = logo
        overrides[('VERIFY', 'logo')] = (
            'sha1sum %s' % hashlib.sha1(logo).hexdigest()).encode('latin1')

    head = bytearray(head)
    struct.pack_into('<I', head, 0x18, 0)  # patched once final num is known

    have = {(it['main'], it['sub']) for it in items}
    for key in overrides:
        if key not in have:
            new = bytearray(DESC)
            struct.pack_into('<I', new, 0x00, len(items))  # ordinal = append index
            new[MAIN:MAIN + MAIN_LEN] = key[0].encode('latin1').ljust(MAIN_LEN, b'\x00')
            new[SUB:SUB + MAIN_LEN] = key[1].encode('latin1').ljust(MAIN_LEN, b'\x00')
            items.append({'main': key[0], 'sub': key[1], 'off': 0, 'size': 0,
                          'desc': new, 'data': None})
            print('  appended %s/%s' % key)

    num = len(items)
    struct.pack_into('<I', head, 0x18, num)

    new_tbl = bytearray(DESC * num)
    out_off = HEADER + DESC * num
    plan = []
    for i, it in enumerate(items):
        key = (it['main'], it['sub'])
        if key in overrides:
            content = overrides[key]
        else:
            content = it['data']
        pad = (-out_off) % align
        out_off += pad
        d = bytearray(it['desc'])
        struct.pack_into('<Q', d, OFF_OFF, out_off)
        struct.pack_into('<Q', d, OFF_SIZE, len(content))
        new_tbl[i * DESC:(i + 1) * DESC] = d
        plan.append((key, content, out_off))
        out_off += len(content)

    with open(out_path, 'wb') as o:
        o.write(bytes(head))
        o.write(bytes(new_tbl))
        for key, content, off in plan:
            pad = (-o.tell()) % align
            if pad:
                o.write(b'\x00' * pad)
            assert o.tell() == off, 'layout drift at %s/%s' % key
            o.write(content)
        o.truncate(out_off)

    with open(out_path, 'r+b') as o:
        o.seek(0x0C)
        o.write(struct.pack('<Q', out_off))
        o.flush()
        o.seek(4)
        c = 0
        while True:
            b = o.read(1 << 22)
            if not b:
                break
            c = zlib.crc32(b, c)
        o.seek(0)
        o.write(struct.pack('<I', (c ^ 0xFFFFFFFF) & 0xFFFFFFFF))

    # single pass: crc over bytes [4:] and full-file sha256
    img_sha = hashlib.sha256()
    with open(out_path, 'rb') as fin:
        head4 = fin.read(4)          # crc covers bytes [4:]; sha covers all
        img_sha.update(head4)
        c = 0
        while True:
            b = fin.read(1 << 22)
            if not b:
                break
            c = zlib.crc32(b, c)
            img_sha.update(b)
        fin.seek(0)
        h0 = fin.read(HEADER)
    stored_crc, = struct.unpack('<I', h0[:4])
    calc = (c ^ 0xFFFFFFFF) & 0xFFFFFFFF
    s_isz, = struct.unpack('<Q', h0[12:20])
    s_num, = struct.unpack('<I', h0[24:28])
    fsize = os.path.getsize(out_path)
    print()
    print('OUT  : %s (%d bytes)' % (out_path, fsize))
    print('header: crc=0x%08x calc=0x%08x [%s]  imageSz=%d  size_ok=%s  num=%d' % (
        stored_crc, calc, 'OK' if stored_crc == calc else 'FAIL',
        s_isz, s_isz == fsize, s_num))
    print()
    print('%-10s %-14s %10s  %s' % ('main', 'sub', 'size', 'sha256'))
    for key, content, _ in plan:
        print('%-10s %-14s %10d  %s' % (key[0], key[1], len(content),
                                        hashlib.sha256(content).hexdigest()[:32]))
    print()
    print('sha256(image) = %s' % img_sha.hexdigest())


if __name__ == '__main__':
    main()
