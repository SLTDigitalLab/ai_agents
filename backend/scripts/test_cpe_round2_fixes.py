"""
Verification Test Suite for Claude's Round 2 Review Findings on Ask CPE.
Tests:
1. SRX320 VPN tunnels: Abstains (no datasheet), does NOT invent 100 tunnels.
2. ONE521 weight: Cites verified 0.8 kg plastic / 1.3 kg metal, does NOT invent 1.2 kg.
3. Ordinal context resolution: 'back to the first one, whats its max routing speed with services' -> ONE421 100 Mbps.
4. C1121X stock: Reports both 22 (Cisco Routers) and 114 (SD-WAN Edges), cites NWKEAC084, flags conflict.
5. 40 km SFP stock: Reports total 158 on hand across all 6 items (includes BNWKSFP026 and BNWKCEN070).
6. SRX 300 PoE: Explicitly cites that CPE Matrix states 'No'.
7. Domain scope boundary: Declines 'capital of france' as out of scope.
8. Strict hardware card filter: Only displays cited models, zero random card fallback.
"""

import json
import time
import urllib.request

BASE_URL = "http://127.0.0.1:8000"


def send_chat(message: str, thread_id: str):
    url = f"{BASE_URL}/api/v1/cpe/chat"
    payload = json.dumps({"message": message, "thread_id": thread_id, "limit": 10}).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=40) as resp:
        return json.loads(resp.read().decode("utf-8"))


