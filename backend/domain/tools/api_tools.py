"""
API tools for agents that need to query external services or databases.

Provides a real leave-balance lookup for the Ask HR agent by calling
the SLTMobitel ERP API.
"""

import re
import logging
from typing import Annotated

import requests
from langchain_core.tools import tool
from langgraph.prebuilt import InjectedState

logger = logging.getLogger(__name__)

# External API URL for leave balance
LEAVE_BALANCE_API_URL = "https://itsdneylie.slt.lk/neylieapi/erp/getLeaveBal"


def _extract_sid_from_email(email: str) -> str | None:
    """Extract SID (employee ID / numeric part) from an email address."""
    if not email:
        return None
    match = re.search(r'\b(\d{1,10})\b', email)
    return match.group(1) if match else None


@tool
def get_employee_leave_balance(
    user_id: Annotated[str, InjectedState("user_id")],
) -> str:
    """Look up the authenticated employee's remaining leave balance.

    This tool is automatically called when an employee asks about their
    personal leave data (annual leave, casual leave, sick leave, etc.).

    Args:
        user_id: Injected from the agent state — hidden from the LLM schema.
                 Expected to be the employee's email address.

    Returns:
        A human-readable summary of the employee's leave balance.
    """
    # Extract the numeric SID from the email / user_id
    sid = _extract_sid_from_email(user_id)
    if not sid:
        return (
            "Could not determine your Service ID from your account. "
            "Please contact HR for assistance."
        )

    try:
        response = requests.post(
            LEAVE_BALANCE_API_URL,
            json={"sid": sid},
            timeout=10,
            headers={"Content-Type": "application/json"},
        )

        if response.status_code != 200:
            logger.error(f"Leave API returned status {response.status_code}")
            return "Unable to fetch leave balance at the moment. Please try again later."

        data = response.json()

        # Parse the nested response: data[0].data[]
        data_list = data.get("data", [])
        if not data_list:
            return f"No leave records found for Service ID {sid}. Please contact HR."

        leave_entries = data_list[0].get("data", [])
        if not leave_entries:
            return f"No leave records found for Service ID {sid}. Please contact HR."

        # Build a readable summary from the leave entries
        lines = [f"Leave balance for Employee {sid}:\n"]
        for entry in leave_entries:
            plan = entry.get("Leave_Plan", "Unknown")
            entitlement = entry.get("Entitlement", 0)
            balance = entry.get("Current_Balance", 0)
            lines.append(
                f"• {plan}: {balance} days remaining (out of {entitlement} entitled)"
            )

        return "\n".join(lines)

    except requests.Timeout:
        logger.error("Leave API request timed out")
        return "The leave balance request timed out. Please try again."
    except requests.ConnectionError:
        logger.error("Cannot connect to Leave API")
        return "Cannot connect to the HR system. Please check your connection or try again later."
    except Exception as exc:
        logger.error(f"Error fetching leave balance: {exc}")
        return "An error occurred while fetching your leave balance. Please try again later."
