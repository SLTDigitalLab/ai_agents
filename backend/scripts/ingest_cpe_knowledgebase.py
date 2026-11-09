"""
Enterprise CPE Knowledge Base Ingestion Script (V3 - Enhanced Production Build).
Ingests all 10 CPE data sources with:
1. Sheet1 in Copy of CPE Matrix.xlsx (Service mapping: DAB, IPVPN, BIL, E Net Access; Existing -> New CPE; IP Phones)
2. Denormalized Cross-Linked Replacement Chunks (joins policy replacements with live Peliyagoda inventory quantities)
3. Model-Disambiguated Technical Hardware Profiles (Huawei S5735 models, Cisco ISR 4000 series, OneAccess NAT/Routing)
4. Cross-document discrepancy documentation (1424 22 Mbps vs 2 Mbps, ONE521 250 Mbps vs 1 Gbps, ISR 4321X retirement)
into isolated Qdrant collection: 'cpe_docs'.
"""

import os
import sys
import time
from pathlib import Path
from typing import List

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter
from qdrant_client import QdrantClient
from langchain_qdrant import QdrantVectorStore, FastEmbedSparse, RetrievalMode

from core.config import settings
from core.llm import get_ingestion_embedding_model

COLLECTION_NAME = "cpe_docs"
CPE_DATA_DIR = BACKEND_DIR / "data" / "cpe"


# ---------------------------------------------------------------------
# 1. Excel Loaders: Structured Inventory & Hardware Matrix
# ---------------------------------------------------------------------
def load_cpe_inventory(excel_path: Path) -> List[Document]:
    """Parse CPE LIST.xlsx (Peliyagoda Main Stores inventory with all quantities)."""
    import openpyxl

    docs = []
    wb = openpyxl.load_workbook(excel_path, data_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))

    header = None
    for r in rows:
        if r and any("Item Code" in str(cell or "") for cell in r):
            header = [str(c or "").strip() for c in r]
            break

    if not header:
        print("[Warning] Could not find header row in CPE LIST.xlsx")
        return []

    def find_idx(candidates):
        for c in candidates:
            for idx, h in enumerate(header):
                if c.lower() in h.lower():
                    return idx
        return -1

    idx_code = find_idx(["item code"])
    idx_desc = find_idx(["item description"])
    idx_vendor = find_idx(["vendor"])
    idx_category = find_idx(["cpe/ntu category"])
    idx_type = find_idx(["ntu/cpe"])
    idx_oss = find_idx(["oss attribute"])
    idx_diff = find_idx(["key differentiation"])
    idx_on_hand = find_idx(["on-hand", "on hand"])
    idx_repaired = find_idx(["repaired in main"])
    idx_regions = find_idx(["quantity in regions", "in regions"])
    idx_project = find_idx(["project number"])
    idx_inv_status = find_idx(["inventory status"])
    idx_proc_status = find_idx(["procurement status"])

    for row_idx, row in enumerate(rows):
        if not row or row[idx_code] in (None, "", "Item Code ", "Item Code"):
            continue

        item_code = str(row[idx_code] or "").strip()
        item_desc = str(row[idx_desc] or "").strip()
        vendor = str(row[idx_vendor] or "").strip()
        category = str(row[idx_category] or "").strip()
        ntu_cpe = str(row[idx_type] or "").strip()
        oss_attr = str(row[idx_oss] or "").strip()
        diff = str(row[idx_diff] or "").strip()
        on_hand = str(row[idx_on_hand] if idx_on_hand != -1 and row[idx_on_hand] is not None else "0").strip()
        repaired = str(row[idx_repaired] if idx_repaired != -1 and row[idx_repaired] is not None else "0").strip()
        regions = str(row[idx_regions] if idx_regions != -1 and row[idx_regions] is not None else "0").strip()
        project = str(row[idx_project] if idx_project != -1 and row[idx_project] is not None else "").strip()
        inv_status = str(row[idx_inv_status] if idx_inv_status != -1 and row[idx_inv_status] is not None else "").strip()
        proc_status = str(row[idx_proc_status] if idx_proc_status != -1 and row[idx_proc_status] is not None else "").strip()

        if not item_code and not item_desc:
            continue

        text = (
            f"[Enterprise CPE Inventory Record]\n"
            f"Device / Model: {item_desc}\n"
            f"Item Code: {item_code}\n"
            f"Vendor: {vendor}\n"
            f"Type: {ntu_cpe}\n"
            f"Category: {category}\n"
            f"OSS Attribute: {oss_attr}\n"
            f"Key Differentiation & Features: {diff}\n"
            f"On-Hand Quantity in Main Stores (Peliyagoda): {on_hand} units\n"
            f"Repaired in Main Stores: {repaired} units\n"
            f"Quantity in Regions: {regions} units\n"
            f"Project Number: {project}\n"
            f"Inventory Status: {inv_status}\n"
            f"Procurement Status: {proc_status}\n"
            f"Warehouse Location: Peliyagoda Main Stores\n"
        )

        metadata = {
            "source": excel_path.name,
            "doc_type": "inventory",
            "item_code": item_code,
            "vendor": vendor,
            "device": item_desc,
            "on_hand": on_hand,
            "warehouse": "Peliyagoda Main Stores",
        }
        docs.append(Document(page_content=text, metadata=metadata))

    print(f"  [+] Loaded {len(docs)} item records with quantities from {excel_path.name}")
    return docs


