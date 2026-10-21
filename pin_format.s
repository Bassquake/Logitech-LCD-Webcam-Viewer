# Code for the .patch section of LCDWebCam_fixed.exe (VA 0x614000).
# Entered by a jmp that replaces "mov ecx,[ebx+58h]; push esi; push 0" at 0x547020,
# right before ICaptureGraphBuilder2::RenderStream. ebx = graph object
# (+58h ICaptureGraphBuilder2, +5Ch camera filter), esi = LCD renderer filter.
#
# Picks the camera format before RenderStream so the Smart Tee never renegotiates:
#   1. the smallest YUY2 / FORMAT_VideoInfo format at least 320 px wide
#      (YUY2 converts to RGB32 with the stock AVI Decompressor; works for C920 and OBS)
#   2. otherwise the camera's current format (GetFormat -> SetFormat)
# Build: as --32 pin_format.s -o pin_format.o
#        ld -m i386pe --image-base 0x400000 --section-start .text=0x614000 -e pin_format -o pin_format.exe pin_format.o
#        objcopy -O binary -j .text pin_format.exe pin_format.bin
    .intel_syntax noprefix
    .set CoTaskMemFree_IAT,    0x55a97c
    .set PIN_CATEGORY_CAPTURE, 0x55ae88
    .set MEDIATYPE_Video,      0x55add8
    .set FORMAT_VideoInfo,     0x55ae58
    .set RenderStream_resume,  0x547026
    .set MIN_WIDTH,            320

    # frame (ebp): [0..7Fh] VIDEO_STREAM_CONFIG_CAPS, 80h sc, 84h pmt, 88h count, 8Ch caps size
    .text
    .globl pin_format
pin_format:
    pushad
    sub     esp, 0x90
    mov     ebp, esp

    # Run the graph without a reference clock: some sources (OBS Virtual Camera) stamp frames
    # with absolute system time, so a clocked renderer waits for days before showing frame 2.
    mov     dword ptr [ebp+0x84], 0
    lea     eax, [ebp+0x84]
    push    eax
    push    offset IID_IMediaFilter
    mov     ecx, [ebx+0x48]             # IGraphBuilder*
    push    ecx
    mov     eax, [ecx]
    call    [eax]                       # QueryInterface
    test    eax, eax
    js      1f
    mov     ecx, [ebp+0x84]
    push    0
    push    ecx
    mov     eax, [ecx]
    call    [eax+0x20]                  # SetSyncSource(NULL)
    mov     ecx, [ebp+0x84]
    push    ecx
    mov     eax, [ecx]
    call    [eax+0x08]                  # Release
1:
    xor     eax, eax
    mov     [ebp+0x80], eax
    lea     eax, [ebp+0x80]
    push    eax
    push    offset IID_IAMStreamConfig
    push    dword ptr [ebx+0x5c]
    push    MEDIATYPE_Video
    push    PIN_CATEGORY_CAPTURE
    mov     ecx, [ebx+0x58]
    push    ecx
    mov     eax, [ecx]
    call    [eax+0x18]                  # FindInterface
    test    eax, eax
    js      done
    mov     edi, [ebp+0x80]             # IAMStreamConfig*
    test    edi, edi
    jz      done

    xor     esi, esi                    # best AM_MEDIA_TYPE*
    xor     eax, eax
    mov     [ebp+0x88], eax
    mov     [ebp+0x8c], eax
    lea     eax, [ebp+0x8c]
    push    eax
    lea     eax, [ebp+0x88]
    push    eax
    push    edi
    mov     eax, [edi]
    call    [eax+0x14]                  # GetNumberOfCapabilities
    test    eax, eax
    js      fallback
    cmp     dword ptr [ebp+0x8c], 0x80
    ja      fallback

    xor     ebx, ebx                    # index
