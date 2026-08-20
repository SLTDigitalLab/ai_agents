import requests

urls = ["http://localhost:6333", "http://localhost:6335", "http://127.0.0.1:6333", "http://127.0.0.1:6335"]
for url in urls:
    try:
        res = requests.get(f"{url}/collections", timeout=2.0)
        print(f"Qdrant at {url}: status {res.status_code}, collections: {res.json()}")
    except Exception as e:
        print(f"Qdrant at {url} failed: {e}")
