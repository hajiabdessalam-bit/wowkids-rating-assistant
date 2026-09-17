from pathlib import Path

PATH = Path(__file__).with_name("wkcommon.py")
TAIL_MARKER = "win32gui.ReleaseDC(win32gui.GetDesktopWindow(), desktop_dc)"
COMPLETE_MARKER = 'return "capture failed: {}".format(exc)'

TAIL = '''\n\n        saved = _bitmap_to_png(bits, width, height, path)\n        if not saved:\n            bmp_path = os.path.splitext(path)[0] + ".bmp"\n            bitmap.SaveBitmapFile(save_dc, bmp_path)\n            path = bmp_path\n            mode += "+BMP"\n\n        save_dc.DeleteDC()\n        mfc_dc.DeleteDC()\n        win32gui.ReleaseDC(handle, window_dc)\n        black = _looks_black(bits, width, height)\n        return "{} {}{}".format(\n            mode, "BLACK?" if black else "ok", " -> " + os.path.basename(path))\n    except Exception as exc:\n        return "capture failed: {}".format(exc)\n'''


def main() -> int:
    if not PATH.exists():
        print("wkcommon.py not found")
        return 1

    text = PATH.read_text(encoding="utf-8")
    if COMPLETE_MARKER in text:
        print("wkcommon.py is complete")
        return 0

    stripped = text.rstrip()
    if not stripped.endswith(TAIL_MARKER):
        print("wkcommon.py is incomplete in an unexpected place; refusing to modify it")
        return 2

    PATH.write_text(stripped + TAIL, encoding="utf-8")
    print("Repaired truncated wkcommon.py tail")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
