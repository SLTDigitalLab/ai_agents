"""
Enterprise CPE Knowledge Base Ingestion Script (V4 - generated from the source files).

What changed from V3
--------------------
* Every record that comes from CPE LIST.xlsx (one record per item, plus the group / list / total
  records such as "all IP phones", "40 km SFPs", "to be retired") is now GENERATED from the sheet.
  Nothing is typed by hand, so quantities, codes and statuses always match the sheet and totals are
  calculated in code.
* Sheet1 of the CPE Matrix (Existing -> New CPE) is generated from the merged cells.
* Fixed loader bugs: section-header rows were indexed as items, #N/A leaked into text, the Matrix
  sheet dropped the ONE2501 column, the Features sheet lost its LAN-switching and Security columns
  and labelled rows "(L3 Switch)" / "(Service Switch)", and every table on the deck slides
  (retiring lists, options table, spec tables) was skipped.
* Slide 8 (flowchart) is stored as an explicit path table transcribed from the slide image.
* Hand-written records that remain (datasheet profiles, policy, discrepancy registry) were corrected
  against the source files. Interpretations are labelled as such.
* Ingest-time guard: every item code in every record must exist in the inventory, records that carry
  an item_code must match the device they describe, and CPEDAT074/075 may never appear next to an ONT.
* Safer indexing: builds a new collection, then swaps the alias `cpe_docs` to it. Use --in-place for
  the old drop-and-rebuild behaviour.

Usage (inside the backend container):
    python /app/scripts/ingest_cpe_knowledgebase.py --dry-run     # build + validate, write dump, no Qdrant
    python /app/scripts/ingest_cpe_knowledgebase.py               # build, validate, index, swap alias
    python /app/scripts/ingest_cpe_knowledgebase.py --in-place    # old behaviour (drop collection, rebuild)
"""

import argparse
import json
import re
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

COLLECTION_NAME = "cpe_docs"          # the name the application queries (an alias after the first V4 run)
CPE_DATA_DIR = BACKEND_DIR / "data" / "cpe"

CODE_RE = re.compile(r"\b(B?(?:CPEDAT|CPEFTH|CPEDTA|NWK[A-Z]{3})\d{2,3}S?)\b")


# ---------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------
def clean(v) -> str:
    """Cell -> clean string. #N/A and None become ''."""
    if v is None:
        return ""
    s = str(v).replace("\xa0", " ").strip()
    return "" if s == "#N/A" else s


def to_int(v) -> Optional[int]:
    """Numeric cell -> int. Blank / #N/A -> None (unknown, NOT zero)."""
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return int(v)
    return None


def qty(n: Optional[int]) -> str:
    return "unknown" if n is None else str(n)


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


# ---------------------------------------------------------------------
# 1. CPE LIST.xlsx  ->  InvRow objects  ->  generated documents
# ---------------------------------------------------------------------
@dataclass
class InvRow:
    section: str
    ntu_cpe: str
    category: str
    oss: str
    vendor: str
    code: str
    desc: str
    diff: str
    on_hand: Optional[int]
    repaired: Optional[int]
    regions: Optional[int]
    project: str
    port: str
    inv_status: str
    proc_status: str
    duplicate: bool = False

    @property
    def lifecycle(self) -> str:
        if self.inv_status.lower() == "retired":
            return "Retired"
        t = f"{self.inv_status} {self.proc_status}".lower()
        if "to be retired" in t:
            return "To be retired"
        if "for maintenance" in t:
            return "Maintenance only"
        if "listed for sales" in t:
            return "Listed for sales"
        return "Not stated"

    @property
    def replenishment(self) -> str:
        t = f"{self.inv_status} {self.proc_status}".lower()
        if "need to re-order" in t:
            return "Need to re-order"
        if "re-odering when alarmed" in t or "re-ordering when alarmed" in t:
            return "Re-order when alarmed"
        if "lc opening" in t:
            return "LC opening in progress"
        if "po issued" in t:
            return "PO issued"
        return ""

    @property
    def on_order_suspected(self) -> bool:
        t = f"{self.inv_status} {self.proc_status}".lower()
        return any(k in t for k in ("lc opening", "po issued", "eta :", "pending site approval"))

    @property
    def eta(self) -> str:
        m = re.search(r"ETA\s*:\s*(.+)", f"{self.inv_status} {self.proc_status}", re.I)
        return m.group(1).strip() if m else ""

    @property
    def triplet(self) -> str:
        return f"{qty(self.on_hand)}/{qty(self.repaired)}/{qty(self.regions)}"


def parse_inventory(excel_path: Path):
    """Return (rows, as_of_date, by_code). Section-header rows are tracked, never emitted as items."""
    import openpyxl

    wb = openpyxl.load_workbook(excel_path, data_only=True)
    ws = wb.active
    all_rows = list(ws.iter_rows(values_only=True))

    as_of = ""
    for r in all_rows[:3]:
        for c in r:
            m = re.search(r"(\d{1,2}/\d{1,2}/\d{4})", str(c or ""))
            if m:
                as_of = m.group(1)

    header_i, header = None, None
    for i, r in enumerate(all_rows):
        if r and any("item code" in str(c or "").lower() for c in r):
            header_i, header = i, [str(c or "").strip().lower() for c in r]
            break
    if header is None:
        raise RuntimeError("Could not find the header row in CPE LIST.xlsx")

    def idx(*cands):
        for cand in cands:
            for i, h in enumerate(header):
                if cand in h:
                    return i
        return -1

    c_type, c_cat, c_oss = idx("ntu/cpe"), idx("cpe/ntu category"), idx("oss attribute")
    c_vendor, c_code, c_desc = idx("vendor"), idx("item code"), idx("item description")
    c_diff, c_on = idx("key differentiation"), idx("on-hand", "on hand")
    c_rep, c_reg = idx("repaired in main"), idx("quantity in regions", "in regions")
    c_proj, c_port = idx("project number"), idx("port")
    c_inv, c_proc = idx("inventory status"), idx("procurement status")

    def get(row, i):
        return row[i] if 0 <= i < len(row) else None

    rows: List[InvRow] = []
    section = ""
    for raw in all_rows[header_i + 1:]:
        raw = list(raw)
        code, desc = clean(get(raw, c_code)), clean(get(raw, c_desc))
        if not code or code.lower().startswith("item code"):
            continue
        has_numbers = any(isinstance(get(raw, i), (int, float)) for i in (c_on, c_rep, c_reg))
        if not desc and not has_numbers:
            section = code                       # e.g. "Cisco Routers", "IP Phone", "ONT"
            continue
        if not desc:
            continue
        proj = clean(get(raw, c_proj))
        rows.append(InvRow(
            section=section, ntu_cpe=clean(get(raw, c_type)), category=clean(get(raw, c_cat)),
            oss=clean(get(raw, c_oss)), vendor=clean(get(raw, c_vendor)), code=code, desc=desc,
            diff=clean(get(raw, c_diff)),
            on_hand=to_int(get(raw, c_on)), repaired=to_int(get(raw, c_rep)), regions=to_int(get(raw, c_reg)),
            project="" if proj in ("0", "0.0") else proj, port=clean(get(raw, c_port)),
            inv_status=clean(get(raw, c_inv)), proc_status=clean(get(raw, c_proc)),
        ))

    by_code: Dict[str, List[InvRow]] = defaultdict(list)
    for r in rows:
        by_code[r.code].append(r)
    for group in by_code.values():
        if len(group) > 1:
            for r in group:
                r.duplicate = True
    return rows, as_of, by_code


def dup_note(r: InvRow, by_code) -> str:
    listings = "; ".join(f"under '{o.section}': {qty(o.on_hand)} on hand" for o in by_code[r.code])
    return (f"CONFLICT: item code {r.code} is listed more than once in the sheet ({listings}). "
            f"Report every listing and say the total is unconfirmed; do NOT add them together.")


def caution_note(r: InvRow) -> str:
    eta = f" (ETA {r.eta})" if r.eta else ""
    return ("CAUTION: the sheet shows an ETA / LC / PO / approval status for this item, so the 'on hand' figure "
            f"may be an incoming or ordered quantity. Say that receipt of the stock is not confirmed{eta}.")


