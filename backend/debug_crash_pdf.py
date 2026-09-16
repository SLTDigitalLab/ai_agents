"""
One-off diagnostic for the 'free(): double free detected in tcache 2' crash
that reliably kills the backend when ingesting a specific PDF
(Procurement Manual Part-I-pages-3.pdf, SCM OneDrive folder).

Since it's a native (C-level) crash, it can only be isolated by running each
risky operation in its own subprocess — if a subprocess crashes, only it
dies, and this driver can report exactly which page/operation did it.

Usage inside the backend container:
    python3 debug_crash_pdf.py /path/to/Procurement Manual Part-I-pages-3.pdf
"""
import subprocess
import sys

WORKER = r'''
import sys, json
import fitz

path = sys.argv[1]
page_num = int(sys.argv[2])
op = sys.argv[3]

doc = fitz.open(path)
page = doc[page_num]

if op == "get_images":
    result = page.get_images(full=True)
    print(json.dumps({"ok": True, "count": len(result)}))
elif op == "get_drawings":
    result = page.get_drawings()
    print(json.dumps({"ok": True, "count": len(result)}))
elif op == "get_pixmap":
    pix = page.get_pixmap(dpi=150)
    print(json.dumps({"ok": True, "width": pix.width, "height": pix.height, "samples": len(pix.samples)}))

doc.close()
'''


def run_op(path: str, page_num: int, op: str) -> dict:
    proc = subprocess.run(
        [sys.executable, "-c", WORKER, path, str(page_num), op],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if proc.returncode != 0:
        return {
            "ok": False,
            "returncode": proc.returncode,
            "signal": -proc.returncode if proc.returncode < 0 else None,
            "stderr_tail": proc.stderr.strip()[-500:],
        }
    return {"ok": True, "raw": proc.stdout.strip()}


def main():
    path = sys.argv[1]

    import fitz
    doc = fitz.open(path)
    page_count = len(doc)
    doc.close()
    print(f"File: {path}")
    print(f"Page count: {page_count}")
    print("-" * 70)

    for page_num in range(page_count):
        for op in ("get_images", "get_drawings", "get_pixmap"):
            result = run_op(path, page_num, op)
            status = "OK" if result["ok"] else "CRASH"
            print(f"page={page_num + 1:>3}  op={op:<12}  {status}  {result}")
            if not result["ok"]:
                print("=" * 70)
                print(f"FOUND IT: page {page_num + 1}, operation '{op}' crashes the process.")
                print(f"Signal/returncode: {result.get('returncode')}")
                print("=" * 70)
                return


if __name__ == "__main__":
    main()