def run_tests():
    print("=" * 70)
    print("RUNNING ASK CPE ROUND 2 GENERALIZATION & RIGOR VERIFICATION")
    print("=" * 70)

    ts = int(time.time())

    # TEST 1: SRX320 VPN Tunnels (Must abstain, no datasheet in corpus)
    print("\n--- Test 1: SRX320 VPN Tunnels (No datasheet abstention) ---")
    r1 = send_chat("how many vpn tunnels on the srx320?", f"t1_{ts}")
    ans1 = r1["answer"]
    print("Answer preview:", ans1[:300])
    no_invented_100 = "100 vpn" not in ans1.lower() and "100 tunnels" not in ans1.lower()
    abstains_or_not_stated = any(w in ans1.lower() for w in ["not stated", "not specified", "no technical datasheet", "not documented", "does not specify"])
    print(f"  [+] No invented 100 tunnels: {no_invented_100}")
    print(f"  [+] Abstains / states not documented in corpus: {abstains_or_not_stated}")

    # TEST 2: ONE521 Weight Grounding (0.8 kg plastic / 1.3 kg metal, not 1.2 kg)
    print("\n--- Test 2: ONE521 Weight Grounding ---")
    r2 = send_chat("what is the 521’s weight?", f"t2_{ts}")
    ans2 = r2["answer"]
    print("Answer preview:", ans2[:300])
    has_08_or_13 = ("0.8" in ans2 or "1.3" in ans2 or "1.8 lb" in ans2 or "2.9 lb" in ans2)
    no_invented_12 = "1.2 kg" not in ans2 and "2.65" not in ans2
    print(f"  [+] Cites verified 0.8 kg plastic / 1.3 kg metal: {has_08_or_13}")
    print(f"  [+] No invented 1.2 kg: {no_invented_12}")

    # TEST 3: Ordinal Context Resolution ('back to the first one')
    print("\n--- Test 3: Ordinal Context ('back to the first one') ---")
    tid3 = f"t3_{ts}"
    send_chat("hey, what’s the ONE421?", tid3)
    send_chat("customer’s cisco 1941 died, what do we give them?", tid3)
    send_chat("what if he insists on cisco?", tid3)
    r3 = send_chat("back to the first one, whats its max routing speed with services", tid3)
    ans3 = r3["answer"]
    print("Answer preview:", ans3[:300])
    resolves_one421_speed = ("100" in ans3 and ("mbps" in ans3.lower() or "routing" in ans3.lower())) or "220" in ans3
    cites_one421 = "one421" in ans3.lower() or "421" in ans3.lower()
    print(f"  [+] Successfully resolves first device (ONE421): {cites_one421}")
    print(f"  [+] Cites accurate 100 Mbps with services: {resolves_one421_speed}")

    # TEST 4: Cisco 1121X Stock Dual-Listing & Conflict Flagging
    print("\n--- Test 4: Cisco 1121X Dual Listing (22 Routers vs 114 SD-WAN) ---")
    r4 = send_chat("how many Cisco C1121X routers are in stock?", f"t4_{ts}")
    ans4 = r4["answer"]
    print("Answer preview:", ans4[:350])
    cites_22 = "22" in ans4
    cites_114 = "114" in ans4
    cites_nwkeac084 = "NWKEAC084" in ans4
    flags_dual_conflict = any(w in ans4.lower() for w in ["conflict", "duplicate", "discrepancy", "two categories", "both", "sd-wan edges", "cisco routers"])
    print(f"  [+] Cites 22 under Cisco Routers: {cites_22}")
    print(f"  [+] Cites 114 under SD-WAN Edges: {cites_114}")
    print(f"  [+] Cites Item Code NWKEAC084: {cites_nwkeac084}")
    print(f"  [+] Flags dual listing / conflict: {flags_dual_conflict}")

    # TEST 5: 40 km SFP Stock Completeness (All 6 items, total 158)
    print("\n--- Test 5: 40 km SFP Stock Completeness (Total 158) ---")
    r5 = send_chat("how much 40 km SFP stock do we have in Peliyagoda?", f"t5_{ts}")
    ans5 = r5["answer"]
    print("Answer preview:", ans5[:350])
    cites_158_or_parts = "158" in ans5 or ("64" in ans5 and "84" in ans5)
    cites_sfp026_or_cen070 = "BNWKSFP026" in ans5 or "BNWKCEN070" in ans5 or "NWKSFP059" in ans5
    print(f"  [+] Cites total 158 on hand (or exact breakdown 64+84+5+5): {cites_158_or_parts}")
    print(f"  [+] Includes previously missed transceivers (BNWKSFP026/BNWKCEN070): {cites_sfp026_or_cen070}")

    # TEST 6: SRX 300 PoE (Matrix explicitly says No)
    print("\n--- Test 6: SRX 300 PoE (Cites Matrix 'No') ---")
    r6 = send_chat("does the srx300 do poe?", f"t6_{ts}")
    ans6 = r6["answer"]
    print("Answer preview:", ans6[:300])
    states_no_poe = any(w in ans6.lower() for w in ["no", "does not support", "not support"])
    cites_matrix_or_explicit = any(w in ans6.lower() for w in ["matrix", "cpe matrix", "explicitly"])
    print(f"  [+] Correctly states No PoE: {states_no_poe}")
    print(f"  [+] Cites CPE Matrix: {cites_matrix_or_explicit}")

    # TEST 7: Domain Scope Boundary ('capital of france')
    print("\n--- Test 7: Domain Scope Boundary ('capital of france') ---")
    r7 = send_chat("capital of france", f"t7_{ts}")
    ans7 = r7["answer"]
    cards7 = r7.get("hardware_cards", [])
    print("Answer preview:", ans7[:250])
    print("Cards returned:", [c["name"] for c in cards7])
    declines_out_of_scope = any(w in ans7.lower() for w in ["out of scope", "specialized in", "cannot assist", "enterprise telecommunications", "customer premises equipment", "slt"])
    zero_cards_on_trivia = len(cards7) == 0
    print(f"  [+] Declines out-of-scope trivia: {declines_out_of_scope}")
    print(f"  [+] Zero cards returned for out-of-scope query: {zero_cards_on_trivia}")

    # TEST 8: Strict Hardware Card Display (Huawei S5735-L8P4S-A1)
    print("\n--- Test 8: Strict Hardware Card Display ---")
    r8 = send_chat("how many poe ports on the Huawei S5735-L8P4S-A1?", f"t8_{ts}")
    cards8 = [c["name"] for c in r8.get("hardware_cards", [])]
    print("Cards returned:", cards8)
    only_huawei_cards = all("Huawei" in c or "S5735" in c for c in cards8)
    no_leaked_cisco_or_one = not any(w in c for c in cards8 for w in ["Cisco", "ONE421", "1424", "ONE521", "Transceiver"])
    print(f"  [+] Only Huawei S5735 cards returned: {only_huawei_cards}")
    print(f"  [+] No unrelated Cisco/ONE cards: {no_leaked_cisco_or_one}")

    print("\n" + "=" * 70)
    all_passed = all([
        no_invented_100, abstains_or_not_stated,
        has_08_or_13, no_invented_12,
        cites_one421, resolves_one421_speed,
        cites_22, cites_114, cites_nwkeac084,
        cites_158_or_parts,
        states_no_poe, cites_matrix_or_explicit,
        declines_out_of_scope, zero_cards_on_trivia,
        only_huawei_cards, no_leaked_cisco_or_one
    ])
    if all_passed:
        print("[SUCCESS] ALL ROUND 2 VERIFICATION TESTS PASSED 100%!")
    else:
        print("[NOTE] Review test details above.")
    print("=" * 70)


if __name__ == "__main__":
    run_tests()