def inventory_row_doc(r: InvRow, as_of: str, by_code) -> Document:
    lines = [
        "[Enterprise CPE Inventory Record]",
        f"Stock As Of: {as_of} (sheet date)",
        f"Device / Model: {r.desc}",
        f"Item Code: {r.code}",
        f"Vendor: {r.vendor or 'not stated'}",
        f"Type: {r.ntu_cpe}",
        f"Category: {r.category}",
        f"Inventory Block (sheet section): {r.section}",
        f"OSS Attribute: {r.oss}",
        f"Key Differentiation & Features: {r.diff}",
        f"On-Hand Quantity in Main Stores (Peliyagoda): {qty(r.on_hand)} units",
        f"Repaired in Main Stores: {qty(r.repaired)} units",
        f"Quantity in Regions: {qty(r.regions)} units",
        f"Project Number: {r.project or 'not stated'}",
        f"Port: {r.port}" if r.port else "",
        f"Inventory Status (sheet column): {r.inv_status or 'blank'}",
        f"Procurement Status (sheet column): {r.proc_status or 'blank'}",
        f"Lifecycle (derived): {r.lifecycle}",
        f"Replenishment (derived): {r.replenishment}" if r.replenishment else "",
        "Warehouse Location: Peliyagoda Main Stores",
    ]
    if None in (r.on_hand, r.repaired, r.regions):
        lines.append("NOTE: one or more quantities are blank or not available in the sheet, so they are unknown (not zero).")
    if r.on_order_suspected:
        lines.append(caution_note(r))
    if r.duplicate:
        lines.append(dup_note(r, by_code))
    return Document(
        page_content="\n".join(l for l in lines if l),
        metadata={
            "source": "CPE LIST.xlsx", "doc_type": "inventory", "item_code": r.code, "vendor": r.vendor,
            "device": r.desc, "on_hand": qty(r.on_hand), "warehouse": "Peliyagoda Main Stores",
            "section": r.section, "lifecycle": r.lifecycle, "stock_as_of": as_of,
        },
    )


def _sum(items, attr) -> int:
    return sum(getattr(i, attr) or 0 for i in items)


def _item_block(n: int, r: InvRow, by_code) -> List[str]:
    out = [f"{n}. {r.code}: {r.desc}",
           f"   - Stock: {qty(r.on_hand)} on hand, {qty(r.repaired)} repaired, {qty(r.regions)} in regions ({r.triplet})"]
    status = []
    if r.inv_status:
        status.append(f"Inventory Status: {r.inv_status}")
    if r.proc_status:
        status.append(f"Procurement Status: {r.proc_status}")
    if status:
        out.append("   - " + "; ".join(status))
    if r.on_order_suspected:
        out.append("   - " + caution_note(r))
    if r.duplicate:
        out.append("   - " + dup_note(r, by_code))
    return out


def group_doc(title: str, ctx: str, noun: str, items: List[InvRow], as_of: str, by_code,
              doc_type: str, device: str, extra: str = "") -> Optional[Document]:
    if not items:
        return None
    lines = [f"[{title} - Peliyagoda Main Stores (stock as of {as_of})]",
             f"Query Context: {ctx}",
             f"The inventory (CPE LIST.xlsx) holds exactly {len(items)} {noun}."]
    for n, r in enumerate(items, 1):
        lines += _item_block(n, r, by_code)
    stocked = [r for r in items if (r.on_hand or 0) > 0]
    empty = [r for r in items if (r.on_hand or 0) == 0]
    lines.append(f"Computed totals (calculated by code): {_sum(items, 'on_hand')} on hand, "
                 f"{_sum(items, 'repaired')} repaired, {_sum(items, 'regions')} in regions.")
    lines.append(f"With stock on hand (> 0): {len(stocked)} of {len(items)}: "
                 + (", ".join(f"{r.desc[:40]} ({r.code}): {r.on_hand}" for r in stocked) or "none"))
    lines.append(f"With 0 on hand: {len(empty)}: " + (", ".join(f"{r.code}" for r in empty) or "none"))
    dups = sorted({r.code for r in items if r.duplicate})
    if dups:
        lines.append(f"Duplicate item code(s) in this list: {', '.join(dups)}; totals are unconfirmed for them.")
    if any(r.on_order_suspected for r in items):
        lines.append("Some quantities above may be incoming / ordered stock, see the CAUTION lines.")
    if extra:
        lines.append(extra)
    return Document(page_content="\n".join(lines),
                    metadata={"source": "CPE LIST.xlsx", "device": device, "doc_type": doc_type, "stock_as_of": as_of})


def reach_km(desc: str) -> Optional[float]:
    m = re.search(r"(\d+(?:\.\d+)?)\s*km\b", desc, re.I)
    return float(m.group(1)) if m else None


def build_group_docs(rows: List[InvRow], as_of: str, by_code) -> List[Document]:
    docs: List[Optional[Document]] = []
    sec = lambda name: [r for r in rows if r.section == name]

    specs = [
        ("Huawei Switches Complete Inventory Record", "list all huawei switches, huawei switch inventory, huawei models held in stock.",
         "Huawei switches", [r for r in rows if r.vendor.lower().startswith("huawei") and r.section != "ONT"],
         "switch_portfolio", "Huawei Switches",
         "Scope note: the Huawei ONT EG8145V5 is an optical network terminal (ONT), NOT a switch."),
        ("Grandstream IP Phones Complete Inventory & Stock Record",
         "list all ip phones we have, ip phone stock, which ip phones have stock on hand in main stores right now.",
         "Grandstream IP phone / extension-module items", sec("IP Phone"), "phone_portfolio", "IP Phones Portfolio", ""),
        ("SD-WAN Edge Devices Complete Inventory Record",
         "list all sd-wan edge devices with stock, list sd-wan edge devices, sd-wan models available.",
         "SD-WAN edge devices", sec("SD WAN Edges"), "sdwan_portfolio", "SD-WAN Edge Devices",
         "Scope note: OneAccess ONE421 / ONE521 / ONE2501 are 'SD-WAN Ready' per the deck but are not in the SD-WAN Edges inventory block."),
        ("FortiGate Inventory Record", "fortigate stock, list all fortigates, fortigates out of stock.",
         "FortiGate models", [r for r in rows if r.vendor.lower().startswith("fortigate")],
         "sdwan_portfolio", "FortiGate", ""),
        ("Cisco Routers Complete Inventory Record", "list all cisco routers in the inventory, cisco router stock.",
         "items in the 'Cisco Routers' block", sec("Cisco Routers"), "router_portfolio", "Cisco Routers",
         "Scope note: other Cisco routers (C1121X-8PLTEP, ISR1100, C8200, C8300, C8200L) are in the 'SD WAN Edges' block."),
        ("Wi-Fi Access Points Complete Inventory Record", "list all wifi access points and how many are on hand, wifi ap stock.",
         "Wi-Fi access points", sec("WiFi Access points"), "wifi_portfolio", "Wi-Fi Access Points", ""),
        ("IAD and Voice Gateways Complete Inventory Record",
         "list all IADs with their port counts and stock, IAD stock, integrated access devices.",
         "IAD / ATA items", sec("IAD"), "iad_portfolio", "IAD Gateways", ""),
        ("Juniper SRX Chassis Complete Inventory Record", "show me all juniper srx chassis in stock, juniper srx chassis stock.",
         "Juniper SRX chassis rows", [r for r in rows if "CHASSIS SRX" in r.desc.upper()],
         "srx_portfolio", "Juniper SRX Chassis",
         "Accessory note: NWKEAC029S (OEM TRANSCEIVERS - SRX-SFP-1GE-LX) is an optical transceiver accessory, NOT a router chassis."),
        ("Optical Transceivers / SFP Complete Inventory Record", "list all sfp transceivers in stock, optical transceiver inventory.",
         "SFP / transceiver / port-module items", [r for r in sec("SFP/tranciver") if any(k in r.desc.upper() for k in ["SFP", "TRANSCEIVER", "NIM", "EHWIC"])], "transceiver_portfolio", "SFP Transceivers", ""),
        ("ONT Inventory & Procurement Record", "ont stock, when will the onts arrive, eta and blocker for the ont.",
         "ONT items", sec("ONT"), "ont_profile", "Enterprise ONTs",
         "Interpretation warning: the sheet shows 1000 in the On-Hand column AND an ETA, so these may be incoming units. "
         "Confirm with the stores team before saying they are in stock."),
        ("SHDSL NTU Complete Inventory Record", "list shdsl ntu stock, oneaccess 1424 and 1432 stock.",
         "SHDSL NTU items", sec("SHDSL NTU"), "shdsl_portfolio", "SHDSL NTUs", ""),
        ("ONE CPE Complete Inventory Record", "oneaccess one421 one521 one2501 stock.",
         "OneAccess ONE-series items", sec("One CPE"), "one_series_portfolio", "OneAccess ONE Series", ""),
    ]
    for title, ctx, noun, items, dt, device, extra in specs:
        docs.append(group_doc(title, ctx, noun, items, as_of, by_code, dt, device, extra))

    # by optical reach, computed from the description (category labels in the sheet are inconsistent)
    optics = [r for r in sec("SFP/tranciver") if any(k in r.desc.upper() for k in ["SFP", "TRANSCEIVER", "NIM", "EHWIC"])]
    for km in sorted({reach_km(r.desc) for r in optics if reach_km(r.desc)}):
        items = [r for r in optics if reach_km(r.desc) == km]
        label = f"{km:g} km"
        docs.append(group_doc(f"{label} Optical Transceiver Inventory - Complete Record",
                              f"{label} SFP, {label.replace(' ', '')} optical transceiver stock, total {label} SFPs in Peliyagoda Main Stores.",
                              f"{label} transceivers (matched on the distance in the item description)", items, as_of,
                              by_code, "transceiver_portfolio", f"{label} SFP Transceivers"))

    # lifecycle / replenishment lists
    retiring = [r for r in rows if r.lifecycle == "To be retired"]
    retired = [r for r in rows if r.lifecycle == "Retired"]
    d = group_doc("Inventory Items Marked 'To be Retired' Complete Record",
                  "which items are marked to be retired in the inventory, retiring items in inventory.",
                  "items marked 'To be Retired' (sheet Procurement Status column; Inventory Status is usually 'For Maintenance' or 'Listed for Sales')",
                  retiring, as_of, by_code, "retiring_portfolio", "Retiring Inventory Items")
    if d and retired:
        d.page_content += (f"\nSeparately, {len(retired)} items already have Inventory Status 'Retired':\n"
                           + "\n".join(f"- {r.code}: {r.desc} ({r.triplet} on hand/repaired/regions)" for r in retired))
    docs.append(d)

    need = [r for r in rows if r.replenishment == "Need to re-order"]
    alarm = [r for r in rows if r.replenishment == "Re-order when alarmed"]
    extra = (f"Disambiguation: a different procurement status, 'Re-odering when alarmed' (typo in the sheet), applies to {len(alarm)} other items: "
             + ", ".join(f"{r.code} ({r.desc[:36]})" for r in alarm)
             + ". Do NOT mix them into the 'Need to Re-order' list.")
    docs.append(group_doc("Procurement Status 'Need to Re-order' Complete Inventory Record",
                          "which items need to be re-ordered, need to re-order list, procurement status re-order.",
                          "items with Procurement Status 'Need to Re-order'", need, as_of, by_code,
                          "reorder_portfolio", "Re-order Items", extra))
    docs.append(group_doc("Procurement Status 'Re-ordering when alarmed' Inventory Record",
                          "which items are re-ordered when alarmed, re-ordering when alarmed list.",
                          "items with Procurement Status 'Re-odering when alarmed'", alarm, as_of, by_code,
                          "reorder_portfolio", "Re-order When Alarmed Items"))
    docs.append(group_doc("Inventory Items For Maintenance Only",
                          "which items are for maintenance only, maintenance-only inventory.",
                          "items with Inventory Status 'For Maintenance' (and not marked to be retired)",
                          [r for r in rows if r.lifecycle == "Maintenance only"], as_of, by_code,
                          "maintenance_portfolio", "Maintenance Items"))
    docs.append(group_doc("Inventory Items With Open ETA / LC / PO Status",
                          "which items are on order, incoming stock, LC opening in progress, ETA, import license or approval blockers.",
                          "items whose sheet status shows an ETA, LC, PO or approval step",
                          [r for r in rows if r.on_order_suspected], as_of, by_code,
                          "on_order_portfolio", "On-Order Items"))
    return [d for d in docs if d]


