"""
Ask CPE Round 3 Automated Evaluation Runner.
Evaluates all 33 test items from cpe_eval_round3.json against the live Ask CPE endpoint:
http://localhost:8000/api/v1/cpe/chat

Validates:
- must_include strings (case-insensitive)
- must_not_include strings (case-insensitive)
- expected_cards in response
- must_not_show_cards in response
- multi-turn conversation coherence and context persistence
"""

import json
import logging
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple
import urllib.request
import urllib.error

API_URL = "http://localhost:8000/api/v1/cpe/chat"
DATA_FILE = Path(__file__).resolve().parent.parent / "data" / "cpe_eval_round3.json"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("cpe_eval")


def send_chat_message(message: str, thread_id: str) -> Dict[str, Any]:
    payload = json.dumps({"message": message, "thread_id": thread_id, "limit": 10}).encode("utf-8")
    req = urllib.request.Request(
        API_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=45) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as exc:
            logger.warning("Attempt %d failed: %s. Retrying...", attempt + 1, exc)
            time.sleep(2)
    raise RuntimeError(f"Failed to query {API_URL} after 3 attempts")


def check_turn(
    turn_name: str,
    resp: Dict[str, Any],
    must_inc: List[str],
    must_not_inc: List[str],
    exp_cards: List[str] = None,
    not_exp_cards: List[str] = None,
) -> Tuple[bool, List[str]]:
    answer = resp.get("answer", "")
    ans_lower = answer.lower()
    cards = [c.get("name", "") for c in resp.get("hardware_cards", [])]
    cards_str = " | ".join(cards).lower()

    failures = []

    # Check must_include
    for s in must_inc:
        if s.lower() not in ans_lower:
            failures.append(f"Missing required string: {s!r}")

    # Check must_not_include
    for s in must_not_inc:
        if s.lower() in ans_lower:
            failures.append(f"Forbidden string appeared: {s!r}")

    # Check expected_cards
    if exp_cards:
        for ec in exp_cards:
            ec_clean = re.sub(r"[^a-z0-9]", "", ec.lower())
            found = any(ec.lower() in c.lower() or ec_clean in re.sub(r"[^a-z0-9]", "", c.lower()) for c in cards)
            if not found:
                failures.append(f"Expected card missing: {ec!r} (Got: {cards})")

    # Check must_not_show_cards
    if not_exp_cards:
        for nc in not_exp_cards:
            nc_clean = re.sub(r"[^a-z0-9]", "", nc.lower())
            found = any(nc.lower() in c.lower() or nc_clean in re.sub(r"[^a-z0-9]", "", c.lower()) for c in cards)
            if found:
                failures.append(f"Forbidden card shown: {nc!r} (Got: {cards})")

    passed = len(failures) == 0
    return passed, failures


def run_evaluation():
    if not DATA_FILE.exists():
        logger.error("Dataset not found at %s", DATA_FILE)
        sys.exit(1)

    with open(DATA_FILE, "r", encoding="utf-8") as f:
        dataset = json.load(f)

    items = dataset.get("items", [])
    logger.info("Starting evaluation of %d items from %s", len(items), dataset.get("dataset"))

    results = []
    ts = int(time.time())

    for idx, item in enumerate(items, 1):
        item_id = item["id"]
        group = item["group"]
        expected_behavior = item.get("expected_behavior", "answer")
        thread_id = f"eval_r3_{item_id.lower()}_{ts}"

        logger.info("[%d/%d] Evaluating %s (%s, %s)...", idx, len(items), item_id, group, item.get("difficulty"))

        if expected_behavior == "multi_turn":
            turns = item.get("turns", [])
            turn_results = []
            item_passed = True
            all_failures = []

            for t_idx, turn in enumerate(turns, 1):
                q = turn["question"]
                m_inc = turn.get("must_include", [])
                m_not = turn.get("must_not_include", [])
                resp = send_chat_message(q, thread_id)
                t_pass, t_fails = check_turn(f"Turn {t_idx}", resp, m_inc, m_not)
                if not t_pass:
                    item_passed = False
                    for f in t_fails:
                        all_failures.append(f"Turn {t_idx} ('{q}'): {f}")
                turn_results.append({
                    "turn": t_idx,
                    "question": q,
                    "answer_preview": resp.get("answer", "")[:160] + "...",
                    "passed": t_pass,
                    "failures": t_fails,
                })
                time.sleep(0.5)

            results.append({
                "id": item_id,
                "group": group,
                "passed": item_passed,
                "failures": all_failures,
                "turns": turn_results,
            })
            status_str = "PASS" if item_passed else "FAIL"
            logger.info("   -> %s: %s %s", item_id, status_str, f"({all_failures})" if all_failures else "")

        else:
            q = item["question"]
            m_inc = item.get("must_include", [])
            m_not = item.get("must_not_include", [])
            exp_cards = item.get("expected_cards", [])
            not_exp_cards = item.get("must_not_show_cards", [])

            resp = send_chat_message(q, thread_id)
            passed, fails = check_turn(item_id, resp, m_inc, m_not, exp_cards, not_exp_cards)

            results.append({
                "id": item_id,
                "group": group,
                "passed": passed,
                "failures": fails,
                "question": q,
                "answer_preview": resp.get("answer", "")[:180] + "...",
                "cards": [c.get("name") for c in resp.get("hardware_cards", [])],
            })
            status_str = "PASS" if passed else "FAIL"
            logger.info("   -> %s: %s %s", item_id, status_str, f"({fails})" if fails else "")

        time.sleep(0.5)

    # Summary
    passed_count = sum(1 for r in results if r["passed"])
    total_count = len(results)
    pass_pct = (passed_count / total_count) * 100

    print("\n" + "=" * 78)
    print(f"EVALUATION SUMMARY: {passed_count}/{total_count} PASSED ({pass_pct:.1f}%)")
    print("=" * 78)

    by_group = {}
    for r in results:
        g = r["group"]
        by_group.setdefault(g, []).append(r)

    for g, g_res in by_group.items():
        g_pass = sum(1 for r in g_res if r["passed"])
        print(f"  [{g.upper()}] {g_pass}/{len(g_res)} passed")
        for r in g_res:
            mark = "✅" if r["passed"] else "❌"
            if not r["passed"]:
                print(f"     {mark} {r['id']}: {r['failures']}")
            else:
                print(f"     {mark} {r['id']}")

    print("=" * 78)

    # Save output report
    report_file = DATA_FILE.parent / "cpe_eval_round3_report.json"
    with open(report_file, "w", encoding="utf-8") as f:
        json.dump({
            "timestamp": ts,
            "passed": passed_count,
            "total": total_count,
            "pass_rate": f"{pass_pct:.1f}%",
            "results": results
        }, f, indent=2)
    print(f"Detailed results saved to {report_file}")


if __name__ == "__main__":
    run_evaluation()

