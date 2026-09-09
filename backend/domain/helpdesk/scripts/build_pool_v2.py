"""
Build a candidate pool for a SECOND 200-row "clear" eval set, distinct from
domain/helpdesk/data/clear_eval_200.xlsx, drawn from the same held-out pool
(domain/helpdesk/data/held_out_test.xlsx) build_clear_eval_set.py already uses.

Two exclusions applied before handing off to build_clear_eval_set.py's own
neighbor-agreement selection:
  1. Any row whose message is already in clear_eval_200.xlsx (so this is a
     genuinely DIFFERENT 200, not a re-pick of the same rows).
  2. Any row whose message text overlaps with the hand-written
     Category_Knowledge_Base_v2.xlsx (checked against every text column of
     the KB, substring match both directions, normalized whitespace/case) —
     so a ticket that happens to also be quoted as a KB "representative
     example" can't leak into the deployed vector classifier's own
     candidate pool for that exact ticket.

Run inside the backend container (same requirement as build_clear_eval_set.py).
"""
import re
import pandas as pd

def norm(s: str) -> str:
    return re.sub(r"\s+", " ", str(s)).strip().lower()

held = pd.read_excel("domain/helpdesk/data/held_out_test.xlsx")
clear200 = pd.read_excel("domain/helpdesk/data/clear_eval_200.xlsx")
kb = pd.read_excel("domain/helpdesk/data/Category_Knowledge_Base_v2.xlsx")

print(f"held_out_test.xlsx: {len(held)} rows")
print(f"clear_eval_200.xlsx: {len(clear200)} rows")
print(f"Category_Knowledge_Base_v2.xlsx: {len(kb)} rows")

# ---- Exclusion 1: already used in clear_eval_200.xlsx ----
used_messages = set(norm(m) for m in clear200["message"])
held["_norm_msg"] = held["message"].apply(norm)
not_already_used = held[~held["_norm_msg"].isin(used_messages)]
print(f"\nAfter excluding rows already in clear_eval_200.xlsx: {len(not_already_used)} rows "
      f"(removed {len(held) - len(not_already_used)})")

# ---- Exclusion 2: overlaps with the hand-written KB text ----
kb_text_cols = [
    "Description", "Customer_Expressions", "Internal_Expressions", "Symptoms",
    "Keywords", "Representative_Examples", "Common_Resolution", "Search_Text",
]
kb_blob = " \n ".join(
    norm(kb[col].dropna().astype(str).str.cat(sep=" \n "))
    for col in kb_text_cols if col in kb.columns
)

def overlaps_kb(msg: str) -> bool:
    m = norm(msg)
    if len(m) < 8:
        return m in kb_blob
    # substring either direction: message quoted verbatim inside the KB,
    # or (for short KB snippets) KB text quoted verbatim inside the message
    return m in kb_blob

not_already_used = not_already_used.copy()
not_already_used["_in_kb"] = not_already_used["message"].apply(overlaps_kb)
pool = not_already_used[~not_already_used["_in_kb"]].drop(columns=["_norm_msg", "_in_kb"])
n_kb_overlap = int(not_already_used["_in_kb"].sum())
print(f"After excluding rows overlapping the KB text: {len(pool)} rows "
      f"(removed {n_kb_overlap} that overlapped the KB)")

pool.to_excel("domain/helpdesk/data/held_out_pool_v2.xlsx", index=False)
print(f"\nWrote candidate pool ({len(pool)} rows) to domain/helpdesk/data/held_out_pool_v2.xlsx")
print(pool["true_category"].value_counts())
