"""
Verifies the repair-before-render fix added to process_pdf_visuals() actually
works against the real file that used to crash the backend, before re-running
the full SCM force ingestion. Run inside a subprocess so if the fix somehow
doesn't work, only this throwaway process dies, not the caller.

Usage inside the backend container:
    python3 debug_verify_fix.py "/path/to/Procurement Manual Part-I-pages-3.pdf"
"""
import subprocess
import sys

WORKER = r'''
import sys
sys.path.insert(0, "/app")
from services.visual_extractor import process_pdf_visuals

path = sys.argv[1]
records, docs = process_pdf_visuals(path, "debug-verify-doc")
print(f"RESULT: {len(records)} visual records, {len(docs)} vector documents")
'''


def main():
    path = sys.argv[1]
    proc = subprocess.run(
        [sys.executable, "-c", WORKER, path],
        capture_output=True,
        text=True,
        timeout=600,
    )
    print("STDOUT:")
    print(proc.stdout)
    print("STDERR (tail):")
    print(proc.stderr[-2000:])
    print(f"Return code: {proc.returncode}")
    if proc.returncode == 0:
        print("\nSUCCESS — the real pipeline function processed the whole file without crashing.")
    else:
        print("\nSTILL CRASHES — the fix did not resolve it.")


if __name__ == "__main__":
    main()