next_cap:
    cmp     ebx, [ebp+0x88]
    jge     chosen
    mov     dword ptr [ebp+0x84], 0
    push    ebp                         # caps buffer
    lea     eax, [ebp+0x84]
    push    eax
    push    ebx
    push    edi
    mov     eax, [edi]
    call    [eax+0x18]                  # GetStreamCaps
    mov     edx, [ebp+0x84]
    test    eax, eax
    js      reject
    test    edx, edx
    jz      advance
    cmp     dword ptr [edx+0x10], 0x32595559    # MEDIASUBTYPE_YUY2
    jne     reject
    cmp     dword ptr [edx+0x14], 0x00100000
    jne     reject
    cmp     dword ptr [edx+0x18], 0xaa000080
    jne     reject
    cmp     dword ptr [edx+0x1c], 0x719b3800
    jne     reject
    mov     eax, [edx+0x2c]                     # formattype == FORMAT_VideoInfo
    cmp     eax, [FORMAT_VideoInfo]
    jne     reject
    mov     eax, [edx+0x30]
    cmp     eax, [FORMAT_VideoInfo+4]
    jne     reject
    mov     eax, [edx+0x34]
    cmp     eax, [FORMAT_VideoInfo+8]
    jne     reject
    mov     eax, [edx+0x38]
    cmp     eax, [FORMAT_VideoInfo+12]
    jne     reject
    mov     ecx, [edx+0x44]                     # pbFormat
    test    ecx, ecx
    jz      reject
    cmp     dword ptr [edx+0x40], 0x58          # cbFormat >= sizeof(VIDEOINFOHEADER)
    jb      reject
    mov     ecx, [ecx+0x34]                     # bmiHeader.biWidth
    cmp     ecx, MIN_WIDTH
    jl      reject
    test    esi, esi
    jz      take
    mov     eax, [esi+0x44]
    cmp     ecx, [eax+0x34]
    jge     reject
    push    edx
    push    esi
    call    free_mt
    pop     edx
take:
    mov     esi, edx
    jmp     advance
reject:
    push    edx
    call    free_mt
advance:
    inc     ebx
    jmp     next_cap

chosen:
    test    esi, esi
    jz      fallback
    push    esi
    push    edi
    mov     eax, [edi]
    call    [eax+0x0c]                  # SetFormat(best)
    push    esi
    call    free_mt
    jmp     release

fallback:
    mov     dword ptr [ebp+0x84], 0
    lea     eax, [ebp+0x84]
    push    eax
    push    edi
    mov     eax, [edi]
    call    [eax+0x10]                  # GetFormat
    test    eax, eax
    js      release
    push    dword ptr [ebp+0x84]
    push    edi
    mov     eax, [edi]
    call    [eax+0x0c]                  # SetFormat(current)
    push    dword ptr [ebp+0x84]
    call    free_mt

release:
    push    edi
    mov     eax, [edi]
    call    [eax+0x08]                  # Release
done:
    lea     esp, [ebp+0x90]
    popad
    mov     ecx, [ebx+0x58]             # replaced instructions
    push    esi
    push    0
    jmp     RenderStream_resume

# void __stdcall free_mt(AM_MEDIA_TYPE*)  (DeleteMediaType)
free_mt:
    push    esi
    mov     esi, [esp+8]
    test    esi, esi
    jz      1f
    push    dword ptr [esi+0x44]
    call    [CoTaskMemFree_IAT]
    mov     eax, [esi+0x3c]
    test    eax, eax
    jz      2f
    push    eax
    mov     eax, [eax]
    call    [eax+0x08]
2:
    push    esi
    call    [CoTaskMemFree_IAT]
1:
    pop     esi
    ret     4

# Cameras without IAMVideoProcAmp / IAMVideoControl (e.g. OBS Virtual Camera).
# Originally a failed QueryInterface(IAMVideoControl) jumped past AddFilter, so the camera was
# never in the graph and RenderStream failed with E_INVALIDARG; the ProcAmp pointer (+60h)
# was also used unchecked.
#
# Entered from 0x546f90 (replaces "mov ecx,[ebx+28h]; mov edi,[ebx+60h]"); the
# IAMVideoControl-failure branch at 0x546f72 is retargeted here too.
    .globl procamp_check
procamp_check:
    mov     ecx, [ebx+0x28]
    mov     edi, [ebx+0x60]             # IAMVideoProcAmp*
    test    edi, edi
    jz      1f
    jmp     0x546f96                    # query brightness/contrast ranges as before
1:
    mov     ecx, ebx
    call    0x546e47                    # zero the ranges
    jmp     0x546fdf                    # AddFilter(camera)

# Brightness setter (0x546e1d) used +60h unchecked. Entered from 0x546e35
# (replaces "mov eax,[ecx+60h]; push 1"); ecx = graph object, edx = value.
    .globl brightness_check
brightness_check:
    mov     eax, [ecx+0x60]
    test    eax, eax
    jz      0x546e2b                    # return 1 (S_FALSE) like an out-of-range value
    push    1
    jmp     0x546e3a

    .balign 4
IID_IAMStreamConfig:
    .long   0xc6e13340
    .short  0x30ac, 0x11d0
    .byte   0xa1, 0x8c, 0x00, 0xa0, 0xc9, 0x11, 0x89, 0x56
IID_IMediaFilter:
    .long   0x56a86899
    .short  0x0ad4, 0x11ce
    .byte   0xb0, 0x3a, 0x00, 0x20, 0xaf, 0x0b, 0xa7, 0x70