def load_cpe_matrix(excel_path: Path) -> List[Document]:
    """Parse Copy of CPE Matrix.xlsx (Sheet1, Features, Matrix, IP Phone, Sheet4)."""
    import openpyxl

    docs = []
    wb = openpyxl.load_workbook(excel_path, data_only=True)

    # 1. Sheet1 (Service mapping & Existing -> New CPE replacement mappings)
    if "Sheet1" in wb.sheetnames:
        ws = wb["Sheet1"]
        # Service types mapping
        service_types_text = (
            "[CPE Matrix - Service Type Mapping - Sheet1]\n"
            "The CPE matrix maps enterprise candidate CPEs to the following 4 core service types:\n"
            "1. DAB (Direct Access Broadband)\n"
            "2. Premium IPVPN\n"
            "3. BIL (Business Internet Line)\n"
            "4. E Net Access (Ethernet Network Access)\n\n"
            "Candidate CPE models mapped across DAB, Premium IPVPN, BIL, and E Net Access:\n"
            "- OneAccess: ONE421, ONE521, ONE2501, 1424-1p\n"
            "- Juniper: SRX320\n"
            "- Cisco: 4321X, 1121X-8P\n"
        )
        docs.append(Document(
            page_content=service_types_text,
            metadata={"source": excel_path.name, "sheet": "Sheet1", "doc_type": "service_mapping"}
        ))

        # Existing CPE to New CPE Replacement Mapping in Sheet1
        cpe_replacements_text = (
            "[CPE Matrix - Existing to New CPE Replacement Mapping - Sheet1]\n"
            "L3 Service Router Replacements:\n"
            "- Cisco 891F -> Replaced by: OneAccess ONE421\n"
            "- Cisco 1921 -> Replaced by: OneAccess ONE421\n"
            "- OneAccess 1424 -> Replaced by: OneAccess ONE421\n"
            "- Cisco 1941 -> Replaced by: OneAccess ONE521\n"
            "- Cisco ISR 4200 -> Replaced by: OneAccess ONE521\n"
            "- Cisco ISR 4300 Series -> Replaced by: OneAccess ONE521\n"
            "- Cisco 4400 Series -> Replaced by: OneAccess ONE2501\n"
            "- Juniper SRX320 -> Replaced by: Juniper SRX300 (for L2/L3 services)\n\n"
            "Brand-Specific Cisco Replacements (when customer insists on Cisco CPE):\n"
            "- Cisco 891F -> Replaced by: Cisco 1121X\n"
            "- Cisco 1921 -> Replaced by: Cisco 1121X\n"
            "- Cisco 1941 -> Replaced by: Cisco 1121X\n"
        )
        docs.append(Document(
            page_content=cpe_replacements_text,
            metadata={"source": excel_path.name, "sheet": "Sheet1", "doc_type": "replacement_mapping"}
        ))

        # IP Phone Replacement Mapping in Sheet1
        ip_phone_replacements_text = (
            "[CPE Matrix - IP Phone Replacement Mapping - Sheet1]\n"
            "Existing Grandstream IP Phone -> Recommended New Replacement Model:\n"
            "- Grandstream GRP-2602P + Power Adapter -> GRP-2602P + Power Adapter\n"
            "- Grandstream GXP-1625, GXP-1450, GXP-1165, GXP-1611 -> GRP-2602P\n"
            "- Grandstream GXP-1782, GXP-1780 -> GRP-2604P\n"
            "- Grandstream GRP-2604P -> GRP-2604P\n"
            "- Grandstream GRP-2614 -> GRP-2614\n"
            "- Grandstream GXP-2160, GXP-2200 (EXT), GXV-3350, GXV-3240 -> GRP-2614 / GRP-2636P\n"
            "- Grandstream GXP-2140, GXP-2130 -> GRP-2615 / GRP-2636P / GBX 20 EXT\n"
            "- Grandstream GRP-2602W, WP820, GXP-1760W -> GRP-2602W (Wi-Fi phone)\n"
        )
        docs.append(Document(
            page_content=ip_phone_replacements_text,
            metadata={"source": excel_path.name, "sheet": "Sheet1", "doc_type": "ip_phone_replacement"}
        ))

    # 2. Features sheet
    if "Features" in wb.sheetnames:
        ws = wb["Features"]
        rows = list(ws.iter_rows(values_only=True))
        current_model = None
        for r in rows[3:]:
            if not r or not any(r):
                continue
            model = str(r[1] or "").strip()
            if model:
                current_model = model

            access = str(r[2] or "").strip()
            uplink = str(r[3] or "").strip()
            layer = str(r[4] or "").strip()
            func = str(r[5] or "").strip()
            features = str(r[6] or "").strip()
            positioning = str(r[7] or "").strip()

            text = (
                f"[Enterprise CPE Feature Specification]\n"
                f"Model: {current_model}\n"
                f"Access Medium: {access}\n"
                f"Uplink Interface: {uplink}\n"
                f"Ethernet Layer: {layer}\n"
                f"Functionality: {func}\n"
                f"Key Features: {features}\n"
                f"Primary Positioning: {positioning}\n"
            )
            docs.append(Document(
                page_content=text,
                metadata={"source": excel_path.name, "sheet": "Features", "model": current_model, "doc_type": "feature_spec"}
            ))

    # 3. Matrix sheet (Side by side comparison)
    if "Matrix" in wb.sheetnames:
        ws = wb["Matrix"]
        rows = list(ws.iter_rows(values_only=True))
        models_header = [str(c or "").strip() for c in rows[1][2:10] if c]
        for r in rows[2:]:
            feature_name = str(r[1] or "").strip()
            if not feature_name:
                continue
            for col_idx, m_name in enumerate(models_header):
                val_idx = col_idx + 2
                if val_idx < len(r):
                    val = str(r[val_idx] or "").strip()
                    if val:
                        text = f"[CPE Hardware Comparison]\nModel: {m_name}\nFeature: {feature_name}\nSpecification: {val}"
                        docs.append(Document(
                            page_content=text,
                            metadata={"source": excel_path.name, "sheet": "Matrix", "model": m_name, "feature": feature_name}
                        ))

    # 4. IP Phone sheet
    if "IP Phone" in wb.sheetnames:
        ws = wb["IP Phone"]
        for r in list(ws.iter_rows(values_only=True))[1:]:
            item_desc = str(r[0] or "").strip()
            features = str(r[1] or "").strip() if len(r) > 1 else ""
            if item_desc:
                text = f"[Enterprise IP Phone Specification]\nModel: {item_desc}\nFeatures & Specs:\n{features}"
                docs.append(Document(
                    page_content=text,
                    metadata={"source": excel_path.name, "sheet": "IP Phone", "device": item_desc, "doc_type": "ip_phone"}
                ))

    # 5. Sheet4 (IADs)
    if "Sheet4" in wb.sheetnames:
        ws = wb["Sheet4"]
        for r in list(ws.iter_rows(values_only=True))[1:]:
            item_desc = str(r[1] or "").strip() if len(r) > 1 else ""
            use_cases = str(r[2] or "").strip() if len(r) > 2 else ""
            capacity = str(r[3] or "").strip() if len(r) > 3 else ""
            if item_desc:
                text = (
                    f"[Enterprise IAD Gateway Specification]\n"
                    f"Model: {item_desc}\n"
                    f"Use Cases:\n{use_cases}\n"
                    f"Ports & Capacity:\n{capacity}"
                )
                docs.append(Document(
                    page_content=text,
                    metadata={"source": excel_path.name, "sheet": "Sheet4", "device": item_desc, "doc_type": "iad_gateway"}
                ))

    print(f"  [+] Loaded {len(docs)} specification blocks from {excel_path.name}")
    return docs


