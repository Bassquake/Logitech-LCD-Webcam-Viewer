"""Patch LCDWebCam.exe 9.4.49.0 (sha256 9fdaef09...586e) so selecting a webcam works.

Bug 1 (root cause): the graph is built with
    ICaptureGraphBuilder2::RenderStream(PIN_CATEGORY_CAPTURE, MEDIATYPE_Video, cam, NULL, LCDRenderer)
without fixing the camera's output format. RenderStream inserts a Smart Tee, the downstream
converter picks a different subtype (C920: MJPG, OBS Virtual Camera: I420), the Smart Tee
tries to reconnect its input, that fails, and the camera is left disconnected while
RenderStream still returns S_OK. The source-less graph immediately posts EC_COMPLETE.
Fix (pin_format.s, new .patch section): before RenderStream pick the smallest YUY2 format
>= 320 px wide via IAMStreamConfig, falling back to SetFormat(GetFormat()).

Bug 2 (the crash): the graph-event loop at 0x546cc0 calls the dialog's callback, which on
EC_COMPLETE tears the graph down and NULLs the IMediaEventEx pointer (this+0x50). The loop then
dereferences it at 0x546d3b -> access violation. Fix: re-check the pointer and leave the loop
(small cave in the zero padding at the end of .text, VA 0x5594a0).

Bug 3 (cameras without IAMVideoControl / IAMVideoProcAmp, e.g. OBS Virtual Camera): a failed
QueryInterface(IAMVideoControl) jumped past AddFilter, so the camera was never in the graph
(RenderStream -> E_INVALIDARG); IAMVideoProcAmp was used unchecked; and the open routine returned
the contrast setter's S_FALSE, so the graph was never Run. Fix: skip only SetMode, guard ProcAmp
(procamp_check / brightness_check in pin_format.s), and treat brightness/contrast as non-fatal.

Bug 4 (OBS Virtual Camera shows no picture): OBS stamps samples with absolute system time, so the
clocked LCD renderer waits days for frame 2. Fix: pin_format also calls
IMediaFilter::SetSyncSource(NULL) so frames render as they arrive (normal for live preview).

Usage: python patch_lcdwebcam.py LCDWebCam.exe LCDWebCam_fixed.exe
"""
import hashlib
import struct
import sys

ORIG_SHA256 = "9fdaef0910a4d6be2f05d1db154d5450438ecd8bc7d32035866f5475aaeb586e"

TEXT_VA, TEXT_RAW = 0x401000, 0x400
NULLCHECK_VA = 0x5594A0
PATCH_RVA = 0x214000                      # first free RVA (old SizeOfImage)
PATCH_VA = 0x400000 + PATCH_RVA
PROCAMP_CHECK_VA = 0x614211               # labels in pin_format.s
BRIGHTNESS_CHECK_VA = 0x61422C
CO_TASK_MEM_FREE_IAT = 0x55A97C           # hard-coded in pin_format.s

# Assembled pin_format.s (linked at 0x614000; see the build lines in that file).
PIN_FORMAT = bytes.fromhex(
    "6081ec9000000089e5c78584000000000000008d85840000005068504261008b"
    "4b48518b01ff1085c0781a8b8d840000006a00518b01ff50208b8d8400000051"
    "8b01ff500831c08985800000008d8580000000506840426100ff735c68d8ad55"
    "006888ae55008b4b58518b01ff501885c00f885e0100008bbd8000000085ff0f"
    "845001000031f631c089858800000089858c0000008d858c000000508d858800"
    "000050578b07ff501485c00f88ec00000081bd8c000000800000000f87dc0000"
    "0031db3b9d880000000f8dbb000000c7858400000000000000558d8584000000"
    "5053578b07ff50188b958400000085c00f888800000085d20f8486000000817a"
    "10595559327577817a1400001000756e817a18800000aa7565817a1c00389b71"
    "755c8b422c3b0558ae550075518b42303b055cae550075468b42343b0560ae55"
    "00753b8b42383b0564ae550075308b4a4485c97429837a405872238b493481f9"
    "400100007c1885f674108b46443b48347d0c5256e86e0000005a89d6eb0652e8"
    "6300000043e939ffffff85f6740f56578b07ff500c56e84c000000eb32c78584"
    "000000000000008d858400000050578b07ff501085c07817ffb584000000578b"
    "07ff500cffb584000000e818000000578b07ff50088da590000000618b4b5856"
    "6a00e93f2ef3ff568b74240885f6741dff7644ff157ca955008b463c85c07406"
    "508b00ff500856ff157ca955005ec204008b4b288b7b6085ff7405e9762df3ff"
    "89d9e8202cf3ffe9b32df3ff8b416085c00f84f42bf3ff6a01e9fc2bf3ff6690"
    "4033e1c6ac30d011a18c00a0c91189569968a856d40ace11b03a0020af0ba770"
)