def build_inventory_docs(rows, as_of, by_code) -> List[Document]:
    docs = [inventory_row_doc(r, as_of, by_code) for r in rows]
    groups = build_group_docs(rows, as_of, by_code)
    print(f"  [+] Inventory: {len(rows)} item records + {len(groups)} generated list/total records (stock as of {as_of})")
    return docs + groups


def stock_lines(by_code, *codes) -> str:
    """Stock text for curated records, always read from the sheet."""
    out = []
    for c in codes:
        for r in by_code.get(c, []):
            where = f" [listed under '{r.section}']" if r.duplicate else ""
            out.append(f"- {r.code} {r.desc}{where}: {qty(r.on_hand)} on hand, {qty(r.repaired)} repaired, "
                       f"{qty(r.regions)} in regions" + (f"; status: {r.proc_status or r.inv_status}" if (r.proc_status or r.inv_status) else ""))
    return "\n".join(out)


# ---------------------------------------------------------------------
# 2. Copy of CPE Matrix.xlsx
# ---------------------------------------------------------------------
def tidy_model(s: str) -> str:
    s = clean(s)
    s = re.sub(r"One Access One(\d+)", r"OneAccess ONE\1", s, flags=re.I)
    s = re.sub(r"^Cisco(\d)", r"Cisco \1", s)
    return s


def merged_value_map(ws) -> Dict[tuple, object]:
    """(row, col) -> value for every cell inside a merged range (value of the top-left cell)."""
    m = {}
    for rng in ws.merged_cells.ranges:
        v = ws.cell(rng.min_row, rng.min_col).value
        for r in range(rng.min_row, rng.max_row + 1):
            for c in range(rng.min_col, rng.max_col + 1):
                m[(r, c)] = v
    return m


def load_sheet1_docs(ws, source: str) -> List[Document]:
    docs: List[Document] = []
    mv = merged_value_map(ws)

    def val(r, c):
        v = ws.cell(r, c).value
        return clean(v if v is not None else mv.get((r, c)))

    # header of the replacement table: Service | Existing CPE | New CPE
    hdr_row = hdr_col = None
    for row in ws.iter_rows():
        for cell in row:
            if clean(cell.value).lower() == "existing cpe":
                hdr_row, hdr_col = cell.row, cell.column
    if hdr_row is None:
        return docs

    # service matrix above the table (DAB / Premium IPVPN / BIL / E Net Access -> candidate models)
    cand = []
    for r in range(1, hdr_row):
        cells = [(c.column, clean(c.value)) for c in ws[r] if clean(c.value)]
        if len(cells) >= 3:
            cand.append((r, cells))
    if cand:
        svc_col = min(cells[0][0] for _, cells in cand)
        lines = []
        for _, cells in cand:
            if cells[0][0] == svc_col:
                lines.append(f"- {cells[0][1]}: " + ", ".join(v for _, v in cells[1:]))
        docs.append(Document(
            page_content="[CPE Matrix - Service Type Mapping - Sheet1]\nCandidate CPE models per service type (as written in the sheet):\n"
                         + "\n".join(lines)
                         + "\nThe sheet does not define the service acronyms (DAB, BIL); do not expand them.",
            metadata={"source": source, "sheet": "Sheet1", "doc_type": "service_mapping"}))

    scenario, entries, order = "Default replacement", [], []
    for r in range(hdr_row + 1, ws.max_row + 1):
        existing = tidy_model(val(r, hdr_col))
        service = val(r, hdr_col - 1)
        new = tidy_model(val(r, hdr_col + 1))
        if not existing:
            continue
        if existing.lower().startswith("in case of brand"):
            scenario = "Brand-specific replacement (customer insists on Cisco CPE)"
            continue
        entries.append((scenario, service, existing, new or existing))
        if scenario not in order:
            order.append(scenario)

    phone_rows = [e for e in entries if "grandstream" in e[2].lower()]
    router_rows = [e for e in entries if e not in phone_rows]
    for scen in order:
        rr = [e for e in router_rows if e[0] == scen]
        if rr:
            docs.append(Document(
                page_content=f"[CPE Matrix - Existing to New CPE Mapping - Sheet1 (generated from the merged cells)]\n{scen}:\n"
                             + "\n".join(f"- {e[2]} -> Replaced by: {e[3]}  (service: {e[1]})" for e in rr),
                metadata={"source": source, "sheet": "Sheet1", "doc_type": "replacement_mapping"}))
    if phone_rows:
        by_new = defaultdict(list)
        for _, _, old, new in phone_rows:
            by_new[new].append(old)
        lines = [f"- {new} is the new model for: " + ", ".join(olds) for new, olds in by_new.items()]
        lines += [f"- {old} -> {new}" for _, _, old, new in phone_rows]
        docs.append(Document(
            page_content="[CPE Matrix - IP Phone Replacement Mapping - Sheet1 (generated from the merged cells)]\n"
                         "Existing Grandstream IP phone -> new replacement model:\n" + "\n".join(lines),
            metadata={"source": source, "sheet": "Sheet1", "doc_type": "ip_phone_replacement"}))
    return docs


def load_features_docs(ws, source: str) -> List[Document]:
    """One record per model, merging the continuation rows and keeping LAN switching + Security."""
    mv = merged_value_map(ws)
    hdr_row = None
    for row in ws.iter_rows():
        for c in row:
            if clean(c.value).lower() == "cpe model":
                hdr_row, model_col = c.row, c.column
    if hdr_row is None:
        return []
    cols = {clean(ws.cell(hdr_row, c).value).lower(): c for c in range(model_col, ws.max_column + 1) if clean(ws.cell(hdr_row, c).value)}
    models, current = [], None
    for r in range(hdr_row + 1, ws.max_row + 1):
        name = clean(ws.cell(r, model_col).value)
        if not any(clean(ws.cell(r, c).value) for c in cols.values()):
            continue
        if name and not name.startswith("("):
            current = {"name": name, "suffix": "", "rows": []}
            models.append(current)
        elif name.startswith("(") and current:
            current["suffix"] = name
        if current:
            current["rows"].append(r)
    docs = []
    for m in models:
        fields = {}
        for label, c in cols.items():
            if label == "cpe model":
                continue
            seen, vals = set(), []
            for r in m["rows"]:
                v = clean(ws.cell(r, c).value)
                if v and v not in seen:
                    seen.add(v)
                    vals.append(v)
            fields[label] = "; ".join(vals)
        title = f"{m['name']} {m['suffix']}".strip()
        text = f"[Enterprise CPE Feature Specification]\nModel: {title}\n" + "\n".join(
            f"{k.strip().title()}: {v}" for k, v in fields.items() if v)
        docs.append(Document(page_content=text,
                             metadata={"source": source, "sheet": "Features", "model": title, "doc_type": "feature_spec"}))
    return docs


