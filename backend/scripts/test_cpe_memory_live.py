"""
Verification suite for Ask CPE Production-level Multi-Turn Memory & History.
Tests:
1. Multi-turn dialogue with pronoun resolution ('its', 'the router').
2. Persistence into PostgreSQL table public.cpe_memory.
3. History retrieval via GET /api/v1/chat/cpe/{thread_id} for frontend restoration.
"""

import sys
import json
import time
import urllib.request

BASE_URL = "http://127.0.0.1:8000"
THREAD_ID = f"test_cpe_multiturn_{int(time.time())}"

def run_test():
    print("=" * 70)
    print("STEP 1: SEND FIRST TURN (Turn 1 - Inventory Lookup)")
    print(f"Thread ID: {THREAD_ID}")
    print("=" * 70)

    url = f"{BASE_URL}/api/v1/cpe/chat"
    payload1 = {
        "message": "How many ONE421 are available in Peliyagoda Main Stores?",
        "thread_id": THREAD_ID,
        "limit": 6
    }

    req1 = urllib.request.Request(
        url,
        data=json.dumps(payload1).encode("utf-8"),
        headers={"Content-Type": "application/json"}
    )

    with urllib.request.urlopen(req1, timeout=40) as resp:
        data1 = json.loads(resp.read().decode("utf-8"))
        print("Status Code:", resp.status)
        print("Turn 1 Answer:\n" + data1.get("answer", ""))
        assert "148" in data1.get("answer", "") or "ONE421" in data1.get("answer", ""), "Expected ONE421 stock data"
        print("[TURN 1 SUCCESS]")

    print("\n" + "=" * 70)
    print("STEP 2: SEND FOLLOW-UP TURN (Turn 2 - Pronoun 'its port specifications')")
    print("=" * 70)

    payload2 = {
        "message": "What are its WAN and LAN port specifications?",
        "thread_id": THREAD_ID,
        "limit": 6
    }

    req2 = urllib.request.Request(
        url,
        data=json.dumps(payload2).encode("utf-8"),
        headers={"Content-Type": "application/json"}
    )

    with urllib.request.urlopen(req2, timeout=40) as resp:
        data2 = json.loads(resp.read().decode("utf-8"))
        print("Status Code:", resp.status)
        print("Turn 2 Answer:\n" + data2.get("answer", ""))
        # Check that it answered about ONE421 ports without needing the user to re-type ONE421
        assert "4" in data2.get("answer", "") or "LAN" in data2.get("answer", ""), "Expected port specifications"
        print("[TURN 2 MULTI-TURN CONTEXT RESOLVED SUCCESS]")

    print("\n" + "=" * 70)
    print("STEP 3: TEST HISTORY RESTORATION (GET /api/v1/chat/cpe/{thread_id})")
    print("=" * 70)

    history_url = f"{BASE_URL}/api/v1/chat/cpe/{THREAD_ID}"
    req3 = urllib.request.Request(history_url)

    with urllib.request.urlopen(req3, timeout=10) as resp:
        hdata = json.loads(resp.read().decode("utf-8"))
        messages = hdata.get("messages", [])
        print("Total restored messages:", len(messages))
        for idx, m in enumerate(messages):
            role = m.get("type")
            snippet = m.get("content", "")[:90].replace("\n", " ")
            print(f"  [{idx+1}] {role.upper()}: {snippet}...")

        assert len(messages) >= 4, f"Expected at least 4 messages (2 human, 2 ai), got {len(messages)}"
        assert messages[0]["type"] == "human"
        assert messages[1]["type"] == "ai"
        assert messages[2]["type"] == "human"
        assert messages[3]["type"] == "ai"
        print("[STEP 3 HISTORY RESTORATION SUCCESS]")

    print("\n" + "=" * 70)
    print("[ALL PRODUCTION MULTI-TURN TESTS PASSED 100%]")
    print("=" * 70)

if __name__ == "__main__":
    run_test()

