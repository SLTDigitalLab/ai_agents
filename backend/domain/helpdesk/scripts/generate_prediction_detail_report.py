"""
PDF report generator for run_prediction_detail.py's output: for every
tested ticket, shows the top-k categories the vector search retrieved and
the final category the LLM chose from them, alongside the ground-truth
category and a match/no-match indicator.

Complements domain/helpdesk/scripts/generate_report.py (which reports aggregate
accuracy numbers only) with the underlying per-ticket evidence.

Usage (from /app inside the backend container):
    python domain/helpdesk/scripts/generate_prediction_detail_report.py \
        --input domain/helpdesk/data/prediction_detail_200.xlsx \
        --output domain/helpdesk/data/Helpdesk_Prediction_Detail_Report.pdf \
        [--top-k 5] [--limit N]
"""

import argparse
import math
import re

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
from fpdf import FPDF

NAVY = (26, 41, 61)
TEAL = (0, 124, 148)
AMBER = (184, 114, 11)
GREEN = (30, 120, 60)
RED = (176, 40, 40)
GREY = (91, 107, 124)
LIGHT_GREY = (240, 243, 246)
CARD_BORDER = (210, 218, 226)


def _clean(text) -> str:
    """FPDF's core Helvetica font is Latin-1 only; real ticket text can
    contain characters outside that range (curly quotes, other scripts,
    emoji). Replace anything non-encodable rather than letting fpdf raise."""
    if text is None or (isinstance(text, float) and math.isnan(text)):
        return ""
    text = str(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text.encode("latin-1", "replace").decode("latin-1")


def make_summary_chart(path: str, accuracy: float, recall_at_k: float, k: int):
    fig, ax = plt.subplots(figsize=(6.4, 3.2), dpi=200)
    labels = [f"Recall@{k}\n(true category retrieved)", "Final accuracy\n(LLM's chosen pick)"]
    values = [recall_at_k, accuracy]
    colors = ["#0E7C86", "#B8720B"]
    bars = ax.bar(labels, values, color=colors, width=0.5)
    for bar, v in zip(bars, values):
        ax.text(bar.get_x() + bar.get_width() / 2, v + 1.5, f"{v:.1f}%", ha="center", fontsize=10, color="#1a293d")
    ax.set_ylim(0, 100)
    ax.set_ylabel("Percent of tickets", fontsize=9)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.tick_params(labelsize=9)
    ax.set_axisbelow(True)
    ax.yaxis.grid(True, color="#e1e0d9", linewidth=0.6)
    fig.tight_layout()
    fig.savefig(path, facecolor="white")
    plt.close(fig)


class Report(FPDF):
    def header(self):
        if self.page_no() == 1:
            return
        self.set_font("Helvetica", "", 8)
        self.set_text_color(*GREY)
        self.cell(0, 8, "Helpdesk Category Prediction - Detailed Test Run", align="L")
        self.set_x(-40)
        self.cell(30, 8, f"Page {self.page_no()}", align="R")
        self.ln(12)

    def footer(self):
        pass

    def h1(self, text):
        self.set_font("Helvetica", "B", 15)
        self.set_text_color(*NAVY)
        self.cell(0, 10, text, new_x="LMARGIN", new_y="NEXT")
        self.set_draw_color(*TEAL)
        self.set_line_width(0.6)
        y = self.get_y() + 1
        self.line(self.l_margin, y, self.l_margin + 22, y)
        self.ln(6)

    def h2(self, text):
        self.set_font("Helvetica", "B", 11.5)
        self.set_text_color(*TEAL)
        self.ln(2)
        self.cell(0, 7, text, new_x="LMARGIN", new_y="NEXT")
        self.ln(1)

    def body(self, text, size=10):
        self.set_font("Helvetica", "", size)
        self.set_text_color(*NAVY)
        self.set_x(self.l_margin)
        self.multi_cell(0, 5.4, text, new_x="LMARGIN", new_y="NEXT")
        self.ln(1)

    def bullet(self, text, size=10):
        self.set_font("Helvetica", "", size)
        x0 = self.l_margin
        self.set_x(x0)
        self.set_text_color(*TEAL)
        self.cell(4, 5.4, chr(149), new_x="RIGHT", new_y="TOP")
        self.set_text_color(*NAVY)
        self.set_x(x0 + 4)
        self.multi_cell(self.w - self.r_margin - (x0 + 4), 5.4, text, new_x="LMARGIN", new_y="NEXT")
        self.set_x(self.l_margin)

    def ticket_card(self, n: int, row: pd.Series, k: int):
        content_w = self.w - self.l_margin - self.r_margin
        message = _clean(row.get("message", ""))
        if len(message) > 320:
            message = message[:317] + "..."
        true_cat = _clean(row.get("true_category", ""))
        true_sub = _clean(row.get("true_sub_category", ""))
        pred_cat = _clean(row.get("predicted_category", ""))
        pred_sub = _clean(row.get("predicted_sub_category", ""))
        correct = bool(row.get("correct", False))

        candidates = []
        for i in range(1, k + 1):
            c_cat = _clean(row.get(f"cand{i}_category", ""))
            if not c_cat:
                continue
            c_sub = _clean(row.get(f"cand{i}_subcategory", ""))
            c_score = row.get(f"cand{i}_score", None)
            score_txt = f"{c_score:.3f}" if isinstance(c_score, (int, float)) and not math.isnan(c_score) else "-"
            candidates.append((i, c_cat, c_sub, score_txt))

        # Estimate the card height up front so it isn't split across pages.
        est_h = 8 + 5 * (1 + math.ceil(len(message) / 108)) + 5.6 * len(candidates) + 14
        if self.get_y() + est_h > self.page_break_trigger:
            self.add_page()

        y0 = self.get_y()
        self.set_font("Helvetica", "B", 10)
        self.set_text_color(*NAVY)
        self.cell(0, 6, f"Ticket #{n}", new_x="LMARGIN", new_y="NEXT")

        self.set_font("Helvetica", "I", 9)
        self.set_text_color(*GREY)
        self.multi_cell(content_w, 4.6, f'"{message}"', new_x="LMARGIN", new_y="NEXT")
        self.ln(0.5)

        self.set_font("Helvetica", "B", 8.6)
        self.set_text_color(*TEAL)
        self.cell(0, 5, f"Top {len(candidates)} categories retrieved from vector search (ranked, similarity score):", new_x="LMARGIN", new_y="NEXT")
        self.set_font("Helvetica", "", 8.8)
        self.set_text_color(*NAVY)
        for i, c_cat, c_sub, score_txt in candidates:
            line = f"  {i}. {c_cat} > {c_sub}  (score {score_txt})"
            self.set_x(self.l_margin)
            self.multi_cell(content_w, 4.6, line, new_x="LMARGIN", new_y="NEXT")

        self.ln(0.5)
        self.set_font("Helvetica", "B", 9)
        self.set_text_color(*AMBER)
        self.set_x(self.l_margin)
        self.multi_cell(content_w, 5, f"LLM final pick: {pred_cat} > {pred_sub}", new_x="LMARGIN", new_y="NEXT")

        self.set_font("Helvetica", "", 8.8)
        self.set_text_color(*GREY)
        self.set_x(self.l_margin)
        self.cell(0, 4.8, f"Ground truth: {true_cat} > {true_sub}", new_x="LMARGIN", new_y="NEXT")

        self.set_font("Helvetica", "B", 8.8)
        self.set_text_color(*GREEN if correct else RED)
        self.set_x(self.l_margin)
        self.cell(0, 4.8, "MATCH" if correct else "NO MATCH", new_x="LMARGIN", new_y="NEXT")

        y1 = self.get_y() + 2.5
        self.set_draw_color(*CARD_BORDER)
        self.set_line_width(0.25)
        self.rect(self.l_margin - 2, y0 - 2, content_w + 4, y1 - y0 + 2)
        self.set_y(y1 + 3)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", default="domain/helpdesk/data/prediction_detail_200.xlsx")
    parser.add_argument("--output", default="domain/helpdesk/data/Helpdesk_Prediction_Detail_Report.pdf")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--limit", type=int, default=None, help="Only render the first N tickets (debug/preview)")
    parser.add_argument("--dataset-label", default="representative_eval_200.xlsx (200 real, unfiltered tickets)")
    args = parser.parse_args()

    df = pd.read_excel(args.input)
    if args.limit:
        df = df.head(args.limit)

    k = args.top_k
    n = len(df)
    accuracy = df["correct"].mean() * 100
    recall_at_k = df["true_category_in_top_k"].mean() * 100 if "true_category_in_top_k" in df.columns else float("nan")

    per_category = (
        df.assign(true_category=df["true_category"].astype(str).str.strip())
        .groupby("true_category")
        .agg(n=("correct", "size"), correct=("correct", "sum"))
    )
    per_category["accuracy_pct"] = (per_category["correct"] / per_category["n"] * 100).round(1)
    per_category = per_category.sort_values("n", ascending=False)

    chart_path = "/tmp/prediction_detail_chart.png"
    make_summary_chart(chart_path, accuracy, recall_at_k, k)

    pdf = Report()
    pdf.set_auto_page_break(auto=True, margin=18)
    pdf.set_margins(18, 15, 18)

    # ---------- Title page ----------
    pdf.add_page()
    pdf.set_y(65)
    pdf.set_font("Helvetica", "B", 21)
    pdf.set_text_color(*NAVY)
    pdf.multi_cell(0, 11, "Helpdesk Category Prediction Test", align="C")
    pdf.set_font("Helvetica", "", 14)
    pdf.set_text_color(*TEAL)
    pdf.cell(0, 10, "Per-Ticket Detail: Vector Retrieval vs. LLM Decision", align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(6)
    pdf.set_draw_color(*TEAL)
    pdf.set_line_width(0.8)
    pdf.line(pdf.w / 2 - 20, pdf.get_y(), pdf.w / 2 + 20, pdf.get_y())
    pdf.ln(12)
    pdf.set_font("Helvetica", "", 10.5)
    pdf.set_text_color(*GREY)
    pdf.multi_cell(
        0, 6,
        f"SLT Mobitel AI Help Desk - support-ticket category classifier\n"
        f"Test set: {_clean(args.dataset_label)}\n"
        f"Tickets evaluated: {n}  |  Candidates shown to LLM per ticket: top {k}",
        align="C",
    )

    # ---------- Summary ----------
    pdf.add_page()
    pdf.h1("Summary")
    pdf.body(
        f"For each of the {n} tickets in this run, the category-knowledge-base vector "
        f"search retrieved its top-{k} most similar category candidates, and the LLM "
        f"picked one final category from just those candidates. This report lists both "
        f"the full candidate set and the LLM's final pick for every ticket, alongside "
        f"the human-confirmed ground-truth category."
    )
    pdf.image(chart_path, x=pdf.l_margin + 15, w=pdf.w - pdf.l_margin - pdf.r_margin - 30)
    pdf.ln(3)
    pdf.set_font("Helvetica", "", 8.3)
    pdf.set_text_color(*GREY)
    pdf.multi_cell(
        0, 4.3,
        f"Recall@{k}: the true category appeared somewhere in the top-{k} candidates "
        f"the vector search retrieved (an upper bound on what the LLM could possibly "
        f"pick correctly). Final accuracy: the LLM's actual final choice matched the "
        f"true category.",
    )
    pdf.ln(3)
    pdf.h2("Headline numbers")
    pdf.bullet(f"Tickets evaluated: {n}")
    pdf.bullet(f"Recall@{k} (true category retrieved by vector search): {recall_at_k:.1f}%")
    pdf.bullet(f"Final accuracy (LLM's chosen category matched ground truth): {accuracy:.1f}% ({int(df['correct'].sum())}/{n})")
    gap = recall_at_k - accuracy
    pdf.bullet(
        f"LLM selection gap: {gap:.1f} points - cases where the true category WAS "
        f"retrieved but the LLM picked a different candidate anyway."
    )

    pdf.h2("Accuracy by true category")
    pdf.set_font("Helvetica", "B", 8.6)
    pdf.set_text_color(*NAVY)
    pdf.set_fill_color(*LIGHT_GREY)
    col_w = [95, 20, 22, 30]
    headers = ["True category", "n", "Correct", "Accuracy"]
    for w, htext in zip(col_w, headers):
        pdf.cell(w, 6, htext, border=0, fill=True)
    pdf.ln(6)
    pdf.set_font("Helvetica", "", 8.4)
    for cat, r in per_category.iterrows():
        if pdf.get_y() + 5 > pdf.page_break_trigger:
            pdf.add_page()
        pdf.cell(col_w[0], 5, _clean(cat)[:58], border=0)
        pdf.cell(col_w[1], 5, str(int(r["n"])), border=0)
        pdf.cell(col_w[2], 5, str(int(r["correct"])), border=0)
        pdf.cell(col_w[3], 5, f"{r['accuracy_pct']:.1f}%", border=0)
        pdf.ln(5)

    # ---------- Per-ticket detail ----------
    pdf.add_page()
    pdf.h1("Per-Ticket Detail")
    pdf.body(
        "Every ticket below shows: the customer message, the ranked list of categories "
        "the vector search retrieved (most similar first, with similarity score), the "
        "single category the LLM ultimately chose from that list, and the ground-truth "
        "category for comparison.",
        size=9.3,
    )
    pdf.ln(1)

    for i, (_, row) in enumerate(df.iterrows(), start=1):
        pdf.ticket_card(i, row, k)

    pdf.output(args.output)
    print(f"Wrote report to {args.output}  ({n} tickets, accuracy {accuracy:.1f}%, recall@{k} {recall_at_k:.1f}%)")


if __name__ == "__main__":
    main()