def load_cpe_matrix(excel_path: Path) -> List[Document]:
    import openpyxl

    docs: List[Document] = []
    wb = openpyxl.load_workbook(excel_path, data_only=True)
    src = excel_path.name

    if "Sheet1" in wb.sheetnames:
        docs += load_sheet1_docs(wb["Sheet1"], src)
    if "Features" in wb.sheetnames:
        docs += load_features_docs(wb["Features"], src)

    if "Matrix" in wb.sheetnames:
        ws = wb["Matrix"]
        hdr_row, feat_col = None, None
        for row in ws.iter_rows():
            for c in row:
                if clean(c.value).lower() == "feature":
                    hdr_row, feat_col = c.row, c.column
        models = {c: clean(ws.cell(hdr_row, c).value) for c in range(feat_col + 1, ws.max_column + 1)
                  if clean(ws.cell(hdr_row, c).value)}
        for r in range(hdr_row + 1, ws.max_row + 1):
            feature = clean(ws.cell(r, feat_col).value)
            if not feature:
                continue
            for c, model in models.items():
                v = clean(ws.cell(r, c).value)
                if v:
                    docs.append(Document(
                        page_content=f"[CPE Hardware Comparison]\nModel: {model}\nFeature: {feature}\nSpecification: {v}",
                        metadata={"source": src, "sheet": "Matrix", "model": model, "feature": feature, "doc_type": "matrix_spec"}))

    if "IP Phone" in wb.sheetnames:
        for r in list(wb["IP Phone"].iter_rows(values_only=True))[1:]:
            item, feats = clean(r[0]), clean(r[1]) if len(r) > 1 else ""
            if item:
                docs.append(Document(
                    page_content=f"[Enterprise IP Phone Specification]\nModel: {item}\nFeatures & Specs:\n{feats or 'No features listed in the sheet.'}",
                    metadata={"source": src, "sheet": "IP Phone", "device": item, "doc_type": "ip_phone"}))

    if "Sheet4" in wb.sheetnames:
        for r in list(wb["Sheet4"].iter_rows(values_only=True))[1:]:
            item = clean(r[1]) if len(r) > 1 else ""
            if item:
                docs.append(Document(
                    page_content=(f"[Enterprise IAD Gateway Specification]\nModel: {item}\n"
                                  f"Use Cases:\n{clean(r[2]) if len(r) > 2 else ''}\n"
                                  f"Ports & Capacity:\n{clean(r[3]) if len(r) > 3 else ''}"),
                    metadata={"source": src, "sheet": "Sheet4", "device": item, "doc_type": "iad_gateway"}))

    print(f"  [+] Loaded {len(docs)} specification blocks from {src}")
    return docs


# ---------------------------------------------------------------------
# 3. Replacement + stock records (policy from the deck/sheet, stock from the inventory)
# ---------------------------------------------------------------------
RENTAL_NEUTRAL = ("For faulty replacement there is no monthly rental increment as per the existing Cisco CPE with the service "
                  "(slide 7). For new services the standard tariff of the OneAccess ONE CPE applies.")


def load_cross_linked_replacements(by_code) -> List[Document]:
    specs = [
        ("Cisco 1941, ISR 4200, ISR 4300 series router failure / replacement.",
         "Default Replacement Model: OneAccess ONE521 (acts as a router).", RENTAL_NEUTRAL, ["CPEDAT082S"],
         "OneAccess ONE521", ""),
        ("Cisco 891F, Cisco 1921, OneAccess 1424 router failure / replacement.",
         "Default Replacement Model: OneAccess ONE421 (acts as a router).", RENTAL_NEUTRAL, ["CPEDAT081S"],
         "OneAccess ONE421", ""),
        ("Cisco 4400 Series router failure / replacement.",
         "Default Replacement Model: OneAccess ONE2501 (acts as a router).", RENTAL_NEUTRAL, ["CPEDAT083S"],
         "OneAccess ONE2501", ""),
        ("Cisco C1121X / C1121X-8P brand-specific Cisco replacement for Cisco 891F, Cisco 1921, Cisco 1941.",
         "Brand-Specific Replacement: Cisco 1121X (C1121X-8P) when the customer insists on Cisco CPE (Sheet1 and slide 7).",
         "The latest tariff of Cisco CPE applies for both new services and faulty replacements (higher monthly rental; slides 7 and 9). "
         "Note: slide 7 names a different Cisco model (written '4231X') for ISR 4200 / 4300; see the ISR 4200 / 4300 record.",
         ["NWKEAC084", "NWKEAC085", "NWKEAC078"], "Cisco C1121X-8P",
         "Grounding rule: if a code is listed more than once, report every listing and flag the conflict; never add them together."),
        ("Juniper SRX 300, SRX 320, SRX 340 series L3 switch / security replacement.",
         "High-performance / upgrade option: Juniper SRX320 or SRX340 (slide 7 and 9). Sheet1: SRX320 -> SRX300 (L2/L3 services).",
         "In case of faulty replacement of a Cisco CPE with a Juniper SRX, the latest monthly rental of the L3 switch applies (slide 7).",
         ["NWKEAC158S", "NWKEAC022", "NWKEAC126", "NWKEAC022S", "NWKEAC025"], "Juniper SRX 300 / 320 / 340", ""),
    ]
    docs = []
    for ctx, rule, terms, codes, device, extra in specs:
        text = ("[Enterprise CPE Replacement & Stock Availability - Multi-Hop Golden Record]\n"
                f"Query Context: {ctx}\n1. Policy Rule:\n   - {rule}\n   - Commercial Terms: {terms}\n"
                f"2. Stock Availability in Peliyagoda Main Stores (read from CPE LIST.xlsx):\n{stock_lines(by_code, *codes)}")
        if extra:
            text += "\n" + extra
        meta = {"source": "Enterprise-CPE-Plan-2025.pptx & CPE LIST.xlsx", "device": device, "doc_type": "multi_hop_replacement"}
        if len(codes) == 1 or device == "Cisco C1121X-8P":
            meta["item_code"] = codes[0]
        docs.append(Document(page_content=text, metadata=meta))
    print(f"  [+] Created {len(docs)} replacement + stock records (stock read from the sheet)")
    return docs


