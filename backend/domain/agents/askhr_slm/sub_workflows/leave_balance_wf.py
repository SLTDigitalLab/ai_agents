import logging
from langgraph.graph import StateGraph, START, END
from langchain_core.messages import AIMessage
from domain.tools.api_tools import fetch_leave_balance_for_user

from domain.agents.askhr_slm.state import AskHRSLMAgentState
from domain.tools.api_tools import fetch_leave_balance_for_user

log = logging.getLogger(__name__)

async def fetch_leave_node(state: AskHRSLMAgentState) -> dict:
    """"Calls the ERP API to fetch the real time leave balance."""
    user_id = state.get("user_id", "") # get the user_id from the state
    leave_data = fetch_leave_balance_for_user(user_id) #call the ERP API to fetch leave balance
    answer = f"Here is your personal leave balance from the SLT ERP:\n\n{leave_data}"
    return {"messages": [AIMessage(content=answer)]} #return the formated answer

#Workflow Assembly
leave_builder = StateGraph(AskHRSLMAgentState)
leave_builder.add_node("fetch_leave_balance", fetch_leave_node)


leave_builder.add_edge(START, "fetch_leave_balance")
leave_builder.add_edge("fetch_leave_balance", END)
#Compiled child graph
leave_balance_subgraph = leave_builder.compile()