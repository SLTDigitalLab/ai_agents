"""
Ingest Curated Knowledge Base for Ask CPE.
Populates the Qdrant 'cpe_docs' collection with all verified hardware profiles,
inventory items, multi-hop replacement policies, and flowchart access paths.
"""

import sys
import time
from pathlib import Path
from collections import defaultdict

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from langchain_core.documents import Document
from scripts.ingest_cpe_knowledgebase import (
    InvRow,
    build_inventory_docs,
    load_curated_records,
    load_cross_linked_replacements,
    SLIDE8_PATHS,
    COLLECTION_NAME,
)

INVENTORY_ROWS = [
    InvRow(section="Data CPE", ntu_cpe="CPE", category="Routers", oss="ONE421", vendor="OneAccess", code="CPEDAT081S", desc="CPE ONEACCESS ONE421", diff="", on_hand=148, repaired=0, regions=0, project="", port="4xGE", inv_status="Active", proc_status="Listed for sales"),
    InvRow(section="Data CPE", ntu_cpe="CPE", category="Routers", oss="ONE521", vendor="OneAccess", code="CPEDAT082S", desc="CPE ONEACCESS ONE521", diff="", on_hand=25, repaired=0, regions=0, project="", port="4xGE", inv_status="Active", proc_status="Listed for sales"),
    InvRow(section="Data CPE", ntu_cpe="CPE", category="Routers", oss="ONE2501", vendor="OneAccess", code="CPEDAT083S", desc="CPE ONEACCESS ONE2501", diff="", on_hand=4, repaired=0, regions=0, project="", port="4/8xGE", inv_status="Active", proc_status="Listed for sales"),
    InvRow(section="Data CPE", ntu_cpe="CPE", category="Routers", oss="C1121X-8P", vendor="Cisco", code="NWKEAC084", desc="CISCO C1121X-8P ROUTER", diff="", on_hand=22, repaired=0, regions=0, project="", port="8xGE", inv_status="Active", proc_status="", duplicate=True),
    InvRow(section="SD WAN Edges", ntu_cpe="CPE", category="Routers", oss="C1121X-8P", vendor="Cisco", code="NWKEAC084", desc="CISCO C1121X-8P ROUTER", diff="", on_hand=114, repaired=0, regions=0, project="", port="8xGE", inv_status="Active", proc_status="", duplicate=True),
    InvRow(section="Data CPE", ntu_cpe="CPE", category="Routers", oss="C1121X", vendor="Cisco", code="NWKEAC085", desc="CISCO C1121X-4P ROUTER", diff="", on_hand=15, repaired=0, regions=0, project="", port="4xGE", inv_status="Active", proc_status=""),
    InvRow(section="Data CPE", ntu_cpe="CPE", category="Routers", oss="C1111", vendor="Cisco", code="NWKEAC078", desc="CISCO C1111-8P ROUTER", diff="", on_hand=10, repaired=0, regions=0, project="", port="8xGE", inv_status="Active", proc_status=""),
    InvRow(section="Data CPE", ntu_cpe="CPE", category="Security", oss="SRX300", vendor="Juniper", code="NWKEAC158S", desc="JUNIPER SRX 300", diff="", on_hand=24, repaired=0, regions=51, project="", port="", inv_status="Active", proc_status="Need to Re-order"),
    InvRow(section="Data CPE", ntu_cpe="CPE", category="Security", oss="SRX320", vendor="Juniper", code="NWKEAC022", desc="JUNIPER SRX 320", diff="", on_hand=0, repaired=2, regions=3, project="", port="", inv_status="Active", proc_status=""),
    InvRow(section="Data CPE", ntu_cpe="CPE", category="Security", oss="SRX320 POE", vendor="Juniper", code="NWKEAC126", desc="SRX 320 POE LAN PORTS", diff="", on_hand=19, repaired=0, regions=0, project="", port="", inv_status="Active", proc_status=""),
    InvRow(section="Data CPE", ntu_cpe="CPE", category="Security", oss="SRX320 SERIAL", vendor="Juniper", code="NWKEAC022S", desc="JUNIPER SRX 320 SERIAL ENABLED", diff="", on_hand=1, repaired=0, regions=26, project="", port="", inv_status="Active", proc_status=""),
    InvRow(section="Data CPE", ntu_cpe="CPE", category="Security", oss="SRX340", vendor="Juniper", code="NWKEAC025", desc="JUNIPER SRX 340", diff="", on_hand=5, repaired=0, regions=1, project="", port="", inv_status="Active", proc_status=""),
    InvRow(section="Data CPE", ntu_cpe="CPE", category="Security", oss="FG-100F", vendor="Fortinet", code="NWKSRG264", desc="FORTIGATE-100F", diff="", on_hand=0, repaired=0, regions=0, project="", port="", inv_status="Listed for Sales", proc_status=""),
    InvRow(section="Data CPE", ntu_cpe="CPE", category="Switches", oss="S5735-L8T4X", vendor="Huawei", code="NWKEAC118", desc="HUAWEI S5735-L8T4X-A1", diff="", on_hand=0, repaired=0, regions=23, project="", port="8xGE, 4x10G SFP+", inv_status="Active", proc_status=""),
    InvRow(section="Data CPE", ntu_cpe="CPE", category="Switches", oss="S5735-L8P4S", vendor="Huawei", code="NWKSRG232S", desc="HUAWEI S5735-L8P4S-A1", diff="", on_hand=0, repaired=0, regions=29, project="", port="8xGE PoE+, 4xGE SFP", inv_status="Active", proc_status=""),
    InvRow(section="Data CPE", ntu_cpe="CPE", category="Switches", oss="S5735-L24P4S", vendor="Huawei", code="NWKSRG233S", desc="HUAWEI S5735-L24P4S-A1", diff="", on_hand=0, repaired=0, regions=5, project="", port="24xGE PoE+, 4xGE SFP", inv_status="Active", proc_status=""),
    InvRow(section="Data CPE", ntu_cpe="CPE", category="Switches", oss="S310-24T4S", vendor="Huawei", code="NWKSRG294S", desc="HUAWEI S310-24T4S", diff="", on_hand=500, repaired=0, regions=0, project="", port="24xGE, 4xGE SFP", inv_status="Active", proc_status="LC Opening inprogress"),
    InvRow(section="Data CPE", ntu_cpe="CPE", category="Switches", oss="S5331-H24P4XC", vendor="Huawei", code="NWKEAC035", desc="HUAWEI S5331-H24P4XC", diff="", on_hand=7, repaired=0, regions=0, project="", port="24xGE PoE+", inv_status="Active", proc_status=""),
    InvRow(section="Data CPE", ntu_cpe="CPE", category="Switches", oss="S5331-H24T4XC", vendor="Huawei", code="NWKEAC033", desc="HUAWEI S5331-H24T4XC", diff="", on_hand=0, repaired=0, regions=6, project="", port="24xGE", inv_status="Retired", proc_status=""),
    InvRow(section="Data CPE", ntu_cpe="CPE", category="Switches", oss="S5332-H48S6Q", vendor="Huawei", code="NWKEAC047", desc="HUAWEI S5332-H48S6Q", diff="", on_hand=48, repaired=0, regions=8, project="", port="48xGE SFP", inv_status="Active", proc_status=""),
    InvRow(section="Enterprise ONTs", ntu_cpe="NTU", category="ONT", oss="ZXHN F670L", vendor="ZTE", code="CPEFTH076S", desc="ZTE ZXHN F670L ONT", diff="", on_hand=1000, repaired=0, regions=0, project="", port="GPON", inv_status="Active", proc_status="PO issued, ETA 30 Nov 2026"),
    InvRow(section="Enterprise ONTs", ntu_cpe="NTU", category="ONT", oss="EG8145V5", vendor="Huawei", code="CPEFTH077S", desc="HUAWEI EG8145V5 ONT", diff="", on_hand=1000, repaired=0, regions=0, project="", port="GPON", inv_status="Active", proc_status="pending site approval from TRCSL, ETA 30 Nov 2026"),
    InvRow(section="Data CPE", ntu_cpe="CPE", category="Switches", oss="C1300", vendor="Cisco", code="CPEDAT124S", desc="CISCO CATALYST 1300 SWITCH", diff="", on_hand=500, repaired=0, regions=0, project="", port="24xGE", inv_status="Active", proc_status="LC Opening inprogress"),
    InvRow(section="Data CPE", ntu_cpe="NTU", category="Routers", oss="1424", vendor="OneAccess", code="CPEDAT004S", desc="ONEACCESS 1424 SERIAL ENABLED", diff="", on_hand=12, repaired=0, regions=0, project="", port="SHDSL", inv_status="Active", proc_status=""),
    InvRow(section="Data CPE", ntu_cpe="CPE", category="Routers", oss="ISR4321X", vendor="Cisco", code="NWKEAC072", desc="CISCO ISR4321X/K9", diff="", on_hand=8, repaired=0, regions=0, project="", port="2xGE", inv_status="To be Retired with Alternatives [C8200L]", proc_status=""),
]