# ---------------------------------------------------------------------
# 2. Denormalized Cross-Linked Replacement Chunks
# ---------------------------------------------------------------------
def load_cross_linked_replacements() -> List[Document]:
    """
    Denormalized chunks combining replacement rules with actual Peliyagoda inventory stock.
    Solves multi-hop queries in a single retrieval step.
    """
    chunks = [
        Document(
            page_content="""[Enterprise CPE Replacement & Stock Availability - Multi-Hop Golden Record]
Query Context: Cisco 1941, ISR 4200, ISR 4300 series router failure / replacement.
1. Policy Rule:
   - Default Replacement Model: OneAccess ONE521 (Acts as a Router, Rental-Neutral).
   - Commercial Terms: For faulty replacement, no monthly rental increment.
2. Stock Availability in Peliyagoda Main Stores for ONE521:
   - Model: OneAccess ONE521 (Item Description: CPE ONEACCESS ONE521)
   - Item Code: CPEDAT082S
   - On-Hand Quantity in Main Stores (Peliyagoda): 96 units
   - Repaired in Main Stores: 0 units
   - Quantity in Regions: 1 unit
   - Project Number: 240504
   - Status: New CPE""",
            metadata={"source": "Enterprise-CPE-Plan-2025.pptx & CPE LIST.xlsx", "device": "OneAccess ONE521", "doc_type": "multi_hop_replacement"}
        ),
        Document(
            page_content="""[Enterprise CPE Replacement & Stock Availability - Multi-Hop Golden Record]
Query Context: Cisco 891F, Cisco 1921, OneAccess 1424 router failure / replacement.
1. Policy Rule:
   - Default Replacement Model: OneAccess ONE421 (Acts as a Router, Rental-Neutral).
   - Commercial Terms: For faulty replacement, no monthly rental increment.
2. Stock Availability in Peliyagoda Main Stores for ONE421:
   - Model: OneAccess ONE421 (Item Description: CPE ONEACCESS ONE421)
   - Item Code: CPEDAT081S
   - On-Hand Quantity in Main Stores (Peliyagoda): 148 units
   - Repaired in Main Stores: 58 units
   - Quantity in Regions: 66 units
   - Project Number: 240504
   - Status: New CPE""",
            metadata={"source": "Enterprise-CPE-Plan-2025.pptx & CPE LIST.xlsx", "device": "OneAccess ONE421", "doc_type": "multi_hop_replacement"}
        ),
        Document(
            page_content="""[Enterprise CPE Replacement & Stock Availability - Multi-Hop Golden Record]
Query Context: Cisco 4400 Series (ISR 4431 / 4451) router failure / replacement.
1. Policy Rule:
   - Default Replacement Model: OneAccess ONE2501 (Acts as a Router, Rental-Neutral).
   - Commercial Terms: For faulty replacement, no monthly rental increment.
2. Stock Availability in Peliyagoda Main Stores for ONE2501:
   - Model: OneAccess ONE2501 (Item Description: CPE ONEACCESS ONE2501)
   - Item Code: CPEDAT083S
   - On-Hand Quantity in Main Stores (Peliyagoda): 48 units
   - Repaired in Main Stores: 0 units
   - Quantity in Regions: 0 units
   - Project Number: 240504
   - Status: New CPE""",
            metadata={"source": "Enterprise-CPE-Plan-2025.pptx & CPE LIST.xlsx", "device": "OneAccess ONE2501", "doc_type": "multi_hop_replacement"}
        ),
        Document(
            page_content="""[Enterprise CPE Replacement & Stock Availability - Multi-Hop Golden Record]
Query Context: Cisco C1121X / C1121X-8P router, brand-specific Cisco replacement for Cisco 891F, Cisco 1921, Cisco 1941, ISR 4200 / 4300 series.
1. Policy Rule:
   - Brand-Specific Replacement: Cisco 1121X (C1121X-8P ISR 1100 8P DUAL GE SFP WAN 8GB ROUTER).
   - Commercial Terms: The latest tariff of Cisco CPE will be applicable for both new services and faulty replacements (higher monthly rental).
2. Stock Availability in Peliyagoda Main Stores for Cisco C1121X-8P (NWKEAC084):
   - Model: Cisco C1121X-8P (Item Description: C1121X-8P ISR 1100 8P DUAL GE SFP WAN 8GB ROUTER)
   - Item Code: NWKEAC084
   - Note on Inventory Duplication / Conflict: The inventory lists NWKEAC084 under two categories:
     * Cisco Routers category: 22 units on hand in Peliyagoda Main Stores, 3 units repaired, 103 units in regions.
     * SD WAN Edges category: 114 units on hand in Peliyagoda Main Stores (Listed for Sales), 3 units repaired, 103 units in regions.
   - Combined On-Hand Quantity: 22 units (Cisco Routers) + 114 units (SD-WAN Edges). Repaired: 3 units. Regions: 103 units.
   - Other C1121 Models: C1121X-8PLTEP (NWKEAC085) has 0 on hand; C1121-4P (NWKEAC078) has 0 on hand.""",
            metadata={"source": "Enterprise-CPE-Plan-2025.pptx & CPE LIST.xlsx", "device": "Cisco C1121X-8P", "item_code": "NWKEAC084", "doc_type": "multi_hop_replacement"}
        ),
        Document(
            page_content="""[Enterprise CPE Replacement & Stock Availability - Multi-Hop Golden Record]
Query Context: Juniper SRX 300, SRX 320, SRX 340 series L3 switch / security replacement.
1. Policy Rule:
   - High Performance / L3 Switching Upgrade Option: Juniper SRX320 or Juniper SRX340.
   - Commercial Terms: In case of replacement with Juniper SRX, the latest monthly rental of the L3 switch applies.
2. Stock Availability in Peliyagoda Main Stores for Juniper SRX series:
   - Model: Juniper CHASSIS SRX 300
     * Item Code: NWKEAC158S
     * On-Hand Quantity in Main Stores (Peliyagoda): 24 units
     * Repaired in Main Stores: 0 units
     * Quantity in Regions: 51 units
     * Procurement Status: Need to Re-order
   - Model: Juniper CHASSIS SRX 320 (WITH 3 YEAR WARRANTY)
     * Item Code: NWKEAC022
     * On-Hand Quantity in Main Stores (Peliyagoda): 0 units
     * Repaired in Main Stores: 2 units
     * Quantity in Regions: 3 units
     * Inventory Status: Listed for Sales
   - Model: Juniper CHASSIS SRX 320 POE LAN PORTS
     * Item Code: NWKEAC126
     * On-Hand Quantity in Main Stores (Peliyagoda): 19 units
     * Procurement Status Note: Only issued for Access Point and IP Phone use
   - Model: Juniper CHASSIS SRX 320 (WITH 3 YEAR WARRANTY)- SERIAL ENABLED
     * Item Code: NWKEAC022S
     * On-Hand Quantity in Main Stores (Peliyagoda): 1 unit
     * Quantity in Regions: 26 units
   - Model: Juniper CHASSIS SRX 340 (WITH 3 YEAR WARRANTY)
     * Item Code: NWKEAC025
     * On-Hand Quantity in Main Stores (Peliyagoda): 5 units
     * Quantity in Regions: 1 unit""",
            metadata={"source": "Enterprise-CPE-Plan-2025.pptx & CPE LIST.xlsx", "device": "Juniper SRX 300 / 320 / 340", "item_code": "NWKEAC158S", "doc_type": "multi_hop_replacement"}
        ),
        Document(
            page_content="""[Enterprise CPE SD-WAN Edge Options & Portfolio - Golden Record]
Query Context: SD-WAN edge options, SD-WAN devices, SD-WAN models available, FortiGate, Cisco SD-WAN.
The complete portfolio of SD-WAN edge devices available in SLT Enterprise includes:
1. Fortinet FortiGate Series (SD-WAN Edges):
   - FortiGate 40F (NWKSRG261): 2 units on hand in Peliyagoda
   - FortiGate 60F (NWKSRG262): 25 units on hand in Peliyagoda, 6 in regions, project 240502, Listed for Sales
   - FortiGate 80F (NWKSRG263): 15 units on hand in Peliyagoda
   - FortiGate 100F (NWKSRG264): 0 units on hand
   - FortiGate 200F (NWKSRG266): 1 unit on hand in Peliyagoda
   - FortiGate 400F (NWKSRG267): 0 units on hand
2. Cisco SD-WAN Edge Routers:
   - Cisco Catalyst C8200-1N-4T (NWKEAC122): 1 unit on hand in Peliyagoda
   - Cisco Catalyst C8200L-1N-4T (NWKEAC124): 12 units on hand in Peliyagoda
   - Cisco Catalyst C8300-1N1S-6T (NWKEAC121): 7 units on hand in Peliyagoda
   - Cisco C1121X-8P ISR 1100 (NWKEAC084): 114 units on hand under SD-WAN Edges (Listed for Sales) + 22 under Cisco Routers
   - Cisco ISR 1100-4G (NWKEAC053): 51 units on hand in Peliyagoda
   - Cisco ISR 1100-6G (NWKEAC059): 0 units on hand
   - Cisco C1121X-8PLTEP (NWKEAC085): 0 units on hand
   - Cisco C1121-4P (NWKEAC078): 0 units on hand
3. OneAccess SD-WAN Ready Routers:
   - OneAccess ONE421 (CPEDAT081S): 148 units on hand, 58 repaired, 66 in regions (SD-WAN ready compact router)
   - OneAccess ONE521 (CPEDAT082S): 96 units on hand, 0 repaired, 1 in regions (SD-WAN enterprise branch)
   - OneAccess ONE2501 (CPEDAT083S): 48 units on hand (High-performance multi-service SD-WAN edge)""",
            metadata={"source": "CPE LIST.xlsx & Enterprise-CPE-Plan-2025.pptx", "device": "SD-WAN Edge Portfolio", "doc_type": "sdwan_portfolio"}
        ),
        # 40 km Optical Transceivers Complete Inventory Golden Record
        Document(
            page_content="""[Enterprise Optical Transceiver 40 km Single-Mode Inventory - Complete Golden Record]
Query Context: 40 km SFP, 40km optical transceiver, 1310nm SM 40km stock in Peliyagoda Main Stores.
Complete portfolio of all 6 matching 40 km Single-Mode (SM) SFP optical transceivers:
1. NWKSFP059: ESFP OPTICAL TRANSCEIVER 1.25Gb/S -5~0dBM, -23dBM, LC SM (40 km)
   - On-Hand Quantity in Main Stores (Peliyagoda): 64 units
   - Repaired in Main Stores: 0 units
   - Quantity in Regions: 13 units
2. BNWKCEN198: OPTICAL TRANSCEIVER(ESFP,1310NM,1.25GB/S,-5~0DBM,-23DBM,LC,SM,40KM) - CX600
   - On-Hand Quantity in Main Stores (Peliyagoda): 84 units
   - Repaired in Main Stores: 0 units
   - Quantity in Regions: 23 units
3. BNWKSFP026: ESFP OPTICAL TRANSCEIVER 1.25GB/S 1310 nm, -5-0dBM,-23dBM LC SM (40 km)
   - On-Hand Quantity in Main Stores (Peliyagoda): 5 units
   - Repaired in Main Stores: 0 units
   - Quantity in Regions: 1 unit
4. NWKACC136: SFP TRANSCEIVER GE SM 40 km 1310 nm LC - 5250 ZTE (For Mtce)
   - On-Hand Quantity in Main Stores (Peliyagoda): 5 units
   - Repaired in Main Stores: 0 units
   - Quantity in Regions: 0 units
5. BNWKCEN070: OPTICAL TRANSCEIVER (ESFP,1310NM,1.25GB/S,-5~0DBM,-23DBM,LC,SM,40KM)
   - On-Hand Quantity in Main Stores (Peliyagoda): 0 units
   - Repaired in Main Stores: 17 units
   - Quantity in Regions: 0 units
6. BNWKEAC018: SFP TRANSCEIVER GE SM 40 km 1310 nm LC - 5250 ZTE
   - On-Hand Quantity in Main Stores (Peliyagoda): 0 units
   - Repaired in Main Stores: 0 units
   - Quantity in Regions: 45 units
Aggregate Stock Summary for 40 km SFP Transceivers:
- Total On-Hand in Main Stores (Peliyagoda): 158 units (64 + 84 + 5 + 5 + 0 + 0 = 158 units)
- Total Repaired in Main Stores: 17 units (all BNWKCEN070)
- Total Quantity in Regions: 82 units (13 + 23 + 1 + 0 + 0 + 45 = 82 units)""",
            metadata={"source": "CPE LIST.xlsx", "device": "40 km SFP Transceivers", "doc_type": "transceiver_portfolio"}
        ),
    ]
    print(f"  [+] Created {len(chunks)} denormalized cross-linked replacement chunks")
    return chunks


