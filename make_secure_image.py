#!/usr/bin/env python3
"""Turn a plain stock Amlogic USB-Burning-Tool image into the secure,
tool image that a fused HG680P (secure boot ON) will accept.

What it does
------------
  * Secure chip (0xc8100228 = 0xbdfd21bc) rejects the plain @AML BL2, so
    USB/DDR gets the encrypted 49,152-byte BL2 and is mirrored into USB/DDR_ENC.
  * The board's own u-boot from USB/UBOOT is kept and mirrored into USB/UBOOT_ENC.
  * Optionally replaces PARTITION/logo and regenerates VERIFY/logo.
  * Descriptors preserved byte-for-byte (ordinal, type); only offset and size
    are patched; num, imageSz and ~crc32 are recomputed.

Requirements
------------
  pip install pyamlboot

Usage
-----
  python make_secure_image.py --src "ATV FW HG680P.img" --bl2 final_DDR_ENC.bin
  python make_secure_image.py --src "ATV FW HG680P.img" --bl2 final_DDR_ENC.bin \\
                               --logo logo_new_blob.bin --out hg680p_secure.img

Where to get final_DDR_ENC.bin
-------------------------------
  Extract the first 49,152 bytes of PARTITION/bootloader from the stock image,
  OR take it from FarelRA's revive-dead-emmc-bootloader/u-boot.bin at offset 0x200.
  sha256 should start with 90d5f0dd.
"""
import argparse
import hashlib
import os
import struct
import sys
import zlib


def image_crc(path):
    f = open(path, 'rb')
    f.seek(4)
    c = 0
    while True:
        b = f.read(1 << 22)
        if not b:
            break
        c = zlib.crc32(b, c)
    f.close()
    return (c ^ 0xFFFFFFFF) & 0xFFFFFFFF


def load_items(path):
    try:
        from amlimage import AmlImagePack
    except ImportError:
        sys.exit('pyamlboot not found — run: pip install pyamlboot')
    return {('%s/%s' % (it.main_type(), it.sub_type())): it.read()
            for it in AmlImagePack(path).items()}


def pick(path, what):
    if not path:
        return None
    if not os.path.isfile(path):
        sys.exit('%s not found: %s' % (what, path))
    return open(path, 'rb').read()