def build_curated_docs():
    by_code = defaultdict(list)
    for r in INVENTORY_ROWS:
        by_code[r.code].append(r)

    as_of = "19/10/2026"
    all_docs = []

    # 1. Inventory documents
    all_docs += build_inventory_docs(INVENTORY_ROWS, as_of, by_code)

    # 2. Curated golden profiles
    all_docs += load_curated_records(by_code)

    # 3. Cross-linked replacement policies
    all_docs += load_cross_linked_replacements(by_code)

    # 4. Slide 8 flowchart access paths
    from collections import OrderedDict
    path_groups: dict[str, list[tuple[str, str, str, str]]] = OrderedDict()
    for row in SLIDE8_PATHS:
        path = row[0]
        if path not in path_groups:
            path_groups[path] = []
        path_groups[path].append(row)

    all_cpes = {row[1] for row in SLIDE8_PATHS}
    for path, rows in path_groups.items():
        own_cpes = [row[1] for row in rows]
        not_on_path = sorted(all_cpes - set(own_cpes))
        lines = [
            f"[Enterprise CPE Selection Flowchart & Capped Performance - Slide 8: {path}]",
            f"Access Path: {path}",
            "CPEs and performance caps on this access path:",
        ]
        for _, cpe, perf, svc in rows:
            lines.append(f"- CPE: {cpe} | Performance: {perf} | Services delivered: {svc}")
        lines.append(f"Not on this path (do not apply these caps to them): {', '.join(not_on_path)}.")
        all_docs.append(Document(
            page_content="\n".join(lines),
            metadata={"source": "Enterprise-CPE-Plan-2025.pptx", "slide": 8, "path": path, "doc_type": "selection_flowchart"}
        ))

    # 5. Slide 7 replacement policy
    all_docs.append(Document(
        page_content="""[Enterprise CPE Policy & Phased-Out Replacement Rules - Slide 7]
Replacement matrix for faulty / phased-out Cisco CPEs (default OneAccess options):
1. Cisco 891F / Cisco 1921 -> OneAccess ONE421.
2. Cisco 1941 / Cisco ISR 4200 / Cisco ISR 4300 series -> OneAccess ONE521.
3. Cisco 4400 series -> OneAccess ONE2501.
   Commercial terms: for faulty replacement there is no monthly rental increment as per the existing Cisco CPE with the service. For new services the standard tariff of the OneAccess ONE CPE applies.
Brand-specific needs (customer insists on Cisco CPE): Cisco 891F / 1921 / 1941 -> Cisco 1121X; Cisco ISR 4200 / ISR 4300 series -> the slide writes 'Cisco 4231X' (see the ISR 4200 / 4300 conflict record).
   The latest tariff of Cisco CPE applies for both new service and faulty replacement.
Higher performance needs: Juniper SRX320 and SRX340 L3 switches are available; in case of faulty replacement of a Cisco CPE the latest monthly rental of the L3 switch applies.
Trigger: replacement occurs when the CPE is faulty.""",
        metadata={"source": "Enterprise-CPE-Plan-2025.pptx", "slide": 7, "doc_type": "policy_replacement"}
    ))

    return all_docs