# ---------------------------------------------------------------------
# 4. Curated datasheet profiles / discrepancy registry (hand-written, verified against the sources)
# ---------------------------------------------------------------------
def load_curated_records(by_code) -> List[Document]:
    D = lambda text, source, device, doc_type, **extra: Document(
        page_content=text, metadata={"source": source, "device": device, "doc_type": doc_type, **extra})
    HUAWEI = "Huawei CloudEngine S5735-L Series Switches Datasheet.pdf"
    docs: List[Document] = []

    # ---- Huawei S5735-L ----
    docs += [
        D("""[Huawei CloudEngine S5735-L Series Switch Hardware Profile]
Model: Huawei CloudEngine S5735-L8P4S-A1
- Fixed Ports: 8 x 10/100/1000Base-T ports, 4 x GE SFP ports
- PoE Support: PoE+ supported
- Available PoE Power Budget: 124 W
- PoE Port Capacity: 8 ports at 802.3af (15.4 W per port) or 4 ports at 802.3at (30 W per port)
- Forwarding Performance: 18 Mpps
- Switching Capacity: 24 Gbps / 336 Gbps
- Power Supply: Built-in AC Power Module (124 W PoE budget)""", HUAWEI, "Huawei S5735-L8P4S-A1", "switch_profile"),
        D("""[Huawei CloudEngine S5735-L Series Switch Hardware Profile]
Model: Huawei CloudEngine S5735-L24P4S-A1
- Fixed Ports: 24 x 10/100/1000Base-T ports, 4 x GE SFP ports (Note: uplinks are 1GbE SFP, NOT 10G SFP+)
- PoE Support: PoE+ supported
- Available PoE Power Budget: 380 W
- PoE Port Capacity: 24 ports at 802.3af (15.4 W per port) or 12 ports at 802.3at (30 W per port)
- Forwarding Performance: 42 Mpps
- Switching Capacity: 56 Gbps / 336 Gbps
- Power Supply: Built-in AC Power Module (380 W PoE budget)
- Authoritative Uplink Spec: 4 x GE SFP. Discrepancy Note: inventory item NWKSRG233S describes 4 x 10G SFP+ uplinks, but the datasheet and the CPE Matrix both show 4 x GE SFP.""",
          HUAWEI, "Huawei S5735-L24P4S-A1", "switch_profile"),
        D("""[Huawei CloudEngine S5735-L Series Switch Hardware Profile]
Model: Huawei CloudEngine S5735-L8T4X-A1
- Fixed Ports: 8 x 10/100/1000Base-T ports, 4 x 10GE SFP+ ports
- PoE Support: No PoE support (non-PoE model; the 'T' variant has no PoE)
- Forwarding Performance: 72 Mpps
- Switching Capacity: 96 Gbps / 336 Gbps
- Power Supply: Built-in AC Power Module""", HUAWEI, "Huawei S5735-L8T4X-A1", "switch_profile"),
        D("""[Huawei CloudEngine S5735-L Series Switch Hardware Profile]
Model: Huawei CloudEngine S5735-L24T4X-A1 / L24T4X-D1
- Fixed Ports: 24 x 10/100/1000Base-T ports, 4 x 10GE SFP+ ports
- PoE Support: No PoE support
- Forwarding Performance: 96 Mpps
- Switching Capacity: 128 Gbps / 336 Gbps""", HUAWEI, "Huawei S5735-L24T4X-A1", "switch_profile"),
        D("""[Huawei CloudEngine S5735-L Series Switch Hardware Profile]
Model: Huawei CloudEngine S5735-L24P4X-A1
- Fixed Ports: 24 x 10/100/1000Base-T ports, 4 x 10GE SFP+ ports
- PoE Support: PoE+ supported
- Available PoE Power Budget: 380 W (24 ports at 15.4 W or 12 ports at 30 W)
- Forwarding Performance: 96 Mpps
- Switching Capacity: 128 Gbps / 336 Gbps""", HUAWEI, "Huawei S5735-L24P4X-A1", "switch_profile"),
        D("""[Huawei CloudEngine S5735-L Stacking Note]
- The S5735-L supports intelligent stack (iStack), stacking through electrical ports.
- Mixed stacking between CloudEngine S5735-L-A/D models and S5735-L-A1/D1 models is NOT supported
  (for example an S5735-L-A cannot be stacked with an S5735-L-A1).""", HUAWEI, "Huawei S5735-L Series", "stacking_profile"),
    ]

    # ---- Cisco ISR 4000 / Catalyst 1300 ----
    ISR = "data_sheet-c78-732542.pdf"
    docs += [
        D("""[Cisco ISR 4000 Family Hardware Profile]
Model: Cisco ISR 4321 Router
- Network Interface Module (NIM) Slots: 2
- SM-X Slots: none listed for the 4321 (the 4331 has 1 SM-X, the 4351 has 2)
- Integrated Services Card (ISC) Slots: 1
- Default Flash: 4 GB (maximum 8 GB). Default DRAM: 4 GB
- Aggregate Throughput: 50 Mbps default; 100 Mbps with the performance license FL-4320-PERF-K9
- Onboard Ports: the datasheet ordering text says '2 onboard GE'. The inventory description reads '2GE LAN/WAN, 1SFP, 2RJ45' and the Features sheet says one RJ-45 plus one GE/SFP combo interface.""",
          ISR, "Cisco ISR 4321", "router_profile"),
        D("""[Cisco ISR 4000 Family Hardware Profile]
Model: Cisco ISR 4331 Router - 2 NIM slots, 1 SM-X slot, default flash 4 GB, default DRAM 4 GB.
  Aggregate throughput 100 Mbps default, 300 Mbps with the performance license.
Model: Cisco ISR 4351 Router - 3 NIM slots, 2 SM-X slots, default flash 4 GB, default DRAM 4 GB.
  Aggregate throughput 200 Mbps default, 400 Mbps with the performance license.""", ISR, "Cisco ISR 4331 / 4351", "router_profile"),
        D("""[Cisco Catalyst 1300 Series Profile]
- Front-panel stacking: up to 8 switches in a stack, up to 400 ports managed as one system. Only PIDs of the same family can be stacked (cross-stacking between families is not supported).
- C1300-24P-4G: 24 x 10/100/1000 PoE+ (30 W) ports with a 195 W PoE budget, 4 x SFP uplinks, 56 Gbps switching capacity, 41.67 Mpps forwarding rate, weight 3.53 kg, dimensions 444.3 x 299 x 43.94 mm.
- C1300-24FP-4G: 24 x PoE+ ports with a 370 W power budget in the ordering section (the power table lists 375 W).
Inventory note: the inventory row 'CISCO CATALYST 1300 SWITCH' (CPEDAT124S) does not name the exact model, and its quantity is marked 'LC Opening inprogress'.""",
          "nb-06-cat1300-ser-data-sheet-cte-en.pdf", "Cisco Catalyst 1300", "switch_profile"),
    ]

    # ---- OneAccess 1424 ----
    docs.append(D("""[OneAccess 1424 SHDSL Ethernet Access Device - Datasheet Profile & Source Conflict]
Device: OneAccess 1424 / 1424-1P (inventory: CPEDAT004S serial enabled, CPEDAT004).
- SHDSL: G.SHDSL.bis on 1, 2 or 4 line pairs; datasheet line rates up to 22 Mbps on short distances and up to 10 Mbps on operator standard loop lengths.
- Slides and CPE Matrix: copper uplink 'bandwidth up to 2 Mbps' (slide 8: 'BW under 2 Mbps'). The sources do not say why they differ (a service limit is possible but is NOT confirmed).
- Slide 8: behind a GPON ONT bridge the 1424 NTU (router CPE) is capped at 30 Mbps.
- LAN: Features sheet lists 4 x Fast Ethernet switch + 1 FE port. USB 2.0 port (at USB 1.1 speed) for backup via an external USB dongle.
- Tunnels: 25 IPsec / L2TP / GRE tunnels. VLANs: 200. Routing and bridging performance: 250 Kpps.
- Dimensions: 275 x 65 x 145 mm, weight 0.8 kg (1.76 lb), external 12 V - 2 A adapter, power consumption < 24 W.
- Ordering codes (datasheet): 208595 = 1424 SHDSL 1P NPWR; 501426 = 2P NPWR; 208602 = 4P NPWR; 70538 = 1P USB NPWR; 70539 = 2P USB NPWR.
- PoE: the CPE Matrix says 'Verify variant'. The datasheet describes no PoE.""",
                  "03ds-1424-ekinops.pdf & Enterprise-CPE-Plan-2025.pptx", "OneAccess 1424", "discrepancy_profile"))

    # ---- ONE521 performance conflict ----
    docs.append(D("""[OneAccess ONE521 Maximum Performance & Throughput Specifications]
Device: OneAccess ONE521
- Datasheet: bidirectional routing with services (IMIX409) 250 Mbps; aggregated IPv4 routing 550 Mbps; best-effort routing 1 Gbps; bidirectional IPsec with services (IMIX410) 60 Mbps.
- CPE Matrix: 'Up to 250 Mbps'. Slide 4: uplink 'bandwidth up to 1 Gbps'. Slide 8 flowchart: capped at 500 Mbps behind a service switch (L2 NTU).
- Source Conflict: these figures measure different things (services vs aggregate vs best effort vs service cap). State which one you quote and where it comes from.""",
                  "03ds-one521-ekinops.pdf & Enterprise-CPE-Plan-2025.pptx", "OneAccess ONE521", "discrepancy_profile"))

    # ---- ISR 4200 / 4300 ambiguity ----
    docs.append(D("""[Cisco ISR 4200 / 4300 Brand-Specific Replacement & Ambiguity]
Query Context: brand-specific Cisco router replacement for Cisco ISR 4200 / ISR 4300 series.
- Slide 7: for ISR 4200 / ISR 4300 series where the customer insists on Cisco, the replacement is written as 'Cisco 4231X'.
- Slide 9 (Customer Options, Brand Specific): lists 'Cisco C1121X, ISR 4321X'.
- Slide 5 (Retiring CPEs): lists Cisco ISR 4231 as retiring, and does not list ISR 4321.
- Sheet1 and slide 7: Cisco 891F / 1921 / 1941 -> Cisco 1121X (this does NOT cover ISR 4200 / 4300).
- Inventory: ISR4321X/K9 (NWKEAC072) is 'To be Retired with Alternatives [C8200L]'.
- Conflict: 'Cisco 4231X' is not a model that appears anywhere else; it may be a typo for 4321X, but the sources do not confirm this. Report the conflict and do not choose one.""",
                  "Enterprise-CPE-Plan-2025.pptx & CPE LIST.xlsx", "Cisco ISR 4200 / 4300", "discrepancy_profile"))

    # ---- sizing ----
    docs.append(D("""[OneAccess ONE Series Routing Table & Session Sizing]
1. OneAccess ONE421: 12,500 combined IPv4 + IPv6 routes; 10,000 concurrent NAT sessions.
2. OneAccess ONE521: 25,000 combined IPv4 + IPv6 routes; 50,000 concurrent NAT sessions.
3. OneAccess ONE2501: 50,000 IPv4 routes + 12,500 IPv6 routes; 16 VRFs; 100,000 concurrent NAT / firewall / DPI sessions.""",
                  "03ds-one421-ekinops.pdf & 03ds-one521-ekinops.pdf & 03ds-one2501-ekinops.pdf", "OneAccess Routers", "sizing_profile"))

    # ---- discrepancy registry ----
    docs.append(D("""[Enterprise Documentation Cross-Source Discrepancies & Conflict Audit]
1. OneAccess 1424 line rate: datasheet up to 22 Mbps (short loops) / 10 Mbps (standard loops); slides and Matrix 'up to 2 Mbps'; slide 8 caps it at 30 Mbps behind an ONT bridge. The sources do not explain the difference.
2. OneAccess ONE521 throughput: datasheet 250 Mbps with services / 550 Mbps aggregate / 1 Gbps best effort; Matrix 250 Mbps; slide 4 'up to 1 Gbps'; slide 8 cap 500 Mbps.
3. Cisco ISR 4321X: inventory 'To be Retired with Alternatives [C8200L]'; slide 5 does not list it as retiring; slide 9 keeps it as a Brand Specific option. Throughput: datasheet 50 Mbps default / 100 Mbps with license; slide 8 cap 50 Mbps; slide 3 'up to 100 Mbps'.
4. OneAccess ONE2501: the CPE Matrix says 'Up to 500 Gbps bidirectional'. The datasheet says 600 Mbps / 1 Gbps (1 Gbps is subject to a license) with services and 2 Gbps best effort. The Matrix figure is a unit error.
5. Slide 7 'Cisco 4231X' vs slide 9 'ISR 4321X' vs slide 5 retiring 'ISR 4231' (see the ISR 4200 / 4300 record).
6. Juniper SRX320: it is stocked (4 inventory rows) and named in the deck, but the CPE Matrix and Features sheets have no SRX320 row (only SRX300 and SRX340). Slide 2 labels a row 'Juniper SRX 320' with '1G SFP x 2', the same figures the Features sheet gives for the SRX 300.
7. Huawei S5735-L24P4S-A1: inventory says 4 x 10G SFP+ uplinks; datasheet and Matrix say 4 x GE SFP.
8. Cisco C1121X-8P (NWKEAC084) is listed twice in the inventory with different on-hand counts (Cisco Routers block vs SD WAN Edges block).
9. Incoming stock: the ONTs (1000 each, ETA 30 Nov 2026) and the Catalyst 1300 and Huawei S310 (500 each, 'LC Opening inprogress') show their quantity in the On-Hand column. Receipt is not confirmed.
10. Phones: the inventory marks GXP-1782, GXV-3350 and WP820 'To be Retired', and GXP-2160 'Need to Re-order', while slide 6 lists GXP 2160 among the retiring phones.
11. The inventory sheet is dated 19/10/2026; quote that date when giving stock.""",
                  "Cross-Document Discrepancy Registry", "Discrepancy Audit", "discrepancy_audit"))

    # ---- PoE ----
    docs.append(D("""[Enterprise CPE PoE Capability & Power Budget Disambiguation Profile]
1. OneAccess ONE421: PoE is 'Verify variant' in the CPE Matrix; the datasheet describes no PoE. Do not state that it supports PoE.
2. OneAccess ONE521: PoE is 'Verify variant' in the CPE Matrix; the datasheet describes no PoE.
3. OneAccess ONE2501: PoE is a factory option on versions with 8 LAN ports: up to 120 W, 4 x PoE+ (30 W, 802.3at) and 4 x PoE (15 W, 802.3af); optional external 150 W power supply (54 Vdc, 2.78 A).
4. OneAccess 1424-1P: PoE is 'Verify variant' in the CPE Matrix.
5. Huawei CloudEngine S5735-L: L8P4S-A1 PoE+ 124 W; L24P4S-A1 PoE+ 380 W; L8T4X-A1 no PoE; L24T4X-A1 no PoE; L24P4X-A1 PoE+ 380 W.
6. Cisco Catalyst 1300: C1300-24P-4G 195 W PoE budget; C1300-24FP-4G 370 W.
7. Juniper SRX 300: the CPE Matrix says PoE 'No'. SRX 320 POE LAN PORTS (NWKEAC126) is a dedicated PoE variant that is only issued for Access Point and IP Phone use.""",
                  "03ds-one2501-ekinops.pdf & Copy of CPE Matrix.xlsx & Switch Datasheets", "PoE Capabilities", "poe_profile"))

    # ---- ONT scope ----
    docs.append(D("""[Enterprise ONT Documentation Scope]
- Stock, item codes and ETA for ONTs come from CPE LIST.xlsx: ZTE ZXHN F670L = CPEFTH076S (ETA 30 Nov 2026, blocker: PO issued, import-control licence to be applied for from ZTE); Huawei EG8145V5 = CPEFTH077S (ETA 30 Nov 2026, blocker: pending site approval from TRCSL).
- The sheet shows 1000 for each in the On-Hand column together with the ETA. Whether they are already received or still on order is NOT stated; do not say 'in transit' or 'in stock' as fact.
- No ONT datasheet (Wi-Fi standards, frequencies, antennas, throughput) is in the corpus. If asked, say the provided documentation does not specify it. The Huawei EG8145V5 is an ONT, not a switch.""",
                  "CPE LIST.xlsx", "Enterprise ONTs", "ont_profile"))

    # ---- ONE series authoritative profiles ----
    docs.append(D(f"""[OneAccess ONE421 Authoritative Technical Specification & Sizing Profile]
Device / Model: OneAccess ONE421 (CPE ONEACCESS ONE421, Item Code: CPEDAT081S)
- Alias note: 'one-421', 'one421', 'ONE 421' and '421' all mean this same device.
- LAN: 4 x Gigabit Ethernet (10/100/1000BASE-T) RJ-45 ports. WAN: modular combinations of fibre (SFP), VDSL2 and LTE; optional WiFi.
- Routing: 100 Mbps bidirectional with services (IMIX409); 220 Mbps aggregated IPv4 routing; 800 Mbps best effort. IPsec with services (IMIX410): 30 Mbps.
- Capacity: 12,500 combined IPv4 + IPv6 routes; 10,000 NAT sessions.
- VDSL2 (factory option): 1 pair, profiles 8, 12, 17, 30 and 35b.
- Dimensions: 274 x 61 x 166 mm; weight 0.75 kg (1.65 lb); plastic housing; operating temperature 0 to 45 C.
- Power: external 12 V adapter (2 A or 3 A, region dependent); optional 24/48 Vdc; power consumption < 36 W.
- Voice (ONeSBC option): up to 100 simultaneous centrex calls and up to 60 simultaneous trunking calls (no transcoding).
- PoE: 'Verify variant' in the CPE Matrix; do not claim PoE support.
Stock (from CPE LIST.xlsx):
{stock_lines(by_code, 'CPEDAT081S')}""", "03ds-one421-ekinops.pdf & Copy of CPE Matrix.xlsx", "OneAccess ONE421", "technical_profile",
                  item_code="CPEDAT081S"))

    docs.append(D(f"""[OneAccess ONE521 Authoritative Technical Specification & Sizing Profile]
Device / Model: OneAccess ONE521 (CPE ONEACCESS ONE521, Item Code: CPEDAT082S)
- Routing: 250 Mbps bidirectional with services (IMIX409); 550 Mbps aggregated IPv4 routing; 1 Gbps best effort. IPsec with services (IMIX410): 60 Mbps.
- LAN: 4 x Gigabit Ethernet ports. WAN: 1 fibre / copper configurable SFP/UTP port (1000BASE-X); no 10G uplink in the datasheet.
- Capacity: 25,000 combined IPv4 + IPv6 routes; 50,000 NAT sessions.
- Dimensions and weight depend on the housing:
  * Plastic housing: 274 x 61 x 166 mm, 0.8 kg (1.8 lb)
  * Metal housing: 295 x 44.5 x 166 mm, 1.3 kg (2.9 lb)
- Power: external 12 V adapter (2 A or 3 A, region dependent); optional 24/48 V DC; dying gasp supported; power consumption < 36 W.
- VDSL2 (factory option): 1 pair profiles 8, 12, 17, 30, 35b; 2 pairs profiles 8, 12, 17.
- A 5G version exists (metal housing; separate datasheet).
- Voice (ONeSBC option): up to 100 simultaneous centrex calls and up to 60 simultaneous trunking calls (no transcoding).
- PoE: 'Verify variant' in the CPE Matrix; the datasheet describes no PoE.
Stock (from CPE LIST.xlsx):
{stock_lines(by_code, 'CPEDAT082S')}""", "03ds-one521-ekinops.pdf & Copy of CPE Matrix.xlsx", "OneAccess ONE521", "technical_profile",
                  item_code="CPEDAT082S"))

    docs.append(D(f"""[OneAccess ONE2501 Authoritative Technical Specification & Sizing Profile]
Device / Model: OneAccess ONE2501 (CPE ONEACCESS ONE2501, Item Code: CPEDAT083S)
- LAN: 4 or 8 x Gigabit Ethernet RJ-45 ports; WAN: dual combo Gigabit Ethernet (SFP / UTP); optional LTE and WiFi 11ac wave2.
- Routing with services (IMIX409): 600 Mbps, or 1 Gbps with a license (the datasheet marks 1 Gbps with '*' = subject to a license). Best effort: 2 Gbps bidirectional.
- IPsec with services (IMIX410): 300 Mbps, or 600 Mbps with a license.
- Capacity: 50,000 IPv4 routes + 12,500 IPv6 routes, 16 VRFs, 100,000 NAT / firewall / DPI sessions.
- PoE (factory option, 8-LAN-port versions): up to 120 W, 4 x PoE+ (30 W) and 4 x PoE (15 W); optional external 150 W PoE power supply (54 Vdc, 2.78 A).
- Dimensions 295 x 44.5 x 166 mm; weight of the router 1.3 kg (2.86 lb); fanless. The '0.8 kg' in the datasheet is the weight of the optional 150 W PoE power supply, NOT the router.
- Power: external 12 V - 3 A adapter, power consumption < 36 W.
- Voice (ONeSBC option): up to 100 simultaneous centrex calls and up to 60 simultaneous trunking calls (no transcoding).
Stock (from CPE LIST.xlsx):
{stock_lines(by_code, 'CPEDAT083S')}""", "03ds-one2501-ekinops.pdf & Copy of CPE Matrix.xlsx", "OneAccess ONE2501", "technical_profile",
                  item_code="CPEDAT083S"))

    # ---- Juniper boundary ----
    docs.append(D(f"""[Juniper SRX Documentation Scope & Corpus Boundary Record]
Hardware family: Juniper SRX 300, SRX 320, SRX 340.
1. What the corpus contains:
   - Stock (from CPE LIST.xlsx):
{stock_lines(by_code, 'NWKEAC158S', 'NWKEAC022', 'NWKEAC126', 'NWKEAC022S', 'NWKEAC025')}
   - Role: upgrade option for higher performance (slides 7, 8, 9); the latest monthly rental of the L3 switch applies.
   - Slide 8 caps: SRX320 1 Gbps and SRX340 3 Gbps when delivered over direct fiber; 300 Mbps for a Juniper L3 switch behind a GPON ONT bridge.
   - CPE Matrix: SRX300 PoE = No; firewall up to 1.9 Gbps (SRX300) and 4.7 Gbps (SRX340) as written in the Matrix (no datasheet to confirm them).
2. What the corpus does NOT contain: there is no Juniper datasheet. VPN / IPsec tunnel counts, concurrent sessions, memory, dimensions and weight are not documented.
   If asked for them, say: 'Not stated in the provided documents (no Juniper datasheet is available in the corpus).' Never invent or estimate a figure.""",
                  "Enterprise-CPE-Plan-2025.pptx & Copy of CPE Matrix.xlsx & CPE LIST.xlsx", "Juniper SRX Series", "boundary_profile"))
    print(f"  [+] Created {len(docs)} curated profile / discrepancy records")
    return docs