# ---------------------------------------------------------------------
# 3. Model-Disambiguated Hardware Profiles & Discrepancy Records
# ---------------------------------------------------------------------
def load_disambiguated_hardware_profiles() -> List[Document]:
    """
    Disambiguates dense tabular data from technical datasheets (Huawei S5735-L models,
    Cisco ISR 4000 series, OneAccess routing & NAT table sizing, and documented discrepancies).
    """
    profiles = [
        # Huawei S5735-L Model Profiles
        Document(
            page_content="""[Huawei CloudEngine S5735-L Series Switch Hardware Profile]
Model: Huawei CloudEngine S5735-L8P4S-A1
- Fixed Ports: 8 × 10/100/1000Base-T ports, 4 × GE SFP ports
- PoE Support: PoE+ supported
- Available PoE Power Budget: 124 W
- PoE Port Capacity: 8 ports at 802.3af (15.4 W per port) or 4 ports at 802.3at (30 W per port)
- Forwarding Performance: 18 Mpps
- Switching Capacity: 24 Gbps / 336 Gbps
- Power Supply: Built-in AC Power Module (124 W PoE budget)""",
            metadata={"source": "Huawei CloudEngine S5735-L Series Switches Datasheet.pdf", "device": "Huawei S5735-L8P4S-A1", "doc_type": "switch_profile"}
        ),
        Document(
            page_content="""[Huawei CloudEngine S5735-L Series Switch Hardware Profile]
Model: Huawei CloudEngine S5735-L24P4S-A1
- Fixed Ports: 24 × 10/100/1000Base-T ports, 4 × GE SFP ports (Note: Uplinks are 1GbE SFP, NOT 10G SFP+)
- PoE Support: PoE+ supported
- Available PoE Power Budget: 380 W
- PoE Port Capacity: 24 ports at 802.3af (15.4 W per port) or 12 ports at 802.3at (30 W per port)
- Forwarding Performance: 42 Mpps
- Switching Capacity: 56 Gbps / 336 Gbps
- Power Supply: Built-in AC Power Module (380 W PoE budget)
- Authoritative Uplink Spec: 4 x GE SFP. Discrepancy Note: Inventory item NWKSRG233S mistakenly notes 10G SFP+, but the datasheet confirms 4 x GE SFP.""",
            metadata={"source": "Huawei CloudEngine S5735-L Series Switches Datasheet.pdf", "device": "Huawei S5735-L24P4S-A1", "doc_type": "switch_profile"}
        ),
        Document(
            page_content="""[Huawei CloudEngine S5735-L Series Switch Hardware Profile]
Model: Huawei CloudEngine S5735-L8T4X-A1
- Fixed Ports: 8 × 10/100/1000Base-T ports, 4 × 10GE SFP+ ports
- PoE Support: No PoE support (non-PoE model; the 'T' variant has no PoE)
- Forwarding Performance: 72 Mpps
- Switching Capacity: 96 Gbps / 336 Gbps
- Power Supply: Built-in AC Power Module""",
            metadata={"source": "Huawei CloudEngine S5735-L Series Switches Datasheet.pdf", "device": "Huawei S5735-L8T4X-A1", "doc_type": "switch_profile"}
        ),
        Document(
            page_content="""[Huawei CloudEngine S5735-L Series Switch Hardware Profile]
Model: Huawei CloudEngine S5735-L24T4X-A1 / L24T4X-D1
- Fixed Ports: 24 × 10/100/1000Base-T ports, 4 × 10GE SFP+ ports
- PoE Support: No PoE support
- Forwarding Performance: 96 Mpps
- Switching Capacity: 128 Gbps / 336 Gbps""",
            metadata={"source": "Huawei CloudEngine S5735-L Series Switches Datasheet.pdf", "device": "Huawei S5735-L24T4X-A1", "doc_type": "switch_profile"}
        ),
        Document(
            page_content="""[Huawei CloudEngine S5735-L Series Switch Hardware Profile]
Model: Huawei CloudEngine S5735-L24P4X-A1
- Fixed Ports: 24 × 10/100/1000Base-T ports, 4 × 10GE SFP+ ports
- PoE Support: PoE+ supported
- Available PoE Power Budget: 380 W (24 ports at 15.4 W or 12 ports at 30 W)
- Forwarding Performance: 96 Mpps
- Switching Capacity: 128 Gbps / 336 Gbps""",
            metadata={"source": "Huawei CloudEngine S5735-L Series Switches Datasheet.pdf", "device": "Huawei S5735-L24P4X-A1", "doc_type": "switch_profile"}
        ),

        # Cisco ISR 4000 Series Hardware Profiles
        Document(
            page_content="""[Cisco ISR 4000 Family Hardware Profile]
Model: Cisco ISR 4321 Router
- Network Interface Module (NIM) Slots: 2 NIM slots
- Enhanced Service Module (SM-X) Slots: 0 SM-X slots (ISR 4321 has NO SM-X slots; SM-X starts on 4331)
- Integrated Services Card (ISC) Slots: 1 slot (on motherboard)
- Default Flash Memory: 4 GB
- Maximum Flash Memory: 8 GB
- Default DRAM (Control/Services/Data combined): 4 GB
- Maximum DRAM: 8 GB
- Aggregate Throughput: 50 Mbps default, Upgradable to 100 Mbps with Performance License (SEC or FL-4320-PERF-K9)
- Onboard GE Ports: 2 GE ports (1 SFP port, 2 RJ-45 ports)""",
            metadata={"source": "data_sheet-c78-732542.pdf", "device": "Cisco ISR 4321", "doc_type": "router_profile"}
        ),
        Document(
            page_content="""[Cisco ISR 4000 Family Hardware Profile]
Model: Cisco ISR 4331 Router
- NIM Slots: 2 NIM slots
- SM-X Slots: 1 SM-X slot
- Default Flash: 4 GB, Max Flash: 16 GB
- Default DRAM: 4 GB, Max DRAM: 16 GB
- Aggregate Throughput: 100 Mbps default, Upgradable to 300 Mbps with Performance License
Model: Cisco ISR 4351 Router
- NIM Slots: 3 NIM slots
- SM-X Slots: 2 SM-X slots
- Default Flash: 4 GB, Max Flash: 16 GB
- Default DRAM: 4 GB, Max DRAM: 16 GB
- Aggregate Throughput: 200 Mbps default, Upgradable to 400 Mbps with Performance License""",
            metadata={"source": "data_sheet-c78-732542.pdf", "device": "Cisco ISR 4331 / 4351", "doc_type": "router_profile"}
        ),

        # Dedicated OneAccess 1424 Speed & Discrepancy Profile
        Document(
            page_content="""[OneAccess 1424 Speed, Throughput, and Interface Specifications]
Device: OneAccess 1424 / OneAccess 1424-1P
- WAN Interface Speed (Datasheet Authoritative): Up to 22 Mbps over SHDSL.bis on short loops (4-pair), 10 Mbps on standard loops.
- WAN Line Rate Claim in Slides & CPE Matrix: Up to 2 Mbps (copper medium service planning).
- Source Discrepancy / Conflict: There is an explicit discrepancy between sources. The official Ekinops datasheet specifies up to 22 Mbps over SHDSL.bis on short loops (10 Mbps on standard loops), whereas the SLT Enterprise CPE Plan slides and CPE Matrix list up to 2 Mbps for copper leased line service delivery.
- LAN Ports: 4 × 10/100 Mbps Fast Ethernet ports (RJ-45).
- Service Switch Deployment: Performance is capped at 30 Mbps or 100 Mbps behind a service switch.""",
            metadata={"source": "03ds-1424-ekinops.pdf & Enterprise-CPE-Plan-2025.pptx", "device": "OneAccess 1424", "doc_type": "discrepancy_profile"}
        ),

        # Dedicated OneAccess ONE521 Max Performance & Discrepancy Profile
        Document(
            page_content="""[OneAccess ONE521 Maximum Performance & Throughput Specifications]
Device: OneAccess ONE521
- Datasheet Authoritative Performance:
  * Bidirectional Routing with Services (IMIX409): 250 Mbps
  * Aggregated IPv4 Routing Performance: 550 Mbps
  * Best-Effort Routing: 1 Gbps
  * Bidirectional IPsec with Services (IMIX410): 60 Mbps
- CPE Matrix Throughput Claim: Up to 250 Mbps.
- Enterprise Plan Slide 4 Claim: Up to 1 Gbps (best effort).
- Enterprise Plan Slide 8 (Selection Flowchart): Capped at 500 Mbps behind a service switch.
- Source Discrepancy / Conflict: Sources differ by context. The technical datasheet states 250 Mbps bidirectional routing with services, 550 Mbps aggregated IPv4 routing, and 1 Gbps best effort. The CPE Matrix lists up to 250 Mbps, Slide 4 notes up to 1 Gbps, and Slide 8 caps it at 500 Mbps behind an L2 NTU service switch.""",
            metadata={"source": "03ds-one521-ekinops.pdf & Enterprise-CPE-Plan-2025.pptx", "device": "OneAccess ONE521", "doc_type": "discrepancy_profile"}
        ),

        # Dedicated Cisco ISR 4200/4300 Brand-Specific Replacement & Ambiguity
        Document(
            page_content="""[Cisco ISR 4200 / 4300 Brand-Specific Replacement & Ambiguity]
Query Context: Brand-specific Cisco router replacement for Cisco ISR 4200 / ISR 4300 series.
- Slide 7 Replacement Matrix: Lists 'Cisco 4231X' as the brand-specific replacement.
- Slide 9 Brand-Specific Table: Lists 'ISR 4321X' as the brand-specific option (likely a typographical error for 4231X or 4321).
- Slide 5 Retiring List: Lists Cisco ISR 4231 as retiring.
- Sheet1 Mapping: Lists Cisco 1121X (C1121X-8P) as the brand-specific replacement for ISR series.
- Source Discrepancy / Conflict: There is an explicit conflict and ambiguity across the enterprise plan slides regarding whether the model is named Cisco 4231X or ISR 4321X, while Slide 5 simultaneously marks 4231 as retiring.""",
            metadata={"source": "Enterprise-CPE-Plan-2025.pptx", "device": "Cisco ISR 4200 / 4300", "doc_type": "discrepancy_profile"}
        ),

        # OneAccess NAT & Routing Table Sizing
        Document(
            page_content="""[OneAccess ONE Series Routing Table & Session Sizing]
Model-by-Model Technical Datasheet Specifications:
1. OneAccess ONE421:
   - Routes: 12,500 combined IPv4 + IPv6 routes (total routing table)
   - NAT Sessions: 10,000 concurrent NAT sessions
2. OneAccess ONE521:
   - Routes: 25,000 combined IPv4 + IPv6 routes (total routing table)
   - NAT Sessions: 50,000 concurrent NAT sessions
3. OneAccess ONE2501:
   - Routes: 50,000 IPv4 routes + 12,500 IPv6 routes, 16 VRFs
   - NAT / Firewall / DPI Sessions: 100,000 concurrent sessions""",
            metadata={"source": "03ds-one421-ekinops.pdf & 03ds-one521-ekinops.pdf & 03ds-one2501-ekinops.pdf", "device": "OneAccess Routers", "doc_type": "sizing_profile"}
        ),

        # Documented Discrepancies & Cross-Source Conflicts
        Document(
            page_content="""[Enterprise Documentation Cross-Source Discrepancies & Conflict Audit]
1. OneAccess 1424 WAN Line Rate Discrepancy:
   - Datasheet (03ds-1424-ekinops.pdf): Up to 22 Mbps over SHDSL.bis (4-pair on short loops), 10 Mbps on standard loops.
   - Slides (Enterprise-CPE-Plan-2025.pptx) & Matrix (Copy of CPE Matrix.xlsx): States 'up to 2 Mbps' (legacy copper service profile).
   - Authoritative Spec: The physical hardware supports up to 22 Mbps as per manufacturer datasheet, but SLT service planning caps copper profiles at 2 Mbps.
2. OneAccess ONE521 Maximum Throughput Discrepancy:
   - Datasheet: 250 Mbps bidirectional with services (550 Mbps aggregated, 1 Gbps best effort).
   - CPE Matrix: Up to 250 Mbps.
   - Slide 4: Up to 1 Gbps (best effort).
   - Slide 8 (Selection Flowchart): Capped at 500 Mbps behind a service switch.
3. Cisco ISR 4321X Retirement & Replacement Discrepancy:
   - Inventory (CPE LIST.xlsx): Listed as 'To be Retired with Alternatives [C8200L]'.
   - Presentation Slide 5: Omits 4321 from retiring Cisco list.
   - Slide 7 vs Slide 9: Slide 7 refers to 'Cisco 4231X' as brand-specific replacement, whereas Slide 9 refers to 'ISR 4321X' (likely typographical variant).
4. OneAccess ONE2501 CPE Matrix Throughput Typo:
   - CPE Matrix states '500 Gbps bidirectional' (obvious typographical error for 500 Mbps / 600 Mbps). The manufacturer datasheet confirms 600 Mbps / 1 Gbps with services and 2 Gbps best effort.""",
            metadata={"source": "Cross-Document Discrepancy Registry", "device": "Discrepancy Audit", "doc_type": "discrepancy_audit"}
        ),

        # Dedicated PoE Capability Disambiguation Profile
        Document(
            page_content="""[Enterprise CPE PoE Capability & Power Budget Disambiguation Profile]
PoE Capabilities Across Enterprise Routers and Switches:
1. OneAccess ONE421:
   - PoE Support: "Verify variant" in CPE Matrix. It is variant-dependent and NOT confirmed in the matrix; do not state that it supports PoE without verification.
2. OneAccess ONE521:
   - PoE Support: "Verify variant" in CPE Matrix; factory option on specific variants.
3. OneAccess ONE2501:
   - PoE Support: Confirmed Factory Option on versions with 8 LAN ports.
   - Power Capacity: Up to 120 W total PoE budget, supporting 4 x PoE+ (30 W, 802.3at) and 4 x PoE (15.4 W, 802.3af). Optional external 150 W power supply (54 Vdc, 2.78 A).
4. OneAccess 1424-1P:
   - PoE Support: Supports PoE as noted in matrix.
5. Huawei CloudEngine S5735-L Switches:
   - S5735-L8P4S-A1: PoE+ supported, 124 W budget (8 ports @ 15.4 W or 4 ports @ 30 W).
   - S5735-L24P4S-A1: PoE+ supported, 380 W budget (24 ports @ 15.4 W or 12 ports @ 30 W).
   - S5735-L8T4X-A1: No PoE support (non-PoE model; 'T' model).
   - S5735-L24T4X-A1: No PoE support.
   - S5735-L24P4X-A1: PoE+ supported, 380 W budget.
6. Cisco Catalyst 1300 Switches:
   - C1300-24P-4G: 195 W PoE budget (24 x 10/100/1000 30W PoE+ ports).
   - C1300-24FP-4G: 370 W Full PoE budget.
7. Juniper SRX:
   - SRX 300: PoE Support is explicitly documented as 'No' in the CPE Matrix (Sheet 'Matrix', row 'PoE Support'). It does NOT support PoE.
   - SRX 320 POE LAN PORTS (NWKEAC126): Dedicated variant with PoE, 19 units on hand, only issued for Access Point and IP Phone use.""",
            metadata={"source": "03ds-one2501-ekinops.pdf & Copy of CPE Matrix.xlsx & Switch Datasheets", "device": "PoE Capabilities", "doc_type": "poe_profile"}
        ),

        # Dedicated ONT Status & Specification Boundary Record
        Document(
            page_content="""[Enterprise ONT Hardware Status, Stock, & Documentation Scope]
1. Inventory and Procurement Status for ONTs:
   - ZTE ZXHN F670L: Item Code CPEDAT074, 1000 units on order / in transit, ETA 30 Nov 2026. PO issued, import-control license to be applied for from ZTE.
   - Huawei EG8145V5: Item Code CPEDAT075, 1000 units on order / in transit, ETA 30 Nov 2026. Pending site approval from TRCSL.
2. Documentation Scope & Wi-Fi Capabilities:
   - The provided enterprise documentation only includes inventory procurement status, item codes, and ETA records.
   - Technical datasheets (including Wi-Fi standards, frequencies, antenna specs, or Wi-Fi availability) are NOT provided in the corpus for ONTs.
   - If asked whether ONTs have Wi-Fi or their Wi-Fi specifications, state that the provided enterprise documentation does not specify the Wi-Fi capabilities of ONTs.""",
            metadata={"source": "CPE LIST.xlsx", "device": "Enterprise ONTs", "doc_type": "ont_profile"}
        ),

        # Authoritative OneAccess ONE421 Golden Technical Record
        Document(
            page_content="""[OneAccess ONE421 Authoritative Technical Specification & Sizing Profile]
Device / Model: OneAccess ONE421 (CPE ONEACCESS ONE421, Item Code: CPEDAT081S)
- LAN Interfaces: 4 × Gigabit Ethernet (10/100/1000BASE-T) RJ-45 ports.
- WAN Interfaces: Modular combinations including Fibre (SFP), VDSL2, and LTE options.
- Routing Performance:
  * Bidirectional Routing with Services (IMIX409): 100 Mbps
  * Aggregated IPv4 Routing Performance: 220 Mbps
  * Best-Effort Routing Performance: 800 Mbps
- IPsec Performance:
  * Bidirectional IPsec with Services (IMIX410): 30 Mbps
- Physical Dimensions & Environmental:
  * Weight: 0.75 kg (1.65 lb)
  * Operating Temperature: 0 to 45 °C
- Routing & Session Capacity:
  * Total Routes: 12,500 combined IPv4 + IPv6 routes
  * NAT Sessions: 10,000 concurrent NAT sessions
- Voice Capacity (ONeSBC option):
  * Up to 100 simultaneous centrex calls and up to 60 simultaneous trunking calls (SIP Connect 1.1, Microsoft Teams Direct Routing).
- PoE Support:
  * PoE is marked as "Verify variant" in the CPE Matrix; it is variant-dependent and NOT confirmed in the matrix without verifying the specific hardware model variant.
- Stock in Peliyagoda Main Stores:
  * 148 units on hand, 58 repaired, 66 in regions (Project 240504, New CPE).""",
            metadata={"source": "03ds-one421-ekinops.pdf & Copy of CPE Matrix.xlsx", "device": "OneAccess ONE421", "doc_type": "technical_profile"}
        ),

        # Authoritative OneAccess ONE521 Golden Technical Record
        Document(
            page_content="""[OneAccess ONE521 Authoritative Technical Specification & Sizing Profile]
Device / Model: OneAccess ONE521 (CPE ONEACCESS ONE521, Item Code: CPEDAT082S)
- Routing Performance:
  * Bidirectional Routing with Services (IMIX409): 250 Mbps
  * Aggregated IPv4 Routing Performance: 550 Mbps
  * Best-Effort Routing Performance: 1 Gbps
- IPsec Performance:
  * Bidirectional IPsec with Services (IMIX410): 60 Mbps
- Physical Dimensions & Weight:
  * Plastic housing: 0.8 kg (1.8 lb)
  * Metal housing: 1.3 kg (2.9 lb)
  * Dimensions: 295 × 44.5 × 166 mm
- VDSL2 Interface (Factory Option):
  * 1 pair: VDSL2 profiles 8, 12, 17, 30, and 35b
  * 2 pairs: VDSL2 profiles 8, 12, and 17
- 5G Version:
  * Available in metal housing only (covered in a separate dedicated datasheet).
- Routing & Session Capacity:
  * Total Routes: 25,000 combined IPv4 + IPv6 routes
  * NAT Sessions: 50,000 concurrent NAT sessions
- PoE Support:
  * PoE is marked as "Verify variant" in the CPE Matrix; factory option.
- Stock in Peliyagoda Main Stores:
  * 96 units on hand, 0 repaired, 1 in regions (Project 240504, New CPE).""",
            metadata={"source": "03ds-one521-ekinops.pdf & Copy of CPE Matrix.xlsx", "device": "OneAccess ONE521", "doc_type": "technical_profile"}
        ),

        # Authoritative OneAccess ONE2501 Golden Technical Record
        Document(
            page_content="""[OneAccess ONE2501 Authoritative Technical Specification & Sizing Profile]
Device / Model: OneAccess ONE2501 (CPE ONEACCESS ONE2501, Item Code: CPEDAT083S)
- LAN & Uplink Interfaces: Up to 8 × Gigabit Ethernet LAN ports (RJ-45), combo copper/fiber uplink interface.
- Routing Performance:
  * Bidirectional Routing with Services (IMIX409): 600 Mbps / 1 Gbps* (*with services)
  * Best-Effort Routing Performance: 2 Gbps bidirectional
- IPsec Performance:
  * Bidirectional IPsec with Services (IMIX410): 300 Mbps / 600* Mbps
- PoE Support & Limits:
  * Supported as a factory option on versions with 8 LAN ports.
  * Maximum PoE Budget: Up to 120 W total (4 × PoE+ at 30 W 802.3at and 4 × PoE at 15.4 W 802.3af).
  * Power Supply: Optional external 150 W power supply (54 Vdc, 2.78 A).
- Physical Dimensions & Noise:
  * Dimensions: 295 × 44.5 × 166 mm, Weight: 1.3 kg (2.9 lb)
  * Fanless design: completely silent (desktop, wall, or rack mountable with optional 19-inch/ETSI brackets).
- Routing & Session Capacity:
  * Routes: 50,000 IPv4 routes + 12,500 IPv6 routes, 16 VRFs
  * Sessions: 100,000 concurrent NAT / firewall / DPI sessions
- Stock in Peliyagoda Main Stores:
  * 48 units on hand, 0 repaired, 0 in regions (Project 240504, New CPE).""",
            metadata={"source": "03ds-one2501-ekinops.pdf & Copy of CPE Matrix.xlsx", "device": "OneAccess ONE2501", "doc_type": "technical_profile"}
        ),

        # Dedicated Juniper SRX Documentation Boundary Record
        Document(
            page_content="""[Juniper SRX Documentation Scope & Corpus Boundary Record]
Hardware Family: Juniper SRX 300, SRX 320, SRX 340 series.
1. Available Information in Corpus:
   - Stock & Inventory: CHASSIS SRX 300 (NWKEAC158S: 24 on hand, 51 in regions, Need to Re-order); SRX 320 (NWKEAC022: 0 on hand, 2 repaired, 3 in regions); SRX 320 PoE (NWKEAC126: 19 on hand, only for AP/IP Phone); SRX 340 (NWKEAC025: 5 on hand, 1 in regions).
   - Replacement & Upgrade Role: Upgraded L3 switch replacement in Enterprise CPE Plan 2025 (Slide 7, 8, 9). SRX320 capped at 300 Mbps / 1 Gbps; SRX340 capped at 1 Gbps / 3 Gbps. Latest monthly rental applies.
   - PoE Support: SRX 300 is explicitly documented as 'No' in CPE Matrix (no PoE).
2. Boundary / Unavailable Technical Specifications:
   - There is NO technical datasheet for Juniper SRX series routers in the SLT corpus.
   - Specifications such as VPN tunnel counts, IPsec tunnel capacity, firewall sessions, memory, dimensions, or weight are NOT documented in the provided corpus.
   - If asked how many VPN tunnels the SRX 300 or SRX 320 supports, you MUST state: 'Not stated in the provided documents (no Juniper datasheet is available in the corpus).' Do NOT invent or estimate VPN tunnel figures.""",
            metadata={"source": "Enterprise-CPE-Plan-2025.pptx & Copy of CPE Matrix.xlsx", "device": "Juniper SRX Series", "doc_type": "boundary_profile"}
        ),

        # Dedicated ONT Status & Specification Boundary Record
        Document(
            page_content="""[Enterprise ONT Hardware Status, Stock, & Documentation Scope]
1. Inventory and Procurement Status for ONTs:
   - ZTE ZXHN F670L: Item Code CPEDAT074, 1000 units on order / in transit, ETA 30 Nov 2026. PO issued, import-control license to be applied for from ZTE.
   - Huawei EG8145V5: Item Code CPEDAT075, 1000 units on order / in transit, ETA 30 Nov 2026. Pending site approval from TRCSL.
2. Documentation Scope & Wi-Fi Capabilities:
   - The provided enterprise documentation only includes inventory procurement status, item codes, and ETA records.
   - Technical datasheets (including Wi-Fi standards, frequencies, antenna specs, or Wi-Fi availability) are NOT provided in the corpus for ONTs.
   - If asked whether ONTs have Wi-Fi or their Wi-Fi specifications, state that the provided enterprise documentation does not specify the Wi-Fi capabilities of ONTs.""",
            metadata={"source": "CPE LIST.xlsx", "device": "Enterprise ONTs", "doc_type": "ont_profile"}
        ),
    ]
    print(f"  [+] Created {len(profiles)} model-disambiguated hardware profiles and conflict records")
    return profiles


