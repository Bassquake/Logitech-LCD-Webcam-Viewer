# LCDWebCam fix for the SpacePilot Pro LCD

A binary patch for Logitech's `LCDWebCam.exe` applet (v9.4.49.0), the webcam viewer that runs on the 3Dconnexion SpacePilot Pro's LCD. Unpatched, the applet crashes or shows nothing when you select a camera. Patched, it works with webcams like the Logitech C920 and with virtual cameras like OBS Virtual Camera.

The applet lives in `C:\Program Files\Logitech\GamePanel Software\LCD Manager\Applets\`.

![LCDWebCam on device](https://github.com/Bassquake/Logitech-LCD-Webcam-Viewer/blob/master/assets/lcdwebcam-photo.jpg)

## What was wrong

1. **Crash on camera select.** When the camera's stream ended, the applet's event loop kept using a pointer it had already released (access violation at `0x546d3b`).
2. **Camera disconnected from the pipeline.** The applet never fixed the camera's video format. DirectShow's Smart Tee renegotiated the format, the reconnect failed silently, and the stream ended as soon as it started.
3. **Cameras without image controls ignored.** If a camera had no `IAMVideoControl`, the applet skipped adding it to the graph at all. It also used `IAMVideoProcAmp` without checking for NULL, and treated a failed contrast setting as a failed open, so the graph was never started.
4. **OBS Virtual Camera stuck on one frame.** OBS stamps frames with absolute system time, so the clocked renderer waited days for the second frame.

## What the patch does

- Exits the event loop cleanly if the graph has been torn down.
- Before `RenderStream`, picks the smallest YUY2 format at least 320 px wide, falling back to the camera's current format.
- Adds cameras that lack `IAMVideoControl`/`IAMVideoProcAmp` to the graph, guards the ProcAmp pointer, and makes brightness/contrast failures non-fatal.
- Runs the graph without a reference clock (`SetSyncSource(NULL)`), so frames render as they arrive.

The new code goes in a new `.patch` section (source in `pin_format.s`), plus a small NULL check in the unused padding at the end of `.text`. The Authenticode signature is removed because patching invalidates it.

## Usage

Requires Python 3. The script only accepts the exact original file (SHA-256 `9fdaef09…586e`).

```
python patch_lcdwebcam.py LCDWebCam.exe LCDWebCam_fixed.exe
```

Then close LCD Manager (or end `LCDWebCam.exe`), back up the original, and copy the patched file over `Applets\LCDWebCam.exe`. You'll need admin rights.

## Rebuilding the patch code

`patch_lcdwebcam.py` embeds the assembled bytes of `pin_format.s`. If you change the assembly, rebuild with MinGW binutils and paste the new bytes into `PIN_FORMAT`:

```
as --32 pin_format.s -o pin_format.o
ld -m i386pe --image-base 0x400000 --section-start .text=0x614000 -e pin_format -o pin_format.exe pin_format.o
objcopy -O binary -j .text pin_format.exe pin_format.bin
```

Keep everything up to the end of `IID_IMediaFilter`. Linker padding after it isn't needed. If labels move, update `PROCAMP_CHECK_VA` and `BRIGHTNESS_CHECK_VA` in the script.

## Notes

- OBS only sends live video while its virtual camera is started.
- With OBS (1920×1080 at 60 fps) the applet uses noticeably more CPU than with a regular webcam.
- No Logitech binaries are included in this repository. Patch your own copy.