# ---------------------------------------------------------------------
# 5. PDF datasheets
# ---------------------------------------------------------------------
PDF_DEVICE = {
    "03ds-one421-ekinops": "OneAccess ONE421", "03ds-one521-ekinops": "OneAccess ONE521",
    "03ds-one2501-ekinops": "OneAccess ONE2501", "03ds-1424-ekinops": "OneAccess 1424",
    "data_sheet-c78-732542": "Cisco ISR 4000 family", "nb-06-cat1300-ser-data-sheet-cte-en": "Cisco Catalyst 1300",
    "Huawei CloudEngine S5735-L Series Switches Datasheet": "Huawei CloudEngine S5735-L",
}


def load_pdf_datasheet(pdf_path: Path) -> List[Document]:
    docs = []
    splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=150)
    device = PDF_DEVICE.get(pdf_path.stem, pdf_path.stem)

    def add(page_idx, txt):
        for chunk in splitter.split_text(txt):
            docs.append(Document(
                page_content=f"[{device} Datasheet ({pdf_path.name}) - Page {page_idx + 1}]\n{chunk}",
                metadata={"source": pdf_path.name, "page": page_idx + 1, "doc_type": "datasheet", "device": device}))
    try:
        import pypdf
        for i, page in enumerate(pypdf.PdfReader(str(pdf_path)).pages):
            txt = page.extract_text() or ""
            if txt.strip():
                add(i, txt)
    except Exception:
        try:
            import fitz
            doc = fitz.open(str(pdf_path))
            for i in range(len(doc)):
                txt = doc[i].get_text() or ""
                if txt.strip():
                    add(i, txt)
        except Exception as exc:
            print(f"  [Error] Failed loading {pdf_path.name}: {exc}")
    print(f"  [+] Loaded {len(docs)} text chunks from {pdf_path.name}")
    return docs


