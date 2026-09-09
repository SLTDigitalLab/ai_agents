"""
Updated generator for the helpdesk category-prediction accuracy report
(board/handover PDF). Supersedes generate_report.py with the current
deployed pipeline (hybrid retrieval + hierarchical classification +
confidence-gated clarification), reflecting the 2026-08-09/11 work.

Usage (from /app inside the backend container):
    python domain/helpdesk/scripts/generate_report_v2.py
"""

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from fpdf import FPDF

NAVY = (26, 41, 61)
TEAL = (0, 124, 148)
AMBER = (184, 114, 11)
GREEN = (30, 120, 70)
GREY = (91, 107, 124)
LIGHT_GREY = (225, 231, 237)


def make_chart(path: str):
    methods = ["Prompt\n(original)", "Vector/KB\n(prev. deployed)", "Real-ticket\nexamples", "Hybrid pipeline\n(now deployed)"]
    representative = [35.0, 33.0, 47.5, 44.5]
    highlight = [False, False, False, True]

    fig, ax = plt.subplots(figsize=(7.4, 3.8), dpi=200)
    x = range(len(methods))
    colors = ["#8a97a6" if not h else "#0E7C86" for h in highlight]

    bars = ax.bar(x, representative, width=0.5, color=colors)
    for bar, v in zip(bars, representative):
        ax.text(bar.get_x() + bar.get_width() / 2, v + 1.3, f"{v:.1f}%", ha="center", fontsize=9, color="#1a293d", fontweight="bold")

    ax.set_xticks(list(x))
    ax.set_xticklabels(methods, fontsize=8.8)
    ax.set_ylabel("Main-category accuracy (%)", fontsize=9)
    ax.set_ylim(0, 60)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.tick_params(labelsize=8)
    ax.set_axisbelow(True)
    ax.yaxis.grid(True, color="#e1e0d9", linewidth=0.6)
    ax.text(3, 44.5 + 5.5, "currently\nlive", ha="center", fontsize=7.8, color="#0E7C86", fontweight="bold")
    fig.tight_layout()
    fig.savefig(path, facecolor="white")
    plt.close(fig)


def make_timeline_chart(path: str):
    stages = ["Baseline\n(deployed)", "+ Hybrid retrieval\n+ hierarchical\nclassification (n=1)", "+ 3x self-consistency\n(confidence signal, now live)"]
    values = [33.0, 42.5, 44.5]

    fig, ax = plt.subplots(figsize=(7.4, 3.2), dpi=200)
    ax.plot(stages, values, marker="o", color="#0E7C86", linewidth=2.2, markersize=7)
    for i, v in enumerate(values):
        ax.text(i, v + 1.5, f"{v:.1f}%", ha="center", fontsize=9.5, color="#1a293d", fontweight="bold")
    ax.set_ylabel("Main-category accuracy (%)", fontsize=9)
    ax.set_ylim(25, 55)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.tick_params(labelsize=8.3)
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
        self.cell(0, 8, "Helpdesk Category Prediction - Accuracy Report (Updated)", align="L")
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
        self.set_text_color(*NAVY)
        x0 = self.l_margin
        self.set_x(x0)
        self.set_text_color(*TEAL)
        self.cell(4, 5.4, chr(149), new_x="RIGHT", new_y="TOP")
        self.set_text_color(*NAVY)
        self.set_x(x0 + 4)
        self.multi_cell(self.w - self.r_margin - (x0 + 4), 5.4, text, new_x="LMARGIN", new_y="NEXT")
        self.set_x(self.l_margin)

    def callout(self, label, text, color=TEAL):
        self.set_fill_color(*LIGHT_GREY)
        y0 = self.get_y()
        self.set_font("Helvetica", "B", 9)
        self.set_text_color(*color)
        self.set_xy(self.l_margin + 4, y0 + 3)
        self.cell(0, 5, label)
        self.set_font("Helvetica", "", 9.5)
        self.set_text_color(*NAVY)
        self.set_xy(self.l_margin + 4, y0 + 8)
        self.multi_cell(self.w - self.l_margin - self.r_margin - 8, 5, text, new_x="LMARGIN", new_y="NEXT")
        y1 = self.get_y() + 3
        self.set_fill_color(*LIGHT_GREY)
        self.rect(self.l_margin, y0, 1.2, y1 - y0, style="F")
        self.set_x(self.l_margin)
        self.set_y(y1 + 3)

    def stat_row(self, items):
        """items: list of (value_str, label_str)"""
        n = len(items)
        col_w = (self.w - self.l_margin - self.r_margin) / n
        y0 = self.get_y()
        for i, (val, label) in enumerate(items):
            x = self.l_margin + i * col_w
            self.set_xy(x, y0)
            self.set_font("Helvetica", "B", 17)
            self.set_text_color(*TEAL)
            self.cell(col_w, 9, val, align="C", new_x="LMARGIN", new_y="NEXT")
            self.set_xy(x, y0 + 9)
            self.set_font("Helvetica", "", 8.3)
            self.set_text_color(*GREY)
            self.multi_cell(col_w, 4, label, align="C")
        self.set_y(y0 + 24)


