
#creating a memory structure (state) for your Ask HR(SLM) agent.
from typing import Annotated, List, Optional, TypedDict
from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages


#Create a new state for the HR agent
class AskHRSLMAgentState(TypedDict):
    messages: Annotated[List[BaseMessage], add_messages]
    user_id: str
    predicted_intent: str
    confidence_score: Optional[float] 
    is_leave_balance: Optional[bool]
    clarification_pending: Optional[bool]