# ---------------------------------------------------------------------
# 6. Enterprise-CPE-Plan-2025.pptx
# ---------------------------------------------------------------------
# Slide 8 is a flowchart image; its text boxes lose their links when extracted, so the paths are
# transcribed here from the slide (verified against the rendered slide).
SLIDE8_PATHS = [
    ("Copper (G.SHDSL)", "One Access 1424 NTU [acts as a router]", "BW under 2 Mbps", "IPVPN / BIL / IP Trunk"),
    ("Direct fiber", "Juniper L3 switch SRX320", "performance capped at 1 Gbps", "IPVPN / BIL / IP Trunk"),
    ("Direct fiber", "OneAccess ONE2501 [acts as a router] (to be introduced)", "performance capped at 1 Gbps", "IPVPN / BIL / IP Trunk"),
    ("Direct fiber", "Juniper L3 switch SRX340", "performance capped at 3 Gbps", "IPVPN / BIL / IP Trunk"),
    ("Direct fiber, Service Switch [L2 NTU]", "the service switch itself", "BW under 1 Gbps", "EVPN / EDL"),
    ("Behind the Service Switch [L2 NTU]", "OneAccess ONE421 [acts as a router] (to be introduced)", "performance capped at 100 Mbps", "IPVPN / BIL / IP Trunk"),
    ("Behind the Service Switch [L2 NTU]", "OneAccess ONE521 [acts as a router] (to be introduced)", "performance capped at 500 Mbps", "IPVPN / BIL / IP Trunk"),
    ("Behind the Service Switch [L2 NTU]", "Cisco ISR 4321X [router CPE]", "performance capped at 50 Mbps", "IPVPN / BIL / IP Trunk"),
    ("GPON, ONT as L2 NTU", "the ONT itself", "BW under 100 Mbps", "EVPN / EDL"),
    ("GPON, ONT [as a bridge]", "One Access 1424 NTU [router CPE]", "performance capped at 30 Mbps", "IPVPN / BIL / IP Trunk"),
    ("GPON, ONT [as a bridge]", "Cisco ISR 4321X [router CPE]", "performance capped at 50 Mbps", "IPVPN / BIL / IP Trunk"),
    ("GPON, ONT [as a bridge]", "Juniper L3 switch [router CPE]", "performance capped at 300 Mbps", "IPVPN / BIL / IP Trunk"),
    ("GPON, ONT [as a bridge]", "OneAccess ONE421 [acts as a router] (to be introduced)", "performance capped at 100 Mbps", "IPVPN / BIL / IP Trunk"),
]

SLIDE_CONTEXT = {
    5: "what is retiring according to the plan deck, retiring CPEs on slide 5.",
    6: "what is retiring in the presentation, retiring phones, IADs and Wi-Fi access points on slide 6.",
    9: "customer options, default vs brand specific vs upgraded CPE options and rental impact.",
}


def shape_lines(shape) -> List[str]:
    out = []
    if shape.shape_type == 6:                              # group shape
        for sub in shape.shapes:
            out += shape_lines(sub)
    if getattr(shape, "has_table", False) and shape.has_table:
        for row in shape.table.rows:
            cells = [c.text.replace("\n", " / ").strip() for c in row.cells]
            if any(cells):
                out.append(" | ".join(cells))
    elif getattr(shape, "has_text_frame", False) and shape.has_text_frame:
        for para in shape.text_frame.paragraphs:
            t = para.text.strip()
            if t:
                out.append(t)
    return out


