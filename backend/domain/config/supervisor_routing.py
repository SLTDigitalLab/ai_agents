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
            "salary",
            "loan",
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
            "Handles corporate finance operations including AP invoice processing, "
            "CAPEX budgeting, advance payments, taxation (VAT, WHT, NBT), and "
            "financial reporting under SLFRS. Manages billing dispute resolution "
            "and adjustment approvals through committees like MDRC, CDRTF, CDRAC, "
            "PDRC, and BAAC according to delegated financial authority limits. "
            "Oversees bank reconciliation, cash handling, vendor payments via EFT "
            "or cheque, and ERP General Ledger maintenance."
        ),
        "keywords": [
            "MDRC",
            "CDRTF",
            "CDRAC",
            "PDRC",
            "BAAC",
            "VAT",
            "WHT",
            "NBT",
            "SLFRS",
            "AP Invoice",
            "Batta payment",
            "Work Confirmation",
            "Purchase Order",
            "PO matching",
            "GRN",
            "CAPEX Budget",
            "advance payment",
            "advance settlement",
            "billing dispute",
            "bill adjustment",
            "sales reversal",
            "credit memo",
            "debit memo",
            "bank reconciliation",
            "cash forecasting",
            "mCash refund",
            "Geneva adjustment",
            "Delegation of Financial Authority",
            "Junior Tender Board",
            "Senior Tender Board",
            "Letter of Credit",
            "LC payment",
            "General Ledger",
            "Trial Balance",
            "telecommunication levy",
            "CPE recovery",
            "Manual Receipt",
            "EFT payment",
            "debt collecting company",
            "manual invoice",
        ],
        "examples": [
            "What are the role and responsibilities of MDRC?",
            "Who approves an MSME dispute adjustment limit above 50,000 for the MDRC team?",
            "How do I record a corporate billing dispute for the CDRTF?",
            "What is the adjustment limit recommended by the CDRTF?",
            "Who sits on the Corporate Customers Dispute Resolution Adjustment Committee (CDRAC)?",
            "How does CDRAC handle billing adjustments over 1.5 million LKR?",
            "When should a regional dispute be forwarded to the PDRC?",
            "Who is the Co-Chairman of the PDRC?",
            "What is the adjustment approval limit for the BAAC?",
            "How do I submit a case to the Bill Adjustment Approval Committee (BAAC)?",
            "How do I process an AP Invoice matched to a Purchase Order?",
            "Where do I find the Advance Request Form and Advance Settlement Form?",
            "What is the procedure for an mCash refund?",
            "Who has the contract signing authority for non-standardized goods?",
            "How do I calculate WHT and VAT on a standard invoice?",
            "What is the ERP process for updating the General Ledger?",
            "How do I handle manual receipts if the cashiering system is down?",
            "What are the steps for a Letter of Credit (LC) payment?",
            "How do I handle CPE recovery for a customer with arrears over Rs. 10,000?",
            "Where do I apply for a CAPEX budget allocation for a new project?",
        ],
    },
    "admin": {
        "display_name": "Admin",
        "description": (
            "The Admin domain handles physical infrastructure, facilities "
            "management, and logistical operations, including power and AC "
            "maintenance, vehicle fleet management, parking allocation, and "
            "property leasing such as holiday bungalows. It oversees physical "
            "security, access control, visitor and gate passes, incident "
            "reporting, and fire safety (FAFA). Key services and assets are "
            "managed through bodies like the Transport Management Section, "
            "Property Management Team, Facility Management Section, and the "
            "Security Section."
        ),
        "keywords": [
            "office transport",
            "PickMe cab service facility",
            "hired vehicle allocation",
            "Self-Provided Vehicle Scheme",
            "vehicle tracking device",
            "e-running chart system",
            "driver's running record book",
            "vehicle in and out registry",
            "access control procedure",
            "visitor pass",
            "temporary electronic access card",
            "trainee pass",
            "gate pass form",
            "Item Declaration Form",
            "IDF",
            "children book",
            "visitor entry form",
            "security help desk",
            "incident management system",
            "daily occurrence book",
            "FAFA",
            "fire extinguisher refilling",
            "holiday bungalow tariff",
            "holiday bungalow booking",
            "property management",
            "third-party occupied spaces",
            "HQ vehicle parking",
            "Lotus Road parking",
            "Leisure parking",
            "green colour parking pass",
            "E pass and F pass",
            "DGM car park",
            "motor bicycle parking",
            "Power and AC Operations",
            "comfort AC",
            "precision AC",
            "UPS backup power",
            "backup generator",
            "surge protection devices",
            "SPD",
            "Facility Management Section",
            "Transport Management Section",
            "Property Management Team",
            "Security Section",
            "Document Preparation Committee",
        ],
        "examples": [
            "Where can I find the Item Declaration Form (IDF) for bringing in personal electronics?",
            "Who needs to sign the Item Declaration Form when I bring my camera to the office?",
            "How do I get a Gate pass form approved to take office equipment out of the building?",
            "What is the procedure for returning the original Gate pass form to the security officer?",
            "Where is the 'in and out' registry kept for hired vehicles?",
            "Does the security officer or the driver update the 'in and out' registry at the gate?",
            "How do I report a security incident for the daily occurrence book?",
            "Who checks the daily occurrence book for minor incidents?",
            "How does the Vehicle Officer in Charge certify invoices using the e-running chart system?",
            "What should I do if the e-running chart system fails during an official trip?",
            "Do I need to enter my relation's details in the children book at the reception desk?",
            "Is the children book mandatory for all visitors under 16 years old?",
            "Where do I submit a visitor entry form for a client meeting at HQ?",
            "Who approves the visitor entry form through the visitor management system?",
            "Who do I contact in the Transport Management Section for a PickMe cab facility?",
            "How does the Transport Management Section handle excess mileage payments for hired vehicles?",
            "How do I report a subsidiary occupied space to the Property Management Team?",
            "What details must be sent to the Property Management Team for billing rented spaces?",
            "Who approves new comfort AC installations in the Facility Management Section?",
            "Does the Facility Management Section handle pest control and elevator maintenance?",
            "How do I submit policy feedback to the Document Preparation Committee?",
            "Who leads the Document Preparation Committee for power and AC operational guidelines?",
        ],
    },
    "it": {
        "display_name": "IT",
        "description": (
            "The IT department manages corporate hardware (desktops, laptops, VDI thin "
            "clients, printers) and enterprise network connectivity (VPNs, firewalls, "
            "WiFi). It also handles user account provisioning, password resets, data "
            "security (DLP, backups, malware protection), and resolves IT service desk "
            "incidents like system outages or equipment damage."
        ),
        "keywords": [
            "password reset",
            "forgot password",
            "locked account",
            "unlock account",
            "domain password expired",
            "workflow manager request",
            "VPN connection",
            "VPN not working",
            "remote access",
            "site-to-site VPN",
            "extend VPN",
            "MFA",
            "laptop request",
            "desktop replacement",
            "broken laptop",
            "stolen laptop",
            "physical damage to PC",
            "hardware repair",
            "IT repair center",
            "new user account",
            "user registration",
            "deregister user",
            "transfer employee account",
            "VDI account",
            "VDI thin client",
            "work from home setup",
            "install software",
            "uninstall software",
            "trial software approval",
            "printer issue",
            "IT helpdesk",
            "report IT incident",
            "data backup",
            "restore data",
            "recover deleted file",
            "firewall rule change",
            "allow service",
            "block service",
            "WAF request",
            "USB drive blocked",
            "DLP alert",
            "data leakage",
            "report data breach",
            "wireless hotspot",
            "WiFi access",
            "network cable issue",
            "data center access",
            "ERP down",
            "CRM issue",
            "system outage",
        ],
        "examples": [
            "How do I request a new user account for a new employee through Workflow Manager?",
            "My domain password expired and my account is locked, how do I reset it?",
            "My corporate laptop is physically damaged, where do I take it for repair?",
            "How do I get a remote access VPN connection set up for working from home?",
            "I need to install some trial software on my official desktop, who do I contact?",
            "My VPN connection is expiring next week, how do I request an extension?",
            "I'm transferring to a different department, what do I do with my current IT accounts and PC?",
            "How do I request a firewall rule change to allow a new service through the network?",
            "I accidentally deleted a critical file, can you restore it from the backup?",
            "Why is my USB drive blocked when I try to copy files from my laptop?",
            "Where can I find the form to request physical access to the IT Data Center?",
            "What do I do if my official laptop or accessories are stolen?",
            "I need a VDI account and a thin client set up for a new call center user.",
            "Who do I contact to report a suspected data leakage or privacy incident?",
            "The ERP system seems to be down, is there a system outage right now?",
            "My office printer is not connecting to the network, can someone from the service desk help?",
            "How do I get approval to connect my mobile device to the corporate WiFi hotspot?",
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
STRONG_ROUTE_THRESHOLD = 0.40
LOW_CONFIDENCE_THRESHOLD = 0.25
OUT_OF_SCOPE_THRESHOLD = 0.18
MIN_ROUTE_MARGIN = 0.04
FOLLOW_UP_STICKINESS_BOOST = 0.06
SHORT_FOLLOW_UP_MAX_WORDS = 6

# Multi-specialist fan-out: when the top match is not strong enough to delegate
# alone but the runner-up is also plausible, consult both specialists in parallel
# and synthesize a single answer instead of asking the user to clarify.
MULTI_DELEGATE_SECONDARY_THRESHOLD = 0.25
MULTI_DELEGATE_MAX_AGENTS = 2

# Keyword match boost: when a query contains one of a specialist's exact keywords
# (word-boundary match, case-insensitive), add this to that specialist's cosine
# score. This corrects for profile-embedding dilution on short queries where the
# semantic model under-scores an obvious keyword hit.
KEYWORD_MATCH_BOOST = 0.12