def off(va):
    return va - TEXT_VA + TEXT_RAW


def rel32(src_next, dst):
    return struct.pack("<i", dst - src_next)


def align(x, a):
    return (x + a - 1) // a * a


def find_iat(data, dll, func):
    """Return the VA of the IAT slot for dll!func (PE32, by-name import)."""
    pe = struct.unpack_from("<I", data, 0x3C)[0]
    nsec = struct.unpack_from("<H", data, pe + 6)[0]
    opt = pe + 0x18
    sect = opt + struct.unpack_from("<H", data, pe + 0x14)[0]
    secs = [struct.unpack_from("<8sIIII", data, sect + i * 40) for i in range(nsec)]

    def r2o(rva):
        for _, vsz, va, rsz, raw in secs:
            if va <= rva < va + max(vsz, rsz):
                return rva - va + raw
        raise ValueError(hex(rva))

    imp_rva = struct.unpack_from("<I", data, opt + 0x60 + 8)[0]
    p = r2o(imp_rva)
    while True:
        oft, _, _, name_rva, ft = struct.unpack_from("<5I", data, p)
        if not name_rva:
            break
        name = data[r2o(name_rva):].split(b"\0", 1)[0].decode()
        if name.lower() == dll.lower():
            i = 0
            while True:
                thunk = struct.unpack_from("<I", data, r2o(oft) + i * 4)[0]
                if not thunk:
                    break
                if not thunk & 0x80000000:
                    fn = data[r2o(thunk) + 2:].split(b"\0", 1)[0].decode()
                    if fn == func:
                        return 0x400000 + ft + i * 4
                i += 1
        p += 20
    raise KeyError(func)