def main():
    docs = build_curated_docs()
    print(f"[+] Built {len(docs)} curated CPE knowledge base documents.")

    from qdrant_client import QdrantClient
    from langchain_qdrant import QdrantVectorStore, FastEmbedSparse, RetrievalMode
    from core.config import settings
    from core.llm import get_ingestion_embedding_model

    qdrant_url = getattr(settings, "QDRANT_URL", "http://db_qdrant:6333")
    client = None
    for candidate in [qdrant_url, "http://db_qdrant:6333", "http://localhost:6333"]:
        try:
            c = QdrantClient(url=candidate, timeout=10, check_compatibility=False)
            c.get_collections()
            client = c
            print(f"[+] Connected to Qdrant at {candidate}")
            break
        except Exception as e:
            continue

    if not client:
        print("[-] Failed to connect to Qdrant")
        sys.exit(1)

    embedding_model = get_ingestion_embedding_model()
    sparse_embeddings = FastEmbedSparse(model_name="Qdrant/bm25")

    target = COLLECTION_NAME
    try:
        client.delete_collection(target)
        print(f"[+] Dropped old '{target}' if existed")
    except Exception:
        pass

    start = time.time()
    print(f"[+] Indexing {len(docs)} documents into '{target}'...")
    QdrantVectorStore.from_documents(
        documents=docs,
        embedding=embedding_model,
        sparse_embedding=sparse_embeddings,
        url=candidate,
        collection_name=target,
        retrieval_mode=RetrievalMode.HYBRID,
        check_compatibility=False,
        timeout=120,
    )
    count = client.count(target, exact=True).count
    print(f"[SUCCESS] Successfully indexed {count} documents into Qdrant collection '{target}' in {time.time() - start:.1f}s!")

if __name__ == "__main__":
    main()
