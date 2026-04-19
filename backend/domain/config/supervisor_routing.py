"""Routing profiles and thresholds for the Ask SLT supervisor agent.

This module keeps the specialist routing metadata separate from the supervisor
implementation so the routing surface can be tuned later without rewriting the
agent logic.
"""

from __future__ import annotations

SPECIALIST_ROUTING_PROFILES: dict[str, dict[str, object]] = {
    "hr": {
        "display_name": "HR",
        "description": (
            "This domain handles employee-related matters at SLTMobitel, including "
            "attendance marking, various leave types (annual, casual, sick, maternity, "
            "overseas), and contract employment administration. It also covers employee "
            "benefits and financial support like medical claims (including Agrahara) and "
            "staff loans, as well as performance management, grievance handling, and "
            "workplace policies such as digital media usage."
        ),
        "keywords": [
            "apply for leave",
            "leave balance",
            "annual leave",
            "casual leave",
            "sick leave",
            "medical leave",
            "half day leave",
            "short leave",
            "maternity leave",
            "child care leave",
            "accident leave",
            "overseas leave",
            "mark attendance",
            "finger scan machine",
            "late attendance",
            "unauthorized absence",
            "ERP leave system",
            "distress loan",
            "motorcycle loan",
            "motor car loan",
            "TDC education loan",
            "loan guarantor",
            "top up loan",
            "loan interest rate",
            "medical claim",
            "Agrahara reimbursement",
            "spectacles claim",
            "dental claim",
            "hospitalization benefit",
            "critical illness benefit",
            "add dependents ERP",
            "performance appraisal",
            "KPI targets",
            "mid-year review",
            "peer evaluation",
            "fairness review committee",
            "annual bonus",
            "salary increase",
            "file a grievance",
            "grievance handling committee",
            "vacation of post",
            "VOP",
            "probation period",
            "EPF",
            "ETF",
            "digital media policy",
            "social media rules",
        ],
        "examples": [
            "How do I apply for annual leave in the ERP system?",
            "What is the policy for taking an afternoon half-day leave?",
            "Where can I find the application for a distress loan?",
            "How many sick leave days do I get in my first year of probation?",
            "Can I apply for a motorcycle loan if I don't have 5 years of service?",
            "How do I claim reimbursement for my new spectacles?",
            "What documents are needed to apply for the critical illness medical benefit?",
            "How do I add my spouse and newborn child to the Agrahara medical benefits?",
            "What is the process for the mid-year performance review discussion?",
            "How are individual KPI targets set and updated in the ERP system?",
            "What should I do if I have a grievance with my manager regarding my performance rating?",
            "Am I allowed to post about SLTMobitel on my personal Facebook account?",
            "What is the disciplinary penalty for unauthorized absence from work?",
            "How do I apply for overseas no-pay leave for higher studies?",
            "Does my contract employment include EPF and ETF contributions?",
            "What happens to my unutilized earned leave when my contract ends?",
            "What is the maximum amount I can get for the TDC education loan?",
            "Where do I submit the valuation report for a used motor car loan?",
        ],
    },
    "finance": {
        "display_name": "Finance",
        "description": (
            "Finance and accounting matters such as salary, payroll execution, "
            "allowances, reimbursement, invoices, procurement budgets, payments, "
            "purchase orders, expense claims, and financial guidelines."
        ),
        "keywords": [
            "salary",
            "payroll",
            "allowance",
            "invoice",
            "budget",
            "payment",
            "reimbursement",
            "expense claim",
            "purchase order",
            "procurement",
            "finance policy",
        ],
        "examples": [
            "When is the salary credited?",
            "How do I submit an expense claim?",
            "Who approves a procurement invoice?",
        ],
    },
    "admin": {
        "display_name": "Admin",
        "description": (
            "Administration and office support such as transport, facilities, "
            "building access, seating, office assets, parking, security, "
            "maintenance, and general admin services."
        ),
        "keywords": [
            "transport",
            "facility",
            "office access",
            "parking",
            "security",
            "maintenance",
            "asset request",
            "admin support",
            "seating",
            "building pass",
        ],
        "examples": [
            "How do I request office transport?",
            "Who handles parking access?",
            "How can I report a facility issue?",
        ],
    },
    "it": {
        "display_name": "IT",
        "description": (
            "IT matters such as technical support, software issues, hardware requests, "
            "network and Wi-Fi, VPN, email accounts, password resets, access management, "
            "and general IT services."
        ),
        "keywords": [
            "it support",
            "technical support",
            "software",
            "hardware",
            "laptop request",
            "network",
            "wi-fi",
            "vpn",
            "email",
            "password reset",
            "access request",
        ],
        "examples": [
            "How do I reset my email password?",
            "How to connect to the corporate VPN?",
            "How can I request a new laptop?",
        ],
    },
    "cio": {
        "display_name": "CIO",
        "description": (
            "CIO matters such as IT strategy, digital transformation, technology roadmap, "
            "enterprise architecture, governance, vendor management, and cybersecurity strategy."
        ),
        "keywords": [
            "cio",
            "strategy",
            "roadmap",
            "digital transformation",
            "governance",
            "architecture",
            "cybersecurity strategy",
        ],
        "examples": [
            "What is the IT strategy for this year?",
            "Tell me about the digital transformation roadmap.",
            "Where can I find the enterprise architecture guidelines?",
        ],
    },
}