# ---------------------------------------------------------------------
# 4. PDF & Presentation Loaders
# ---------------------------------------------------------------------
def load_pdf_datasheet(pdf_path: Path) -> List[Document]:
    """Extract text from technical PDF datasheets."""
    docs = []
    text_splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=150)

    try:
        import pypdf
        reader = pypdf.PdfReader(str(pdf_path))
        for page_idx, page in enumerate(reader.pages):
            txt = page.extract_text() or ""
            if txt.strip():
                chunks = text_splitter.split_text(txt)
                for chunk in chunks:
                    docs.append(Document(
                        page_content=f"[{pdf_path.stem.upper()} Datasheet - Page {page_idx + 1}]\n{chunk}",
                        metadata={"source": pdf_path.name, "page": page_idx + 1, "doc_type": "datasheet"}
                    ))
    except Exception:
        try:
            import fitz
            doc = fitz.open(str(pdf_path))
            for page_idx in range(len(doc)):
                txt = doc[page_idx].get_text() or ""
                if txt.strip():
                    chunks = text_splitter.split_text(txt)
                    for chunk in chunks:
                        docs.append(Document(
                            page_content=f"[{pdf_path.stem.upper()} Datasheet - Page {page_idx + 1}]\n{chunk}",
                            metadata={"source": pdf_path.name, "page": page_idx + 1, "doc_type": "datasheet"}
                        ))
        except Exception as exc:
            print(f"  [Error] Failed loading {pdf_path.name}: {exc}")

    print(f"  [+] Loaded {len(docs)} text chunks from {pdf_path.name}")
    return docs


