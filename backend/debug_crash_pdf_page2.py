"""
Follow-up to debug_crash_pdf.py — page 2 of Procurement Manual Part-I-pages-3.pdf
crashes specifically on page.get_pixmap(dpi=150). This tries a set of alternate
get_pixmap parameter combos against that same page, each in its own subprocess,
to see if any avoid the native crash.

Usage inside the backend container:
    python3 debug_crash_pdf_page2.py "/path/to/Procurement Manual Part-I-pages-3.pdf"
"""
import subprocess
import sys

WORKER = r'''
import sys, json
import fitz

path = sys.argv[1]
page_num = int(sys.argv[2])
kwargs = json.loads(sys.argv[3])

doc = fitz.open(path)
page = doc[page_num]

if "colorspace" in kwargs:
    kwargs["colorspace"] = {"rgb": fitz.csRGB, "gray": fitz.csGRAY}[kwargs["colorspace"]]

pix = page.get_pixmap(**kwargs)
print(json.dumps({"ok": True, "width": pix.width, "height": pix.height}))
doc.close()
'''

VARIANTS = [
    {"dpi": 150},                                   # baseline, expected to crash
    {"dpi": 100},
    {"dpi": 72},
    {"dpi": 150, "annots": False},
    {"dpi": 150, "alpha": False},
    {"dpi": 150, "alpha": True},
    {"dpi": 150, "colorspace": "rgb"},
    {"dpi": 150, "colorspace": "gray"},
]


def run_variant(path: str, page_num: int, kwargs: dict) -> dict:
    import json as _json
    proc = subprocess.run(
        [sys.executable, "-c", WORKER, path, str(page_num), _json.dumps(kwargs)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if proc.returncode != 0:
        return {"ok": False, "returncode": proc.returncode, "stderr_tail": proc.stderr.strip()[-300:]}
    return {"ok": True, "raw": proc.stdout.strip()}


def main():
    path = sys.argv[1]
    page_num = 1  # page 2, zero-indexed

    print(f"File: {path}")
    print(f"Testing page {page_num + 1} across {len(VARIANTS)} get_pixmap variants")
    print("-" * 70)

    working = []
    for kwargs in VARIANTS:
        result = run_variant(path, page_num, kwargs)
        status = "OK" if result["ok"] else "CRASH"
        print(f"kwargs={kwargs}  ->  {status}  {result}")
        if result["ok"]:
            working.append(kwargs)

    print("-" * 70)
    if working:
        print(f"WORKING VARIANTS ({len(working)}): {working}")
    else:
        print("No variant avoided the crash.")


if __name__ == "__main__":
    main()