def load_cpe_policy_presentation(pptx_path: Path) -> List[Document]:
    from pptx import Presentation

    # Split slide 8 into one chunk per access path: group SLIDE8_PATHS by first field
    from collections import OrderedDict
    path_groups: dict[str, list[tuple[str, str, str, str]]] = OrderedDict()
    for row in SLIDE8_PATHS:
        path = row[0]
        if path not in path_groups:
            path_groups[path] = []
        path_groups[path].append(row)

    all_cpes = {row[1] for row in SLIDE8_PATHS}
    docs = []
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
        docs.append(Document(
            page_content="\n".join(lines),
            metadata={"source": pptx_path.name, "slide": 8, "path": path, "doc_type": "selection_flowchart"}
        ))

    docs.append(Document(
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
        metadata={"source": pptx_path.name, "slide": 7, "doc_type": "policy_replacement"}))

    prs = Presentation(str(pptx_path))
    for i, slide in enumerate(prs.slides, 1):
        if i == 8:
            continue                                   # replaced by the transcribed path table above
        lines = []
        for shape in slide.shapes:
            lines += shape_lines(shape)
        if lines:
            ctx = f"Query Context: {SLIDE_CONTEXT[i]}\n" if i in SLIDE_CONTEXT else ""
            docs.append(Document(page_content=f"[{pptx_path.stem} - Slide {i}]\n{ctx}" + "\n".join(lines),
                                 metadata={"source": pptx_path.name, "slide": i, "doc_type": "presentation"}))
    print(f"  [+] Loaded {len(docs)} slide & policy chunks from {pptx_path.name}")
    return docs


# ---------------------------------------------------------------------
# 7. Ingest-time validation (the guard)
# ---------------------------------------------------------------------
def validate_docs(all_docs: List[Document], by_code) -> List[str]:
    errors = []
    for d in all_docs:
        text = d.page_content
        for code in set(CODE_RE.findall(text)):
            if code not in by_code:
                errors.append(f"unknown item code {code} in: {text[:70]!r}")
        ic = d.metadata.get("item_code")
        dev = d.metadata.get("device", "")
        if ic and dev and ic in by_code and d.metadata.get("doc_type") in ("technical_profile", "ont_profile", "multi_hop_replacement"):
            descs = " ".join(r.desc + " " + r.vendor for r in by_code[ic])
            if not any(len(t) >= 4 and t in norm(descs) for t in [norm(w) for w in re.split(r"[\s/]+", dev)]):
                errors.append(f"item_code {ic} ({descs[:40]}) does not match device {dev!r}")
        if ("CPEDAT074" in text or "CPEDAT075" in text) and re.search(r"\bONT\b|F670L|EG8145V5", text):
            errors.append(f"phone code CPEDAT074/075 appears next to an ONT in: {text[:70]!r}")
        if "#N/A" in text:
            errors.append(f"#N/A leaked into: {text[:70]!r}")
    return errors


# ---------------------------------------------------------------------
# 8. Main pipeline
# ---------------------------------------------------------------------
def build_all_docs(data_dir: Path) -> List[Document]:
    all_docs: List[Document] = []
    inv_path = data_dir / "CPE LIST.xlsx"
    rows, as_of, by_code = parse_inventory(inv_path)
    all_docs += build_inventory_docs(rows, as_of, by_code)

    matrix_path = data_dir / "Copy of CPE Matrix.xlsx"
    if matrix_path.exists():
        all_docs += load_cpe_matrix(matrix_path)
    all_docs += load_cross_linked_replacements(by_code)
    all_docs += load_curated_records(by_code)
    for pdf in sorted(data_dir.glob("*.pdf")):
        all_docs += load_pdf_datasheet(pdf)
    pptx_path = data_dir / "Enterprise-CPE-Plan-2025.pptx"
    if pptx_path.exists():
        all_docs += load_cpe_policy_presentation(pptx_path)

    errors = validate_docs(all_docs, by_code)
    if errors:
        print(f"\n[VALIDATION FAILED] {len(errors)} problem(s):")
        for e in errors[:40]:
            print("  -", e)
        raise SystemExit(2)
    print("  [+] Ingest-time validation passed (item codes, code/device match, no ONT/phone mix-up, no #N/A)")

    issues = [r for r in rows if r.duplicate or r.on_order_suspected or None in (r.on_hand, r.repaired, r.regions)]
    if issues:
        print("\n[Data issues to resolve with the stores team]")
        for r in issues:
            why = "duplicate listing" if r.duplicate else "possible on-order stock" if r.on_order_suspected else "blank / #N/A quantity"
            print(f"  - {r.code} {r.desc[:45]}: {why}")
    return all_docs


def run_cpe_ingestion(dry_run: bool = False, in_place: bool = False, data_dir: Path = CPE_DATA_DIR, dump: Optional[Path] = None):
    print("=" * 70)
    print("RE-INDEXING CPE KNOWLEDGE BASE (V4 - generated from the source files)")
    print(f"Source directory: {data_dir}")
    print("=" * 70)
    all_docs = build_all_docs(data_dir)
    print(f"\n[Total Ingestable Documents]: {len(all_docs)}")

    if dump:
        with open(dump, "w", encoding="utf-8") as f:
            for d in all_docs:
                f.write(json.dumps({"text": d.page_content, "metadata": d.metadata}, ensure_ascii=False) + "\n")
        print(f"[+] Wrote dump to {dump}")
    if dry_run:
        print("[dry-run] Qdrant not touched.")
        return all_docs

    from qdrant_client import QdrantClient
    from langchain_qdrant import QdrantVectorStore, FastEmbedSparse, RetrievalMode
    from core.config import settings
    from core.llm import get_ingestion_embedding_model

    qdrant_url = getattr(settings, "QDRANT_URL", "http://slt_qdrant:6333")
    client, connected_url = None, None
    for candidate in [qdrant_url, "http://slt_qdrant:6333", "http://localhost:6335", "http://localhost:6333"]:
        try:
            c = QdrantClient(url=candidate, timeout=5, check_compatibility=False)
            c.get_collections()
            client, connected_url = c, candidate
            print(f"[+] Connected to Qdrant at {candidate}")
            break
        except Exception:
            continue
    if not client:
        raise RuntimeError("Failed to connect to Qdrant on any known candidate URL.")

    embedding_model = get_ingestion_embedding_model()
    sparse_embeddings = FastEmbedSparse(model_name="Qdrant/bm25")
    start = time.time()

    if in_place:
        try:
            client.delete_collection(COLLECTION_NAME)
            print(f"[+] Dropped old collection '{COLLECTION_NAME}'.")
        except Exception:
            pass
        target = COLLECTION_NAME
    else:
        target = f"{COLLECTION_NAME}_{int(time.time())}"

    QdrantVectorStore.from_documents(
        documents=all_docs, embedding=embedding_model, sparse_embedding=sparse_embeddings,
        url=connected_url, collection_name=target, retrieval_mode=RetrievalMode.HYBRID,
        check_compatibility=False, timeout=60)
    count = client.count(target, exact=True).count
    if count < len(all_docs):
        raise RuntimeError(f"Indexed {count} points but expected {len(all_docs)}; leaving the live index untouched.")
    print(f"[+] Indexed {count} documents into '{target}' in {time.time() - start:.1f}s")

    if not in_place:
        from qdrant_client.models import CreateAlias, CreateAliasOperation, DeleteAlias, DeleteAliasOperation
        existing = {c.name for c in client.get_collections().collections}
        aliases = {a.alias_name for a in client.get_aliases().aliases}
        ops = []
        if COLLECTION_NAME in aliases:
            ops.append(DeleteAliasOperation(delete_alias=DeleteAlias(alias_name=COLLECTION_NAME)))
        elif COLLECTION_NAME in existing:
            client.delete_collection(COLLECTION_NAME)     # first V4 run only: replace the old real collection
            print(f"[+] Replaced the old real collection '{COLLECTION_NAME}' with an alias (first run).")
        ops.append(CreateAliasOperation(create_alias=CreateAlias(collection_name=target, alias_name=COLLECTION_NAME)))
        client.update_collection_aliases(change_aliases_operations=ops)
        print(f"[+] Alias '{COLLECTION_NAME}' now points to '{target}'.")
        old = sorted(n for n in existing if n.startswith(COLLECTION_NAME + "_") and n != target)
        for name in old[:-1]:                                   # keep the previous build as a rollback
            client.delete_collection(name)
            print(f"[+] Removed old build '{name}'.")
    print("=" * 70)
    return all_docs


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="build and validate only; do not touch Qdrant")
    ap.add_argument("--in-place", action="store_true", help="drop and rebuild the collection (old behaviour)")
    ap.add_argument("--data-dir", type=Path, default=CPE_DATA_DIR)
    ap.add_argument("--dump", type=Path, default=None, help="write the generated documents to a .jsonl file")
    a = ap.parse_args()
    run_cpe_ingestion(dry_run=a.dry_run, in_place=a.in_place, data_dir=a.data_dir, dump=a.dump)