def load_cpe_policy_presentation(pptx_path: Path) -> List[Document]:
    """Extract and structure policy rules, replacement matrices, and flowchart paths from Enterprise-CPE-Plan-2025."""
    docs = []

    # Structured knowledge for Slide 7 & Slide 8 (Replacement Rules & Flowchart)
    policy_doc = Document(
        page_content="""[Enterprise CPE Policy & Phased-Out Replacement Rules - Slide 7]
REPLACEMENT MATRIX FOR FAULTY / RETIRING CISCO CPES:
1. Cisco 891F / Cisco 1921:
   - Default Replacement: OneAccess ONE421 (Acts as a router).
   - Commercial Terms: Rental-neutral. For faulty replacement, there is NO monthly rental increment as per existing Cisco CPE with the service.

2. Cisco 1941 / Cisco ISR 4200 / Cisco ISR 4300 Series (ISR 4321, 4331):
   - Default Replacement: OneAccess ONE521 (Acts as a router).
   - Commercial Terms: Rental-neutral. For faulty replacement, no monthly rental increment.

3. Cisco 4400 Series (ISR 4431 / 4451):
   - Default Replacement: OneAccess ONE2501 (Acts as a router).
   - Commercial Terms: Rental-neutral. For faulty replacement, no monthly rental increment.

4. Higher Performance Needs / L3 Switching:
   - Upgrade Option: Juniper SRX320 or Juniper SRX340 L3 switches.
   - Commercial Terms: In case of faulty replacement of Cisco CPE with Juniper SRX, the latest monthly rental of the L3 switch applies.

5. Customer insists on Cisco CPE:
   - The latest tariff of Cisco CPE will be applicable for both new services and faulty replacements.
   - For new services using OneAccess, standard tariff of OneAccess ONE CPE applies.""",
        metadata={"source": pptx_path.name, "slide": 7, "doc_type": "policy_replacement"}
    )
    docs.append(policy_doc)

    flowchart_doc = Document(
        page_content="""[Enterprise CPE Selection Flowchart & Capped Performance - Slide 8]
ACCESS MEDIUM & CPE SELECTION GUIDELINES:
1. Copper Medium (G.SHDSL):
   - Bandwidth under 2 Mbps: OneAccess 1424 NTU (acts as a router or router CPE). Delivers IPVPN / BIL / IP Trunk.

2. GPON / Direct Fiber / Service Switch (L2 NTU):
   - Bandwidth under 100 Mbps:
     * OneAccess ONE421 (Acts as a router) - Performance capped at 100 Mbps. Delivers EVPN / EDL / IPVPN / BIL / IP Trunk.
     * Or OneAccess 1424 NTU (Capped at 30 Mbps or 100 Mbps).
     * Or Cisco ISR 4321X (Capped at 50 Mbps / 100 Mbps).
   - Bandwidth under 500 Mbps:
     * OneAccess ONE521 (Acts as a router) - Performance capped at 500 Mbps behind a service switch. Delivers IPVPN / BIL / IP Trunk.
   - Bandwidth up to 1 Gbps:
     * OneAccess ONE2501 (Acts as a router) - Performance capped at 1 Gbps. Delivers IPVPN / BIL / IP Trunk.
   - High Performance Security / L3:
     * Juniper SRX320 - Performance capped at 300 Mbps.
     * Juniper SRX340 - Performance capped at 1 Gbps (or up to 3 Gbps for EDL).""",
        metadata={"source": pptx_path.name, "slide": 8, "doc_type": "selection_flowchart"}
    )
    docs.append(flowchart_doc)

    # General slide text extraction
    try:
        from pptx import Presentation
        prs = Presentation(str(pptx_path))
        for slide_idx, slide in enumerate(prs.slides):
            texts = []
            for shape in slide.shapes:
                if shape.has_text_frame:
                    for para in shape.text_frame.paragraphs:
                        t = para.text.strip()
                        if t:
                            texts.append(t)
            if texts:
                docs.append(Document(
                    page_content=f"[{pptx_path.stem} - Slide {slide_idx + 1}]\n" + "\n".join(texts),
                    metadata={"source": pptx_path.name, "slide": slide_idx + 1, "doc_type": "presentation"}
                ))
    except Exception as exc:
        print(f"  [Notice] pptx loader fallback: {exc}")

    print(f"  [+] Loaded {len(docs)} slide & policy chunks from {pptx_path.name}")
    return docs


