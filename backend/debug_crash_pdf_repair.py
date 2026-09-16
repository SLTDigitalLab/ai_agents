"""
Third step in diagnosing the double-free crash: none of get_pixmap's own
parameters avoided it, which points to something malformed in page 2's
actual content (corrupted embedded image/font) rather than a render-setting
issue. This tries PyMuPDF's own repair/cleanup pass (garbage collection +
content-stream cleaning), then retests the crashing page on the repaired
copy, isolated in a subprocess as before.

Usage inside the backend container:
    python3 debug_crash_pdf_repair.py "/path/to/Procurement Manual Part-I-pages-3.pdf"
"""
import subprocess
import sys

REPAIR_WORKER = r'''
import sys
import fitz

src = sys.argv[1]
dst = sys.argv[2]

doc = fitz.open(src)
doc.save(dst, garbage=4, deflate=True, clean=True)
doc.close()
print("repaired ok")
'''

RENDER_WORKER = r'''
import sys, json
import fitz

path = sys.argv[1]
page_num = int(sys.argv[2])

doc = fitz.open(path)
page = doc[page_num]
pix = page.get_pixmap(dpi=150)
print(json.dumps({"ok": True, "width": pix.width, "height": pix.height}))
doc.close()
'''


def run(script: str, *args: str) -> dict:
    proc = subprocess.run(
        [sys.executable, "-c", script, *args],
        capture_output=True,
        text=True,
        timeout=180,
    )
    if proc.returncode != 0:
        return {"ok": False, "returncode": proc.returncode, "stderr_tail": proc.stderr.strip()[-400:]}
    return {"ok": True, "raw": proc.stdout.strip()}


def main():
    src = sys.argv[1]
    repaired = "/tmp/repaired_procurement_pages3.pdf"
    page_num = 1  # page 2, zero-indexed

    print("Step 1: repairing (garbage=4, clean=True) ...")
    repair_result = run(REPAIR_WORKER, src, repaired)
    print(repair_result)
    if not repair_result["ok"]:
        print("Repair pass itself crashed — cannot proceed with this approach.")
        return

    print("\nStep 2: retesting page 2 render on the repaired copy ...")
    render_result = run(RENDER_WORKER, repaired, str(page_num))
    print(render_result)

    print("\n" + "=" * 70)
    if render_result["ok"]:
        print(f"SUCCESS — repaired copy renders page 2 fine. Repaired file: {repaired}")
    else:
        print("Still crashes even after repair — the damage survives PyMuPDF's own cleanup.")
    print("=" * 70)


if __name__ == "__main__":
    main()
