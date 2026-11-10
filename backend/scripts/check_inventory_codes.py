"""Checks every inventory row against the live Ask CPE bot.
For each row it asks (1) "item code for <description>" and (2) "stock of <description>"
in a fresh session, then compares the answer to the spreadsheet.
Prints only mismatches and writes inventory_check_report.csv.

Usage:
  python backend/scripts/check_inventory_codes.py --dry-run                 # just parse the sheet, no API calls
  python backend/scripts/check_inventory_codes.py --probe "stock of ONE421"  # show the raw API response
  python backend/scripts/check_inventory_codes.py                            # full run
  python backend/scripts/check_inventory_codes.py --limit 10                 # try the first 10 rows
"""
import argparse, csv, os, re, sys, time, uuid
import openpyxl

# ---------- EDIT THESE IF YOUR API IS DIFFERENT ----------
XLSX_PATHS = [
    "backend/data/cpe/CPE LIST.xlsx",
    "/app/data/cpe/CPE LIST.xlsx",
    "CPE LIST.xlsx",
    "../data/cpe/CPE LIST.xlsx"
]
URL = "http://localhost:8000/api/v1/cpe/chat"

def get_xlsx_path():
    for p in XLSX_PATHS:
        if os.path.exists(p):
            return p
    return "CPE LIST.xlsx"

def build_payload(question, session_id):
    return {"message": question, "thread_id": session_id}

ANSWER_KEYS = ["answer", "response", "reply", "message", "content", "text"]
# ---------------------------------------------------------

CODE_RE = re.compile(r"\b(?:B?CPE[A-Z0-9]{3,4}\d{3}[A-Z]?|B?NWK[A-Z0-9]{3,4}\d{3}[A-Z]?|CPEDTA\d{3})\b")
FULL_CODE = re.compile(r"^(?:B?CPE[A-Z0-9]{3,4}\d{3}[A-Z]?|B?NWK[A-Z0-9]{3,4}\d{3}[A-Z]?|CPEDTA\d{3})$")

def num(x):
    try: return int(float(str(x).replace(",", "").strip()))
    except Exception: return None

def load_rows(path):
    ws = openpyxl.load_workbook(path, data_only=True).active
    rows, skipped = [], []
    for i in range(3, ws.max_row + 1):
        v = [(str(c.value).strip() if c.value is not None else None) for c in ws[i]]
        v += [None] * (16 - len(v))
        codes_in_row = [x for x in v if x and FULL_CODE.match(x)]
        if not codes_in_row:
            continue
        code, desc = v[5], v[6]
        if not (code and FULL_CODE.match(code)) or not desc:
            skipped.append((i, codes_in_row[0], "columns shifted, check manually"))
            continue
        on, rep, reg = num(v[8]), num(v[9]), num(v[10])
        rows.append(dict(row=i, code=code, desc=" ".join(desc.split()), on=on, rep=rep, reg=reg))
    return rows, skipped

def get_answer(resp_json):
    if isinstance(resp_json, dict):
        for k in ANSWER_KEYS:
            if isinstance(resp_json.get(k), str):
                return resp_json[k]
        for v in resp_json.values():
            if isinstance(v, dict):
                a = get_answer(v)
                if a: return a
    return str(resp_json)

def ask(question):
    import requests
    r = requests.post(URL, json=build_payload(question, f"inv_chk_{uuid.uuid4().hex[:8]}"), timeout=90)
    r.raise_for_status()
    return get_answer(r.json())

def numbers_in(text):
    return {int(n.replace(",", "")) for n in re.findall(r"(?<![\w.])\d[\d,]*(?![\w.])", text) if n.replace(",", "").isdigit()}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--probe")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--sleep", type=float, default=0.2)
    a = ap.parse_args()

    if a.probe:
        import requests
        r = requests.post(URL, json=build_payload(a.probe, f"inv_chk_{uuid.uuid4().hex[:8]}"), timeout=90)
        print("Status Code:", r.status_code)
        print("Response Answer:\n", get_answer(r.json())[:2000])
        return

    xlsx_path = get_xlsx_path()
    rows, skipped = load_rows(xlsx_path)
    by_code = {}
    for r in rows: by_code.setdefault(r["code"], []).append(r)
    print(f"parsed {len(rows)} rows, {len(by_code)} distinct codes, {len(skipped)} skipped")
    for s in skipped: print("  skipped row", s)
    dups = {c: rs for c, rs in by_code.items() if len(rs) > 1}
    for c, rs in dups.items(): print(f"  duplicate code {c}: on-hand values {[r['on'] for r in rs]} (any accepted)")
    if a.dry_run: return

    todo = rows[: a.limit] if a.limit else rows
    seen, bad, total = set(), [], 0
    report_file = a.output if hasattr(a, "output") and a.output else "/tmp/inventory_check_report.csv"
    out = open(report_file, "w", newline="", encoding="utf-8-sig")
    w = csv.writer(out); w.writerow(["row", "code", "desc", "check", "problem", "answer"])
    for r in todo:
        if r["code"] in seen and len(by_code[r["code"]]) > 1: continue
        seen.add(r["code"])
        siblings = by_code[r["code"]]
        # 1. code question
        q1 = f"item code for {r['desc'][:90]}"
        # 2. stock question
        q2 = f"stock of {r['desc'][:90]}"
        for check, q in (("code", q1), ("stock", q2)):
            total += 1
            try: ans = ask(q)
            except Exception as e:
                problem, ans = f"request failed: {e}", ""
                bad.append(r); w.writerow([r["row"], r["code"], r["desc"][:60], check, problem, ans]); print("ERR", r["code"], e); continue
            problems = []
            if r["code"] not in ans: problems.append(f"expected code {r['code']} missing")
            wrong = [c for c in set(CODE_RE.findall(ans)) if c not in {s['code'] for s in siblings}]
            if wrong and check == "code": problems.append(f"other codes in answer: {sorted(wrong)}")
            if check == "stock":
                got = numbers_in(ans)
                ok = any(all(x is None or x in got for x in (s["on"], s["rep"], s["reg"])) for s in siblings)
                if not ok: problems.append(f"numbers don't match sheet {[(s['on'], s['rep'], s['reg']) for s in siblings]}")
                if wrong: problems.append(f"other codes in answer: {sorted(wrong)}")
            if problems:
                bad.append(r); print(f"MISMATCH row {r['row']} {r['code']} [{check}]: {'; '.join(problems)}")
                w.writerow([r["row"], r["code"], r["desc"][:60], check, "; ".join(problems), ans.replace("\n", " ")[:400]])
            time.sleep(a.sleep)
    out.close()
    print(f"\nchecked {total} answers, {len(bad)} with problems. report: {report_file}")

if __name__ == "__main__":
    main()