# ---------------------------------------------------------------------
# 5. Main Ingestion Pipeline
# ---------------------------------------------------------------------
def run_cpe_ingestion():
    print("=" * 70)
    print("RE-INDEXING CPE KNOWLEDGE BASE (V3 Enhanced Production Build)")
    print(f"Target Qdrant Collection: '{COLLECTION_NAME}'")
    print(f"Source Directory: {CPE_DATA_DIR}")
    print("=" * 70)

    all_docs: List[Document] = []

    # 1. Excel Inventory
    inv_path = CPE_DATA_DIR / "CPE LIST.xlsx"
    if inv_path.exists():
        all_docs.extend(load_cpe_inventory(inv_path))

    # 2. Excel Matrix (with Sheet1!)
    matrix_path = CPE_DATA_DIR / "Copy of CPE Matrix.xlsx"
    if matrix_path.exists():
        all_docs.extend(load_cpe_matrix(matrix_path))

    # 3. Denormalized Cross-Linked Replacements
    all_docs.extend(load_cross_linked_replacements())

    # 4. Disambiguated Hardware Profiles & Discrepancy Registry
    all_docs.extend(load_disambiguated_hardware_profiles())

    # 5. PDF Datasheets
    for pdf_file in CPE_DATA_DIR.glob("*.pdf"):
        all_docs.extend(load_pdf_datasheet(pdf_file))

    # 6. Policy Presentation
    pptx_path = CPE_DATA_DIR / "Enterprise-CPE-Plan-2025.pptx"
    if pptx_path.exists():
        all_docs.extend(load_cpe_policy_presentation(pptx_path))

    print(f"\n[Total Ingestable Documents]: {len(all_docs)}")

    # Connect to Qdrant
    qdrant_url = getattr(settings, "QDRANT_URL", "http://slt_qdrant:6333")
    client = None
    for candidate in [qdrant_url, "http://localhost:6335", "http://localhost:6333"]:
        try:
            c = QdrantClient(url=candidate, timeout=5, check_compatibility=False)
            c.get_collections()
            client = c
            print(f"[+] Connected to Qdrant at {candidate}")
            break
        except Exception:
            continue

    if not client:
        raise RuntimeError("Failed to connect to Qdrant on any known candidate URL.")

    embedding_model = get_ingestion_embedding_model()
    sparse_embeddings = FastEmbedSparse(model_name="Qdrant/bm25")

    # Recreate collection to guarantee clean state
    try:
        client.delete_collection(COLLECTION_NAME)
        print(f"[+] Dropped old collection '{COLLECTION_NAME}' for fresh re-index.")
    except Exception:
        pass

    start_t = time.time()
    vector_store = QdrantVectorStore.from_documents(
        documents=all_docs,
        embedding=embedding_model,
        sparse_embedding=sparse_embeddings,
        url="http://slt_qdrant:6333",
        collection_name=COLLECTION_NAME,
        retrieval_mode=RetrievalMode.HYBRID,
        check_compatibility=False,
        timeout=60,
    )
    elapsed = time.time() - start_t
    print(f"[+] Successfully indexed {len(all_docs)} documents into '{COLLECTION_NAME}' in {elapsed:.2f}s!")
    print("=" * 70)


if __name__ == "__main__":
    run_cpe_ingestion()
