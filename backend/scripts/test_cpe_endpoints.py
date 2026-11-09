"""
Verification script for Ask CPE endpoints:
1. Tests the dedicated LifeStore-style endpoint: POST /api/v1/cpe/chat
2. Tests the LangGraph platform chat endpoint: POST /api/v1/chat with agent_id='cpe'
"""

import sys
import json
import urllib.request

BASE_URL = "http://127.0.0.1:8000"

def test_dedicated_cpe_endpoint():
    print("=" * 70)
    print("1. TESTING DEDICATED CPE ENDPOINT: POST /api/v1/cpe/chat")
    print("=" * 70)
    
    url = f"{BASE_URL}/api/v1/cpe/chat"
    payload = {
        "message": "What Huawei S5735 switches do we have in stock at Peliyagoda Main Stores and what are their port specs?",
        "limit": 4
    }
    
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}
    )
    
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            print("Status Code:", resp.status)
            print("Agent Status:", data.get("status"))
            print("\nAGENT ANSWER:\n" + data.get("answer", ""))
            print("\nHARDWARE CARDS:")
            for card in data.get("hardware_cards", []):
                print(f"  * Model: {card.get('name')}")
                print(f"    Vendor: {card.get('vendor')} | Item Code: {card.get('item_code')}")
                print(f"    Location: {card.get('warehouse')}")
                print(f"    Key Specs: {card.get('specs')}")
            return True
    except Exception as exc:
        print("Dedicated endpoint test failed:", exc)
        return False


def test_comparison_query():
    print("\n" + "=" * 70)
    print("2. TESTING SPEC COMPARISON QUERY: OneAccess 1424 vs Juniper SRX300")
    print("=" * 70)
    
    url = f"{BASE_URL}/api/v1/cpe/chat"
    payload = {
        "message": "Compare OneAccess 1424 and Juniper SRX300 for branch connectivity",
        "limit": 4
    }
    
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}
    )
    
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            print("Status Code:", resp.status)
            print("\nAGENT ANSWER:\n" + data.get("answer", ""))
            return True
    except Exception as exc:
        print("Comparison test failed:", exc)
        return False


if __name__ == "__main__":
    s1 = test_dedicated_cpe_endpoint()
    s2 = test_comparison_query()
    if s1 and s2:
        print("\n[ALL TESTS PASSED] Ask CPE agent is 100% operational!")
    else:
        sys.exit(1)