def main():
    ap = argparse.ArgumentParser(
        description='Patch a stock Amlogic image for secure-boot HG680P')
    ap.add_argument('--src', required=True,
                    help='Source .img (stock HG680P firmware)')
    ap.add_argument('--out',
                    help='Output path (default: <src-dir>/hg680p_secure.img)')
    ap.add_argument('--bl2', required=True,
                    help='Encrypted BL2 blob, exactly 49152 bytes (sha256 starts 90d5f0dd)')
    ap.add_argument('--logo',
                    help='Optional raw logo partition blob to inject')
    ap.add_argument('--uboot',
                    help='Override u-boot blob (default: use USB/UBOOT from src)')
    ap.add_argument('--force', action='store_true',
                    help='Overwrite output if it exists')
    a = ap.parse_args()

    src_path = os.path.abspath(a.src)
    out_path = os.path.abspath(a.out or os.path.join(
        os.path.dirname(src_path), 'hg680p_secure.img'))
    if out_path == src_path:
        sys.exit('refusing to overwrite the source image; pass a different --out')
    if os.path.exists(out_path) and not a.force:
        sys.exit('%s exists (use --force to replace)' % out_path)

    bl2 = pick(a.bl2, 'BL2')
    if len(bl2) != 49152:
        sys.exit('BL2 must be 49152 bytes, got %d' % len(bl2))
    h = hashlib.sha256(bl2).hexdigest()
    if not h.startswith('90d5f0dd'):
        print('WARNING: BL2 sha256 %s does not match the known-good value (90d5f0dd...)' % h,
              file=sys.stderr)

    logo = pick(a.logo, 'logo') if a.logo else None

    print('SRC  : %s (%d bytes)' % (src_path, os.path.getsize(src_path)))
    src_items = load_items(src_path)

    if 'USB/UBOOT' not in src_items and not a.uboot:
        sys.exit('source image has no USB/UBOOT item; pass --uboot <path>')
    uboot = pick(a.uboot, 'u-boot') if a.uboot else src_items['USB/UBOOT']

    print('BL2  : %d  sha256=%s' % (len(bl2), hashlib.sha256(bl2).hexdigest()))
    print('UBOOT: %d  sha256=%s  (from %s)' % (
        len(uboot), hashlib.sha256(uboot).hexdigest(), a.uboot or 'src USB/UBOOT'))
    if logo is not None:
        print('LOGO : %d  sha256=%s' % (len(logo), hashlib.sha256(logo).hexdigest()))
    else:
        print('LOGO : unchanged')

    overrides = {
        ('USB', 'DDR'):      bl2,
        ('USB', 'DDR_ENC'):  bl2,
        ('USB', 'UBOOT'):    uboot,
        ('USB', 'UBOOT_ENC'): uboot,
    }
    if logo is not None:
        overrides[('PARTITION', 'logo')] = logo
        overrides[('VERIFY', 'logo')] = (
            'sha1sum %s' % hashlib.sha1(logo).hexdigest()).encode('latin1')

    src = open(src_path, 'rb')
    head = bytearray(src.read(0x40))
    stored_crc, ver, magic = struct.unpack('<3I', head[:12])
    isz, = struct.unpack('<Q', head[12:20])
    align, num = struct.unpack('<2I', head[20:28])
    assert magic == 0x27B51956, 'not an Amlogic item container'
    assert align == 4, 'unexpected alignment %d' % align
    assert isz == os.path.getsize(src_path), 'imageSz does not match file size'

    src.seek(0x40)
    tbl = bytearray(src.read(0x240 * num))
    assert len(tbl) == 0x240 * num

    items = []
    for i in range(num):
        d = bytearray(tbl[i * 0x240:(i + 1) * 0x240])
        off, sz = struct.unpack('<2Q', d[0x10:0x20])
        m = d[0x20:0x120].rstrip(b'\x00').decode('latin1')
        s = d[0x120:0x220].rstrip(b'\x00').decode('latin1')
        items.append({'main': m, 'sub': s, 'off': off, 'sz': sz, 'desc': d})
    print('SRC  : %d items' % num)

    have = set((i['main'], i['sub']) for i in items)
    for k in overrides:
        if k not in have:
            new = bytearray(0x240)
            struct.pack_into('<I', new, 0x00, len(items))
            new[0x20:0x120] = k[0].encode('latin1').ljust(0x100, b'\x00')
            new[0x120:0x220] = k[1].encode('latin1').ljust(0x100, b'\x00')
            items.append({'main': k[0], 'sub': k[1], 'off': 0, 'sz': 0, 'desc': new})
            print('  appended %s/%s' % k)
    num = len(items)
    struct.pack_into('<I', head, 0x18, num)

    new_tbl = bytearray(0x240 * num)
    out_off = 0x40 + 0x240 * num
    plan = []
    for i, it in enumerate(items):
        key = (it['main'], it['sub'])
        if key in overrides:
            content = overrides[key]
        else:
            src.seek(it['off'])
            content = src.read(it['sz'])
            assert len(content) == it['sz'], 'short read %s/%s' % key
        pad = (-out_off) % align
        out_off += pad
        d = bytearray(it['desc'])
        struct.pack_into('<Q', d, 0x10, out_off)
        struct.pack_into('<Q', d, 0x18, len(content))
        new_tbl[i * 0x240:(i + 1) * 0x240] = d
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
    src.close()

    with open(out_path, 'r+b') as o:
        o.seek(0x0C)
        o.write(struct.pack('<Q', out_off))
        o.flush()
        c = image_crc(out_path)
        o.seek(0)
        o.write(struct.pack('<I', c))

    print()
    print('OUT  : %s (%d bytes)' % (out_path, os.path.getsize(out_path)))
    with open(out_path, 'rb') as f:
        h = f.read(0x40)
        s_crc, = struct.unpack('<I', h[:4])
        s_isz, = struct.unpack('<Q', h[12:20])
        s_num, = struct.unpack('<I', h[24:28])
    calc = image_crc(out_path)
    print('header: crc=0x%08x calc=0x%08x [%s]  imageSz=%d  size_ok=%s  num=%d' % (
        s_crc, calc, 'OK' if s_crc == calc else 'FAIL',
        s_isz, s_isz == os.path.getsize(out_path), s_num))
    print()
    print('%-10s %-14s %10s  %s' % ('main', 'sub', 'size', 'sha256'))
    try:
        from amlimage import AmlImagePack
        for it in AmlImagePack(out_path).items():
            d = it.read()
            print('%-10s %-14s %10d  %s' % (it.main_type(), it.sub_type(),
                                              len(d), hashlib.sha256(d).hexdigest()[:32]))
    except Exception as e:
        print('(could not list output items: %s)' % e)
    print()
    print('sha256(image) = %s' % hashlib.sha256(open(out_path, 'rb').read()).hexdigest())


if __name__ == '__main__':
    main()
