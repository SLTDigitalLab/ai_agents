from typing import Literal
from pydantic import BaseModel, Field
from langchain_core.prompts import ChatPromptTemplate
from core.config import agent_collection_name
from core.llm import get_chat_model


# 1. DEFINE THE STRUCTURED OUTPUT SCHEMA
class RouterDecision(BaseModel):
    """The structured decision output from the LLM router."""
    
    selected_agent: Literal["hr", "it", "finance"] = Field(
        ...,
        description="The target agent that must handle this query."
    )
    reasoning: str = Field(
        ...,
        description="Short reason why this specific agent was selected."
    )


# 2. THE ROUTER FUNCTION
async def classify_user_query(query: str) -> RouterDecision:
    """Reads the user's question and decides which agent should handle it."""
    
    # Initialize your LLM engine
    llm = get_chat_model()
    
    # Enforce structured output using Pydantic schema
    structured_llm = llm.with_structured_output(RouterDecision)
    
    # System prompt defining agent responsibilities
    prompt = ChatPromptTemplate.from_messages([
        ("system", (
            "You are an intent router for an enterprise company.\n"
            "Analyze the user's inquiry and assign it to the most relevant agent:\n\n"
            "- 'hr': Questions about annual leave, medical insurance, employee benefits, salary, or HR policies.\n"
            "- 'it': Questions about password resets, Wi-Fi, VPN, software access, or hardware problems.\n"
            "- 'finance': Questions about expense claims, invoices, budgets, or payment reimbursements.\n"
        )),
        ("human", "{query}")
    ])
    
    # Combine prompt with structured LLM
    chain = prompt | structured_llm
    
    # Run classification
    decision: RouterDecision = await chain.ainvoke({"query": query})
    return decision


# 1. Map each agent name to its Qdrant database collection
AGENT_COLLECTION_MAP = {
    "hr": agent_collection_name("hr"),
    "it": agent_collection_name("it"),
    "finance": agent_collection_name("finance"),
}

def get_target_collection(agent_name: str) -> str:
    """Returns the Qdrant collection name for a given agent name.
    
    Defaults to the Ask IT collection if an unknown agent is provided.
    """
    return AGENT_COLLECTION_MAP.get(agent_name, "askit_docs")


async def run_multi_agent_pipeline(query: str):
    """Execution Pipeline:
    1. Classifies intent using the LLM Router.
    2. Maps the decision to the target Qdrant collection.
    3. Returns routing info and targeted collection for execution.
    """
    # 1. Run LLM Router Classification
    decision = await classify_user_query(query)
    
    # 2. Get Target Database Collection
    target_collection = get_target_collection(decision.selected_agent)
    
    # 3. Output routing context for downstream execution/retrieval
    return {
        "query": query,
        "selected_agent": decision.selected_agent,
        "target_collection": target_collection,
        "reasoning": decision.reasoning,
    }


if __name__ == "__main__":
    import asyncio

    async def test_router():
        res = await classify_user_query("How do I request an extra monitor for my desk?")
        print("Selected Agent:", res.selected_agent)
        print("Reasoning:", res.reasoning)

    asyncio.run(test_router())



if __name__ == "__main__":
    import asyncio

    async def test_router_and_mapper():
        # Step 1: LLM Classification
        decision = await classify_user_query("How do I request an extra monitor for my desk?")
        
        # Step 2: Collection Mapping
        collection_name = get_target_collection(decision.selected_agent)
        
        print(f"Selected Agent   : {decision.selected_agent}")
        print(f"Target Collection: {collection_name}")
        print(f"Reasoning        : {decision.reasoning}")

    asyncio.run(test_router_and_mapper())    

if __name__ == "__main__":
    import asyncio

    async def test_full_pipeline():
        # Test query 1: Finance inquiry
        res_finance = await run_multi_agent_pipeline("How do I get reimbursed for my lunch meeting?")
        print("\n--- Test 1 (Finance) ---")
        print(f"Selected Agent   : {res_finance['selected_agent']}")
        print(f"Target Collection: {res_finance['target_collection']}")
        print(f"Reasoning        : {res_finance['reasoning']}")

        # Test query 2: HR inquiry
        res_hr = await run_multi_agent_pipeline("How many days of annual leave do I have left?")
        print("\n--- Test 2 (HR) ---")
        print(f"Selected Agent   : {res_hr['selected_agent']}")
        print(f"Target Collection: {res_hr['target_collection']}")
        print(f"Reasoning        : {res_hr['reasoning']}")

    asyncio.run(test_full_pipeline())