def main(src, dst):
    data = bytearray(open(src, "rb").read())
    if hashlib.sha256(data).hexdigest() != ORIG_SHA256:
        sys.exit("unexpected input file (hash mismatch)")

    # Original bytes we are about to overwrite.
    assert data[off(0x547020):off(0x547026)] == bytes.fromhex("8b4b58566a00")
    assert data[off(0x546D2F):off(0x546D35)] == bytes.fromhex("8b47508d5508")
    assert data[off(0x546F72):off(0x546F74)] == bytes.fromhex("787c")
    assert data[off(0x546F90):off(0x546F96)] == bytes.fromhex("8b4b288b7b60")
    assert data[off(0x546E35):off(0x546E3A)] == bytes.fromhex("8b41606a01")
    assert data[off(0x546DB0):off(0x546DB2)] == bytes.fromhex("8bf0")
    assert find_iat(data, "ole32.dll", "CoTaskMemFree") == CO_TASK_MEM_FREE_IAT

    pe = struct.unpack_from("<I", data, 0x3C)[0]
    opt = pe + 0x18
    opt_size = struct.unpack_from("<H", data, pe + 0x14)[0]
    nsec = struct.unpack_from("<H", data, pe + 6)[0]
    sect = opt + opt_size

    # Drop the (now invalid) Authenticode signature so the new section can go at the end.
    sec_dir = opt + 0x80
    cert_off, cert_len = struct.unpack_from("<II", data, sec_dir)
    if cert_off:
        assert cert_off + cert_len >= len(data) - 8
        del data[cert_off:]
        struct.pack_into("<II", data, sec_dir, 0, 0)

    # --- NULL check on IMediaEventEx in the event loop (.text padding) ------
    nullcheck = (
        b"\x8B\x47\x50"                                     # mov eax,[edi+50h]
        + b"\x85\xC0"                                       # test eax,eax
        + b"\x0F\x84" + rel32(NULLCHECK_VA + 11, 0x546D4A)  # jz -> leave loop
        + b"\x8D\x55\x08"                                   # lea edx,[ebp+8] (replaced)
        + b"\xE9" + rel32(NULLCHECK_VA + 19, 0x546D35)      # jmp back
    )
    region = data[off(NULLCHECK_VA):off(NULLCHECK_VA) + len(nullcheck)]
    assert region == bytes(len(region)), "cave not empty"
    data[off(NULLCHECK_VA):off(NULLCHECK_VA) + len(nullcheck)] = nullcheck
    data[off(0x546D2F):off(0x546D35)] = b"\xE9" + rel32(0x546D34, NULLCHECK_VA) + b"\x90"

    text_hdr = sect
    assert data[text_hdr:text_hdr + 5] == b".text"
    struct.pack_into("<I", data, text_hdr + 8, struct.unpack_from("<I", data, text_hdr + 16)[0])

    # --- New .patch section holding pin_format -------------------------------
    file_align = struct.unpack_from("<I", data, opt + 0x24)[0]
    size_of_headers = struct.unpack_from("<I", data, opt + 0x3C)[0]
    new_hdr = sect + nsec * 40
    assert new_hdr + 40 <= size_of_headers and data[new_hdr:new_hdr + 40] == bytes(40)
    assert struct.unpack_from("<I", data, opt + 0x38)[0] == PATCH_RVA      # SizeOfImage
    assert len(data) % file_align == 0
    raw_ptr, raw_size = len(data), align(len(PIN_FORMAT), file_align)
    data += PIN_FORMAT + bytes(raw_size - len(PIN_FORMAT))
    struct.pack_into("<8sIIIIIIHHI", data, new_hdr, b".patch", len(PIN_FORMAT), PATCH_RVA,
                     raw_size, raw_ptr, 0, 0, 0, 0, 0x60000020)            # code, RX
    struct.pack_into("<H", data, pe + 6, nsec + 1)
    struct.pack_into("<I", data, opt + 0x38, PATCH_RVA + 0x1000)           # SizeOfImage
    size_of_code = struct.unpack_from("<I", data, opt + 0x04)[0]
    struct.pack_into("<I", data, opt + 0x04, size_of_code + raw_size)
    data[off(0x547020):off(0x547026)] = b"\xE9" + rel32(0x547025, PATCH_VA) + b"\x90"

    # Cameras without IAMVideoControl / IAMVideoProcAmp.
    data[off(0x546F72):off(0x546F74)] = b"\x78\x1C"                       # js 0x546f90 (skip SetMode only)
    data[off(0x546F90):off(0x546F96)] = b"\xE9" + rel32(0x546F95, PROCAMP_CHECK_VA) + b"\x90"
    data[off(0x546E35):off(0x546E3A)] = b"\xE9" + rel32(0x546E3A, BRIGHTNESS_CHECK_VA)
    data[off(0x546DB0):off(0x546DB2)] = b"\x33\xF6"                       # xor esi,esi: contrast is non-fatal

    # PE checksum.
    csum_off = opt + 0x40
    struct.pack_into("<I", data, csum_off, 0)
    s = 0
    for (w,) in struct.iter_unpack("<H", bytes(data) + b"\0" * (len(data) & 1)):
        s = (s + w) & 0xFFFFFFFF
        s = (s & 0xFFFF) + (s >> 16)
    s = (s & 0xFFFF) + (s >> 16)
    struct.pack_into("<I", data, csum_off, (s & 0xFFFF) + len(data))

    open(dst, "wb").write(data)
    print(f"null check @ {NULLCHECK_VA:#x}, pin_format {len(PIN_FORMAT)} bytes @ {PATCH_VA:#x}\n"
          f"wrote {dst}")


if __name__ == "__main__":
    main(*sys.argv[1:3])