def main():
    chart_path = "/tmp/accuracy_chart_v2.png"
    timeline_path = "/tmp/accuracy_timeline_v2.png"
    make_chart(chart_path)
    make_timeline_chart(timeline_path)

    pdf = Report()
    pdf.set_auto_page_break(auto=True, margin=18)
    pdf.set_margins(18, 15, 18)

    # ---------- Title page ----------
    pdf.add_page()
    pdf.set_y(60)
    pdf.set_font("Helvetica", "B", 22)
    pdf.set_text_color(*NAVY)
    pdf.multi_cell(0, 11, "Helpdesk Category Prediction", align="C")
    pdf.set_font("Helvetica", "", 15)
    pdf.set_text_color(*TEAL)
    pdf.cell(0, 10, "Accuracy Report - Board Update", align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(8)
    pdf.set_draw_color(*TEAL)
    pdf.set_line_width(0.8)
    pdf.line(pdf.w / 2 - 20, pdf.get_y(), pdf.w / 2 + 20, pdf.get_y())
    pdf.ln(14)
    pdf.set_font("Helvetica", "", 10.5)
    pdf.set_text_color(*GREY)
    pdf.multi_cell(
        0, 6,
        "SLT Mobitel AI Help Desk - support-ticket category classifier\n"
        "Status as of 12 August 2026 - prepared for board review",
        align="C",
    )
    pdf.ln(14)
    pdf.stat_row([
        ("33.0% -> 44.5%", "Main-category accuracy,\nbaseline vs. deployed"),
        ("17.5% -> 25.5%", "Strict accuracy - BOTH main\n& sub-category correct"),
        ("3", "Production bugs found\n& fixed via full E2E testing"),
    ])
    pdf.set_font("Helvetica", "I", 8)
    pdf.set_text_color(*GREY)
    pdf.set_x(pdf.l_margin)
    pdf.multi_cell(0, 4, "All accuracy figures measured on a 200-ticket sample of real, unfiltered traffic. See \"How accuracy is measured\" on page 3 for exact definitions.", align="C")

    # ---------- Executive summary ----------
    pdf.add_page()
    pdf.h1("Executive Summary")
    pdf.body(
        "The AI help desk's automatic ticket-category prediction has been rebuilt and "
        "redeployed since the last review. Main-category accuracy on real traffic is up "
        "from 33.0% to 44.5% (a measured +11.5 point absolute gain), the system now "
        "recognises when it is unsure and asks the customer a clarifying question "
        "instead of guessing, and the full conversation pipeline has been hardened "
        "through real end-to-end testing."
    )
    pdf.callout(
        "HOW ACCURACY IS MEASURED",
        "\"Accuracy\" throughout this report means the MAIN category alone (e.g. "
        "\"Billing Related\") is correct, unless stated otherwise. Every ticket also "
        "gets a finer sub-category (e.g. \"Bills not received\"), which is harder to "
        "get right even when the main category is correct. The strict measure - BOTH "
        "main AND sub-category correct - is 25.5% for the deployed system, up from "
        "17.5% for the previous baseline. Both views are reported throughout so the "
        "numbers can't be misread as stronger than they are.",
    )
    pdf.callout(
        "KEY FINDING (unchanged, re-confirmed)",
        "The remaining gap to a very high accuracy number is a DATA problem, not a MODEL "
        "problem. About 70% of real ticket volume falls into three categories (SOA, "
        "CRM OM - After Submit Issues, Clarity OSS - Order Issues) that describe the SAME "
        "underlying event, distinguished only by which internal backend system currently "
        "owns it - a fact never present in the ticket text. This has now been verified "
        "across four independent evaluation runs.",
        color=(int(AMBER[0]), int(AMBER[1]), int(AMBER[2])),
    )
    pdf.h2("What's new since the last report")
    pdf.bullet("Replaced the single-pass retrieval classifier with a hybrid pipeline that merges the knowledge base with ~13,000 real historical tickets and classifies hierarchically (main category, then sub-category).")
    pdf.bullet("Deployed live, raising measured main-category accuracy on real traffic from 33.0% to 44.5% (strict both-correct accuracy: 17.5% to 25.5%).")
    pdf.bullet("Added a confidence-gated clarification step: when the model is unsure and the case isn't part of the known-ambiguous cluster, it now asks the customer one targeted question instead of guessing.")
    pdf.bullet("Added an upfront gate for too-short messages, so a one-word ticket gets a clarifying question before any classification is attempted.")
    pdf.bullet("Ran full end-to-end tests against the live chat system (not just isolated function tests) and fixed 3 bugs this surfaced - see Reliability section.")

    # ---------- Results ----------
    pdf.add_page()
    pdf.h1("Results: Accuracy by Method")
    pdf.image(chart_path, x=pdf.l_margin, w=pdf.w - pdf.l_margin - pdf.r_margin)
    pdf.ln(2)
    pdf.set_font("Helvetica", "", 8.3)
    pdf.set_text_color(*GREY)
    pdf.multi_cell(
        0, 4.3,
        "All figures measured on a 200-ticket sample randomly drawn from real, unfiltered "
        "traffic (not a cherry-picked or easy subset) - the number a customer would "
        "actually experience.",
    )
    pdf.ln(4)

    pdf.h2("How accuracy was improved, step by step")
    pdf.image(timeline_path, x=pdf.l_margin, w=pdf.w - pdf.l_margin - pdf.r_margin)
    pdf.ln(2)
    pdf.bullet("Baseline (33.0%): the previously deployed vector/knowledge-base retrieval classifier, single-shot.")
    pdf.bullet("+9.5 points (42.5%): swapping in the hybrid pipeline - merged KB + real-ticket-example retrieval, reranked by relevance and cross-source agreement, classified hierarchically. Briefly deployed in this form.")
    pdf.bullet("+2.0 further points (44.5%), at higher cost: upgraded to 3x self-consistency voting the same day, which also produces a confidence score used to trigger the clarification step below. This is the configuration currently live - chosen because the confidence signal (not just the accuracy bump) is what makes the clarification loop possible.")

    pdf.h2("Main category vs. sub-category vs. strict (both correct)")
    pdf.body(
        "The figures above are main-category accuracy. Every ticket also needs a "
        "correct sub-category; the table below breaks that out, since it's a "
        "materially different (lower) number.",
        size=9.3,
    )
    col_labels = ["Metric", "Previous baseline", "Currently deployed"]
    rows = [
        ("Main category correct", "33.0%", "44.5%"),
        ("Sub-category correct (given main was already right)", "53.0%", "57.3%"),
        ("Both main AND sub-category correct (strict)", "17.5%", "25.5%"),
    ]
    pdf.set_font("Helvetica", "B", 9)
    pdf.set_text_color(*NAVY)
    pdf.set_fill_color(*LIGHT_GREY)
    w0, w1, w2 = 96, 40, 40
    pdf.cell(w0, 7, col_labels[0], border=0, fill=True)
    pdf.cell(w1, 7, col_labels[1], border=0, fill=True, align="C")
    pdf.cell(w2, 7, col_labels[2], border=0, fill=True, align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 9)
    for label, base, dep in rows:
        pdf.set_x(pdf.l_margin)
        pdf.cell(w0, 7, label)
        pdf.cell(w1, 7, base, align="C")
        pdf.set_font("Helvetica", "B", 9)
        pdf.set_text_color(*TEAL)
        pdf.cell(w2, 7, dep, align="C", new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "", 9)
        pdf.set_text_color(*NAVY)
    pdf.ln(3)

    pdf.h2("Impact on the hardest cases")
    pdf.body(
        "On the ambiguous SOA / CRM OM / Clarity OSS cluster specifically - previously "
        "close to chance-level for a 3-way confusion - main-category accuracy rose from "
        "36.2% to 48.9%. Merging real-ticket examples into the candidate pool gives the "
        "model more disambiguating signal than the hand-written knowledge base alone could."
    )

    # ---------- Confidence & clarification ----------
    pdf.add_page()
    pdf.h1("New Behavior: Confidence-Gated Clarification")
    pdf.body(
        "Rather than always committing to a category, the system now measures its own "
        "confidence (agreement across 3 independent classification attempts) and reacts "
        "accordingly:"
    )
    pdf.bullet("High confidence -> drafts the ticket immediately, as before.")
    pdf.bullet("Low confidence AND the case is outside the known-ambiguous cluster -> asks one clarifying question (e.g. about specifics of the issue), never asking the customer something they couldn't be expected to know (like which internal backend system is involved).")
    pdf.bullet("Low confidence but inside the known-ambiguous cluster -> drafts anyway, since a clarifying question can't resolve information the customer doesn't have either.")
    pdf.bullet("A ticket can only be asked to clarify once - the system always drafts on the second attempt, so customers never get stuck in a loop.")
    pdf.body(
        "Note: the 44.5% figure above measures the classifier in isolation (single-turn "
        "batch evaluation). The live clarification step is a multi-turn conversational "
        "behavior that the batch eval scripts don't simulate, so no fresh headline number "
        "yet captures its real-world effect - it is expected to help, since it targets "
        "exactly the cases most likely to be wrong. Measuring it properly is listed under "
        "Next Steps.",
        size=9,
    )

    # ---------- Reliability ----------
    pdf.add_page()
    pdf.h1("Production Reliability")
    pdf.body(
        "The classifier was first verified with direct function-level tests, which all "
        "passed. Testing was then repeated against the real chat API end-to-end (the "
        "actual path a customer's message takes), which surfaced three issues invisible "
        "to the earlier tests - all now fixed and re-verified:"
    )
    pdf.bullet("Garbled, overlapping text could appear during streaming replies, caused by internal background classification calls being visibly streamed to the customer. Fixed by tagging internal-only calls so they're suppressed from the visible stream.")
    pdf.bullet("Clarifying questions could run on directly into a prior reply with no line break. Fixed by applying the same paragraph-break handling already used elsewhere in the conversation.")
    pdf.bullet("A stale-message bug could cause the system to ask a customer for detail they had already just provided, if they replied to an earlier prompt with a full new description. Fixed by refreshing the tracked query text on that reply path.")
    pdf.body(
        "Reusable automated test suites now cover 8+ real conversation scenarios "
        "(greetings, direct ticket filing, ambiguous cases, mid-conversation topic "
        "changes, ticket status checks) to catch regressions in future changes."
    )

    # ---------- What's unchanged / structural ----------
    pdf.add_page()
    pdf.h1("What Hasn't Changed: The Structural Ceiling")
    pdf.body(
        "The core diagnosis from the original investigation still holds and has been "
        "re-confirmed multiple times since: roughly 70% of real ticket volume is "
        "genuinely ambiguous from text alone, because three categories describe the same "
        "real-world event as seen from three different backend systems. Which one is "
        "correct depends on which system currently owns the order - information that "
        "isn't in the customer's message, no matter how the model is trained or prompted."
    )
    pdf.h2("Decisions on record")
    pdf.bullet("Merging the three ambiguous categories into one coarse intake bucket is OFF THE TABLE - the category taxonomy is company-mandated and cannot be changed (confirmed decision).")
    pdf.bullet("A live lookup of which backend system owns a given order at classification time (an API integration by account/order number) remains the only path considered structurally sufficient to reach 80%+ - confirmed feasible once deployed inside the company network, but not yet built.")

    # ---------- Next steps ----------
    pdf.add_page()
    pdf.h1("Recommended Next Steps")
    pdf.h2("1. Scope and build the backend-ownership lookup")
    pdf.body(
        "The only remaining path to materially higher accuracy on the ambiguous cluster. "
        "Requires new integration work (an internal API call by account/order number) - "
        "not yet started."
    )
    pdf.h2("2. Measure the live clarification loop's real-world effect")
    pdf.body(
        "Build a multi-turn evaluation harness (extending the existing conversation test "
        "suite) to get a true accuracy number for the fully deployed system, including "
        "the clarification step's impact - not just the single-turn classifier number."
    )
    pdf.h2("3. Keep evaluating on representative, unfiltered samples")
    pdf.body(
        "Continue measuring against random real-traffic samples rather than easy/curated "
        "test sets, so reported numbers keep reflecting what customers actually experience."
    )
    pdf.h2("4. Treat the AI category as a first-pass suggestion")
    pdf.body(
        "For the ambiguous cluster specifically, human review remains necessary - this "
        "matches how tickets are already handled today and is unchanged by this work."
    )

    out_path = "domain/helpdesk/data/Helpdesk_Category_Accuracy_Report_v2.pdf"
    pdf.output(out_path)
    print(f"Wrote report to {out_path}")


if __name__ == "__main__":
    main()