GENERAL_HELP_PATTERNS: tuple[str, ...] = (
    r"^\s*(hi|hello|hey|good morning|good afternoon|good evening)\b",
    r"\b(thanks|thank you|bye|goodbye)\b",
    r"\bwhat can you do\b",
    r"\bhow can you help\b",
    r"\bwho are you\b",
    r"\bwhat is ask slt\b",
    r"\bhow does this work\b",
    r"\bhow do i use (this|the platform|ask slt)\b",
    r"\bwhich agent should i use\b",
    r"\bwho handles\b",
    r"\bwhere should i ask\b",
    r"\bavailable agents\b",
    r"\bhelp me choose\b",
    r"\broute me\b",
)

VAGUE_SPECIALIST_PATTERNS: tuple[str, ...] = (
    r"^\s*help\s*\??\s*$",
    r"^\s*support\s*\??\s*$",
    r"^\s*i need help\s*\??\s*$",
    r"^\s*i need help with something\s*\??\s*$",
    r"^\s*i need assistance\s*\??\s*$",
    r"^\s*can you help me\s*\??\s*$",
    r"^\s*i have a question\s*\??\s*$",
    r"^\s*i need to ask something\s*\??\s*$",
)

CLARIFICATION_CHOICE_ALIASES: dict[str, tuple[str, ...]] = {
    "hr": ("hr", "human resources"),
    "finance": ("finance", "financial", "accounts", "accounting"),
    "admin": ("admin", "administration", "facilities"),
    "it": ("it", "information technology", "tech support", "technical support"),
    "cio": ("cio", "chief information officer", "technology strategy"),
}

FOLLOW_UP_PATTERNS: tuple[str, ...] = (
    r"^\s*what about( that| this)?\s*\??$",
    r"^\s*how about( that| this)?\s*\??$",
    r"^\s*can i apply\s*\??$",
    r"^\s*can i do that\s*\??$",
    r"^\s*what next\s*\??$",
    r"^\s*how do i apply\s*\??$",
    r"^\s*and that\s*\??$",
    r"^\s*what else\s*\??$",
    r"^\s*how much\s*\??$",
)

# Initial tuning defaults for cosine similarity routing.
# These should be refined later using real routing logs.
STRONG_ROUTE_THRESHOLD = 0.42
LOW_CONFIDENCE_THRESHOLD = 0.30
OUT_OF_SCOPE_THRESHOLD = 0.22
MIN_ROUTE_MARGIN = 0.04
FOLLOW_UP_STICKINESS_BOOST = 0.06
SHORT_FOLLOW_UP_MAX_WORDS = 6

# Multi-specialist fan-out: when the top match is not strong enough to delegate
# alone but the runner-up is also plausible, consult both specialists in parallel
# and synthesize a single answer instead of asking the user to clarify.
MULTI_DELEGATE_SECONDARY_THRESHOLD = 0.30
MULTI_DELEGATE_MAX_AGENTS = 2