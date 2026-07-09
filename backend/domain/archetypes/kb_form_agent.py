"""
Archetype 3 - Knowledge-Base + Form (Generative UI) agent graph.

Used by: Ask Lifestore, Ask Enterprise.

The LLM answers product/service questions via RAG and triggers a frontend
form when the user wants to buy, order, or subscribe. No backend state
machine is needed — the React frontend handles the form rendering when
it detects the ``[RENDER_*_FORM]`` token in the response.

LifeStore enhancement:
- Ask LifeStore can now use MCP-backed product tools for product search,
  precise lookup, availability checks, comparisons, categories, and local
  draft-order demonstration.
- Ask Enterprise keeps using the normal RAG tool only.

Flow:
    START ──► agent (LLM) ──► tools_condition ──► tools ──► agent ──► END
"""

import os
import re

from langchain_core.messages import AIMessage, trim_messages
from langgraph.graph import START, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition

from core.llm import get_chat_model
from domain.state import AgentState
from domain.tools.rag_tools import search_knowledge_base
from domain.tools.lifestore_mcp_tools import LIFESTORE_MCP_TOOLS
from domain.tools.lifestore_cart_tools import LIFESTORE_CART_TOOLS


# ── LLM setup ────────────────────────────────────────────────────────────
llm = get_chat_model()


# ── Tool setup ───────────────────────────────────────────────────────────
# Base tool used by all KB + Form agents.
BASE_TOOLS = [search_knowledge_base]

# LifeStore gets the normal RAG tool, MCP-backed product tools, and the
# chat-driven cart + PayHere checkout tools.
LIFESTORE_TOOLS = BASE_TOOLS + LIFESTORE_MCP_TOOLS + LIFESTORE_CART_TOOLS

# ToolNode must know every possible tool that any agent in this workflow can call.
# However, in call_model(), the LLM is only bound to the correct tool list for
# the current agent_id, so Enterprise will not see LifeStore-specific MCP tools.
ALL_TOOLS = BASE_TOOLS + LIFESTORE_MCP_TOOLS + LIFESTORE_CART_TOOLS


def _is_lifestore_agent(agent_id: str) -> bool:
    return "lifestore" in agent_id.lower()


def _get_agent_tools(agent_id: str):
    if _is_lifestore_agent(agent_id):
        return LIFESTORE_TOOLS
    return BASE_TOOLS


def _get_form_token(agent_id: str) -> str:
    if _is_lifestore_agent(agent_id):
        return "[RENDER_LIFESTORE_FORM]"
    return "[RENDER_ENTERPRISE_FORM]"


def _get_lifestore_tool_rules() -> str:
    return """
LIFESTORE MCP TOOL-SELECTION RULES:
For Ask LifeStore, you have access to MCP-backed LifeStore product tools.

Use the tools like this:

1. `lifestore_precise_product_lookup_tool`
   Use this when the user asks about one specific named product, product ID, SKU, or URL.
   Examples:
   - "Tell me about TP-Link TD-W8961ND"
   - "What is the price of COMSTOX ZLT T10 Max?"
   - "Who is the seller of this product?"

2. `lifestore_availability_lookup_tool`
   Use this when the user asks whether one specific product is available, in stock, or out of stock.
   Examples:
   - "Is TP-Link TD-W8961ND available?"
   - "Is this router in stock?"
   - "Do you have COMSTOX ZLT T10 Max?"

3. `lifestore_filtered_product_search_tool`
   Use this for filtered product-list requests.
   Examples:
   - "Show me available routers" → query="router", in_stock_only=true
   - "Show me in-stock routers" → query="router", in_stock_only=true
   - "Show me routers under Rs. 10000" → query="router", max_price=10000
   - "Show me available routers under Rs. 15000" → query="router", in_stock_only=true, max_price=15000

   CRITICAL FILTERED SEARCH OUTPUT RULES:
   - When the user asks for available, in-stock, or currently available products, call this tool with `in_stock_only=true`.
   - In the final answer, mention ONLY products returned in the filtered `products` list.
   - Do NOT mention excluded, out-of-stock, unavailable, or related products unless the user specifically asks for alternatives or all matching products.
   - Do NOT add notes like "this product was found but out of stock" for available-product queries.
   - Product cards must contain only the filtered products shown in the answer.

4. `lifestore_strict_category_products_tool`
   Use this for exact category or offer-listing questions.
   This tool must be used instead of hybrid search when the user asks about:
   - Special Offers
   - specially offered items
   - New Connection Offers
   - Bundle Offers
   - exact LifeStore categories

   Examples:
   - "What are the specially-offered items?" → category="Special Offers"
   - "What are the new connection offers?" → category="New Connection Offers"
   - "Show me bundle offers" → category="Bundle Offers"

   CRITICAL CATEGORY/OFFER RULES:
   - For offer/category questions, do NOT use Qdrant/vector-based semantic results.
   - Use only the products returned by `lifestore_strict_category_products_tool`.
   - If the tool returns no products, say no products were found in the current LifeStore data.
   - Do NOT mention products from the hybrid search, vector evidence, or unrelated categories.

5. `lifestore_compare_products_tool`
   Use this when the user asks to compare products.
   Examples:
   - "Compare these two routers"
   - "Compare TP-Link TD-W8961ND and COMSTOX ZLT T10 Max"
   - "Compare available routers"

6. `lifestore_hybrid_product_search_tool`
   Use this for broad LifeStore product search, general browsing, product recommendations,
   and general product questions when no strict filter/category rule applies.
   Examples:
   - "Show me routers"
   - "Recommend routers"
   - "Do you have TVs?"
   - "What products are available?"

   Do NOT use this for:
   - Special Offers / New Connection Offers / Bundle Offers
   - broad available/in-stock queries that need `in_stock_only=true`

7. `lifestore_list_categories_tool`
   Use this when the user asks for LifeStore categories.
   Examples:
   - "What categories do you have?"
   - "List LifeStore product categories"

8. `lifestore_add_to_cart`
   Use this when the customer wants to add/buy a specific product
   (e.g. "add the COMSTOX ZLT T10 Max", "I'll take 2 of those", "buy this one").
   Pass the product name/ID as product_query and the quantity. The price is
   taken from the live catalog automatically — NEVER state or guess a price.
   After adding, briefly confirm what was added and the running subtotal from
   the tool result.

9. `lifestore_view_cart`
   Use when the customer asks what's in their cart, or before checkout.

10. `lifestore_update_cart_item` / `lifestore_remove_from_cart` / `lifestore_clear_cart`
   Use to change a quantity ("make it 3"), remove one product, or empty the cart.

11. `lifestore_begin_checkout`  ← PAYMENT
   Use ONLY when the customer clearly wants to pay / check out and the cart is
   not empty (e.g. "checkout", "pay now", "place the order", "I'm ready to pay").
   The tool returns an `order_id` and the server-computed `amount`.
   After it succeeds you MUST:
   - Tell the customer the total to pay (use the tool's `amount` exactly).
   - Say clearly this is a sandbox demo payment — no real money is charged.
   - End your reply with EXACTLY this marker on its own, using the returned id:
     [RENDER_LIFESTORE_CHECKOUT:<order_id>]
   NEVER write a payment URL, card form, or made-up amount yourself. The secure
   PayHere checkout button is rendered by the frontend from the order_id.
   Do NOT append the old [RENDER_LIFESTORE_FORM] token in the checkout flow.

CHECKOUT SAFETY RULES:
- All prices and totals come only from the cart/checkout tool results. Never
  invent, estimate, or round prices.
- If lifestore_begin_checkout returns status "cart_empty", ask the customer to
  add a product first instead of emitting a checkout marker.

8b. `lifestore_create_draft_order_tool`
   Legacy local draft order (no payment). Do not use this for the normal cart +
   pay flow; prefer lifestore_add_to_cart + lifestore_begin_checkout.

IMPORTANT LIFESTORE RULES:
- For LifeStore product questions, prefer MCP product tools over `search_knowledge_base`.
- Use `search_knowledge_base` only as a fallback for LifeStore non-product knowledge or when MCP tools cannot answer.
- For a single-product question, do NOT return a whole category.
- For availability questions, return only the specific matched product.
- Never mix details from different products.
- Do NOT print raw image URLs in the chat answer. Image URLs belong only in structured product data.
- Do NOT invent prices, stock status, seller, brand, features, categories, offer labels, or availability.
"""

def _payments_enabled() -> bool:
    return os.getenv("LIFESTORE_PAYMENTS_ENABLED", "true").strip().lower() in {"1", "true", "yes"}


def _build_purchase_rule(agent_id: str, form_token: str) -> str:
    """
    Build the "what happens on purchase intent" section of the prompt.

    LifeStore with payments enabled uses the cart/checkout tools exclusively
    and must NEVER fall back to the legacy [RENDER_LIFESTORE_FORM] token — that
    token is only for Enterprise (and LifeStore when payments are disabled).
    Mixing the two caused both the cart tool AND the old form to render for the
    same purchase message.
    """
    if _is_lifestore_agent(agent_id) and _payments_enabled():
        return f"""
5. PURCHASE / CHECKOUT RULE (chat commerce):
   LifeStore purchases go through the cart + checkout tools only
   (`lifestore_add_to_cart`, `lifestore_view_cart`, `lifestore_update_cart_item`,
   `lifestore_remove_from_cart`, `lifestore_clear_cart`, `lifestore_begin_checkout`).

   The token {form_token} is RETIRED for LifeStore. Never write it, in any
   response, for any reason.

   When the user expresses intent to add/buy a specific product
   (e.g. "I want to buy this", "add this to my cart", "I'll take 2"):
   - Call `lifestore_add_to_cart` with the product and quantity.
   - Confirm what was added and the running subtotal using ONLY the tool's
     returned values. Do not ask for name, address, or phone.

   When the user expresses checkout/payment intent
   (e.g. "checkout", "I'm ready to pay", "place the order", "pay now"):
   - Call `lifestore_begin_checkout` and follow tool rule 11 exactly: state the
     total from the tool result, mention this is a sandbox demo, and end the
     reply with EXACTLY the returned [RENDER_LIFESTORE_CHECKOUT:<order_id>]
     marker as the final text.

   Do NOT end informational answers with purchase-suggestion sentences such as
   "If you want, I can help you proceed with the purchase request."
   If the user asks only for information, answer descriptively and stop — do
   not add to the cart or start checkout.
"""

    return f"""
5. FORM TRIGGER RULE:
   Append {form_token} ONLY when the user's latest message directly and clearly expresses that they want to proceed with a purchase, order, subscription, application, registration, or service request.

   Append {form_token} for clear intent such as:
   - "I want to buy this"
   - "I need to buy a TV"
   - "I want to order this product"
   - "I want to purchase this"
   - "I want to subscribe to this package"
   - "I need a new connection"
   - "I want to apply for this service"
   - "Register me for this"
   - "How can I buy this?"
   - "How can I order this?"

   Do NOT append {form_token} for informational, comparison, recommendation, stock, seller, price, feature, or availability questions.

   Do NOT append {form_token} for questions such as:
   - "What is the price?"
   - "Is this in stock?"
   - "Is this available?"
   - "Who is the seller?"
   - "What are the features?"
   - "Tell me about this product"
   - "Compare these products"
   - "Recommend a router"
   - "Do you have TVs?"
   - "What products are available?"

   Do NOT end informational answers with purchase-suggestion sentences such as "If you want, I can help you proceed with the purchase request."

   If the user asks only for information, answer descriptively and stop.

   If the user clearly wants to buy/order/subscribe/apply/register, append exactly this token at the very end of the response:
   {form_token}
"""


def _get_enterprise_tool_rules() -> str:
    return """
ENTERPRISE TOOL-SELECTION RULES:
For Ask Enterprise, use `search_knowledge_base` to answer factual questions about Enterprise
products, services, pricing, eligibility, features, and packages.

Do not use LifeStore MCP tools for Enterprise questions.
"""



def _latest_user_text(state: AgentState) -> str:
    for message in reversed(state.get("messages", [])):
        if getattr(message, "type", None) == "human":
            return str(message.content or "").strip()
    return ""


def _extract_lifestore_purchase_product(text: str) -> str:
    cleaned = re.sub(
        r"\b(i\s+need\s+to|i\s+want\s+to|want\s+to|need\s+to|please|can\s+i|how\s+can\s+i)\b",
        "",
        text,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(
        r"\b(buy|purchase|order|get|place\s+an\s+order\s+for|place\s+order\s+for|a|an)\b",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .?!")
    return cleaned


def _is_lifestore_purchase_intent(text: str) -> bool:
    """
    Deterministic gate for opening LifeStoreForm.

    This bypasses the normal LLM/tool loop for clear purchase intent, making
    form rendering faster and more reliable.
    """
    value = text.lower().strip()

    purchase_patterns = [
        r"\bi\s+need\s+to\s+buy\b",
        r"\bi\s+want\s+to\s+buy\b",
        r"\bi\s+need\s+to\s+purchase\b",
        r"\bi\s+want\s+to\s+purchase\b",
        r"\bi\s+need\s+to\s+order\b",
        r"\bi\s+want\s+to\s+order\b",
        r"\bhow\s+can\s+i\s+buy\b",
        r"\bhow\s+can\s+i\s+order\b",
        r"\bplace\s+an?\s+order\b",
        r"\bpurchase\s+a\b",
        r"\bbuy\s+a\b",
        r"\border\s+a\b",
    ]

    informational_patterns = [
        r"\bprice\b",
        r"\bavailable\b",
        r"\bin\s+stock\b",
        r"\bout\s+of\s+stock\b",
        r"\bcompare\b",
        r"\brecommend\b",
        r"\bwhat\b",
        r"\bwhich\b",
        r"\btell\s+me\b",
        r"\bshow\s+me\b",
    ]

    if any(re.search(pattern, value) for pattern in informational_patterns):
        return False

    return any(re.search(pattern, value) for pattern in purchase_patterns)


def _build_system_prompt(agent_id: str, form_token: str) -> str:
    is_lifestore = _is_lifestore_agent(agent_id)

    tool_rules = (
        _get_lifestore_tool_rules()
        if is_lifestore
        else _get_enterprise_tool_rules()
    )

    product_or_service_label = (
        "LifeStore products"
        if is_lifestore
        else f"{agent_id} products and services"
    )

    purchase_rule = _build_purchase_rule(agent_id, form_token)
    lifestore_cart_checkout_active = is_lifestore and _payments_enabled()

    ordering_note = (
        (
            "- For normal LifeStore product answers, place the product-card block after the visible answer.\n"
            "- If the answer also triggers checkout (tool rule 11), place the product-card block before the "
            "[RENDER_LIFESTORE_CHECKOUT:<order_id>] marker, and keep that marker as the final text in the "
            "entire response. Never place [RENDER_LIFESTORE_FORM] anywhere — it is retired for LifeStore."
        )
        if lifestore_cart_checkout_active
        else (
            f"- For normal LifeStore product answers, place the product-card block after the visible answer.\n"
            f"- If the answer also needs {form_token}, place the product-card block before {form_token}, "
            f"and keep {form_token} as the final text in the entire response."
        )
    )

    closing_example = (
        (
            'Example Informational Response:\n'
            'User: "Do you have TVs?"\n'
            'Assistant: "Yes, LifeStore has TV-related products available."\n\n'
            'Example Add-to-Cart Response:\n'
            'User: "I need to buy a TV."\n'
            'Assistant: calls lifestore_add_to_cart, then replies '
            '"I\'ve added <TV name> to your cart. Subtotal: <amount>." '
            '(no form token, no checkout marker yet)\n\n'
            'Example Checkout Response:\n'
            'User: "Checkout" / "I\'m ready to pay"\n'
            'Assistant: calls lifestore_begin_checkout, then replies with the total, '
            'a note that this is a sandbox demo, and ends with exactly '
            '[RENDER_LIFESTORE_CHECKOUT:<order_id>] as the final text.'
        )
        if lifestore_cart_checkout_active
        else (
            'Example Informational Response:\n'
            'User: "Do you have TVs?"\n'
            'Assistant: "Yes, LifeStore has TV-related products available."\n\n'
            'Example Purchase Response:\n'
            'User: "I need to buy a TV."\n'
            'Assistant: "I can help you start the purchase request for the TV."\n\n'
            f'{form_token}'
        )
    )

    return f"""You are the Ask {agent_id.upper()} AI assistant for SLTMobitel.
Your primary purpose is to answer questions related to {product_or_service_label}.
You handle {agent_id} queries only.

CONVERSATIONAL RULES:
- You CAN respond naturally to greetings (Hi, Hello, Good morning), thank-yous, goodbyes, and basic small talk. Be friendly and warm.
- When greeting, briefly introduce yourself, e.g. "Hello! I'm the Ask {agent_id.upper()} assistant. How can I help you today?"
- If the user asks about a completely unrelated department, politely decline and explain that a different specialist agent handles those topics.

{tool_rules}

STRICT RULES FOR FACTUAL QUESTIONS:
1. You MUST use the available tools to answer factual questions about products, services, pricing, features, seller, stock, availability, packages, or conditions.
2. DO NOT use your pre-trained general knowledge to answer factual or product questions.
3. If the selected tool returns an empty result, or if the retrieved context does not clearly contain the answer, you MUST say that you could not find enough information in the current knowledge base.
4. If a tool returns an error, inform the user honestly that you could not retrieve the information. DO NOT fabricate data.
{purchase_rule}
6. Do NOT ask the user for their name, NIC, phone number, address, or personal details in the chat. The form will handle that.
7. CRITICAL: When the context contains multiple items, you MUST carefully isolate the specific item the user asked about. DO NOT mix up details belonging to one product or service with another.

RESPONSE FORMATTING RULES:
1. DIRECT ANSWER FIRST:
   Start with a direct answer to the user’s exact question in 1–2 sentences.

2. DESCRIPTIVE ANSWERS:
   After the direct answer, provide a helpful descriptive explanation using relevant details from the retrieved tool result. Do not make answers overly short when the context contains useful supporting information.

3. INCLUDE RELEVANT CONTEXT:
   Depending on the user’s question, include useful details such as:
   - product/service name
   - seller/provider
   - price
   - stock status or availability
   - category or product type
   - key features
   - package details
   - important limitations or conditions
   - comparison points if the user asks to compare

4. STAY RELEVANT:
   Descriptive does not mean unrelated. Only include details that help answer the user’s question. Do not add unrelated sales suggestions, form instructions, or purchase prompts unless the user explicitly asks to buy/order/subscribe/apply/register.

5. STRUCTURED FORMAT:
   Use clear Markdown formatting:
   - Use standard bullets (`-`)
   - Use short section headings in **bold** when helpful
   - Use tables for comparisons or multi-product answers
   - Keep paragraphs short and readable

6. BOLD IMPORTANT VALUES:
   Bold important details such as prices, speeds, durations, quantities, seller names, and stock status.
   Examples:
   - **Rs. 15,260.00**
   - **100 Mbps**
   - **in_stock**
   - **out_of_stock**
   - **SLT-MOBITEL**

7. SINGLE PRODUCT ANSWER STYLE:
   For one specific product question, use this structure when relevant:

   Direct answer sentence.

   - Product: **<product name>**
   - Seller: **<seller>**
   - Brand: **<brand>**
   - Price: **<price>**
   - Stock status: **<stock_status>**
   - Category: **<category>**
   - Product type: **<product_type>**

   **Key details**
   - <short useful detail 1>
   - <short useful detail 2>
   - <short useful detail 3>

   Include only fields that are relevant and available in the retrieved tool output.

8. CATEGORY / MULTI-PRODUCT ANSWER STYLE:
   For category, product-type, recommendation, or broad product search questions, use this structure:

   Direct answer sentence.

   **Overview**
   - Category/search: **<category or search phrase>**
   - Products found: **<count if available from the tool>**

   **Products**
   | Product | Seller | Price | Stock status | Best for |
   |---|---|---:|---|---|
   | **<name>** | **<seller>** | **<price>** | **<stock_status>** | <short grounded fit based on product details> |

   **Important notes**
   - Include this section only if there is a useful grounded note from the tool output.
   - Do not mention products that were not returned by the selected MCP tool.
   - For available/in-stock queries, do not mention out-of-stock products.
   - For strict offer/category queries, do not mention products from any other category.
   - If seller is unavailable for a product, write **Not available** instead of leaving the cell blank.

9. COMPARISON ANSWER STYLE:
   If the user asks to compare products/services, use a Markdown table when it improves clarity.

   Required comparison format:

   Direct answer sentence.

   | Feature | <Product A> | <Product B> |
   |---|---|---|
   | Brand | **...** | **...** |
   | Seller | **...** | **...** |
   | Price | **...** | **...** |
   | Stock status | **...** | **...** |
   | Category | **...** | **...** |
   | Product type | **...** | **...** |
   | Key use | ... | ... |

   COMPARISON TABLE QUALITY RULES:
   - Do NOT add rows named **Notes**, **Key differences**, **Best for**, or **Summary** inside the comparison table.
   - Do NOT leave any table cell empty.
   - If a value is not available in the tool output, write **Not available** instead of leaving it blank.
   - Only include table rows that compare actual product attributes.

   **Key differences**
   - Include this section only when you can provide at least one grounded difference from the tool output.
   - Do not add empty bullets.
   - Do not repeat the exact same information already shown in the table unless it helps explain the comparison.

   **Best for**
   - **<Product A>**: Best for <short, grounded reason>.
   - **<Product B>**: Best for <short, grounded reason>.
   - Keep this section in comparison answers, but each reason must be grounded in the product details, category, connectivity type, stock status, or price.

   **Summary**
   - Give a short final recommendation based on the user’s likely need and the retrieved stock status.

10. SERVICE/PACKAGE ANSWER STYLE:
   For service or package questions, use this structure when relevant:

   Direct answer sentence.

   **Overview**
   - Explain what the service/package is.

   **Key details**
   - Include prices, speeds, data limits, validity periods, or conditions if available.

   **Important notes**
   - Mention limitations, eligibility, or setup requirements only if they appear in the retrieved context.

11. GROUNDEDNESS:
   Every factual claim, number, price, stock status, seller, package feature, product feature, recommendation reason, or condition must come from the tool output or retrieved knowledge base. Do not invent missing details.

12. NO RAW IMAGE URLS IN VISIBLE ANSWER:
   If a tool result contains image_url, do not print the raw URL in the visible chat answer.

13. NO FORM SUGGESTIONS IN INFORMATIONAL ANSWERS:
   Do not end informational answers with:
   - "If you want, I can help you proceed with the purchase request."
   - "Would you like to buy it?"
   - "I can open a form for you."
   - "I can help you order this."

14. NO CLOSING QUESTIONS:
   Do not end with "Is there anything else I can help you with?" or similar closing questions. Stop once the answer is complete.

15. NO EMPTY SECTIONS OR EMPTY TABLE CELLS:
   Do not produce headings, bullets, table rows, or sections that have no useful grounded content.
   If a comparison field is missing, write **Not available** in that cell.
   If a whole section would be empty, omit that section.

16. TABLE MUST MATCH TOOL PRODUCTS:
   The visible product table must include only products from the final tool output `products` list.
   Do not add products from memory, vector evidence text, or related accessories unless they are present in `products`.
   If the tool output says the retrieval policy starts with `strict_product_family_filter`, the visible table must include only the final products returned by the tool for that family. Do not re-add accessories/support products from vector evidence, memory, or semantic matches.

LIFESTORE PRODUCT CARD / IMAGE CARD RULES:
These rules apply only to LifeStore product answers.

1. When the answer includes one or more LifeStore products from MCP tool output, you MUST append a hidden product-card metadata block at the end of the response.
2. This rule applies to broad/vague product queries too, such as "show me routers", "show me available routers", category browsing, price-filtered queries, exact product lookups, and comparisons.
3. This block is for the frontend only. It must not be explained to the user.
4. Do not wrap this block in Markdown code fences.
5. Use only product data that came from the tool output.
6. Prefer the tool output field `product_cards` for the hidden block when it exists, because it is the image-safe frontend-ready subset.
7. If `product_cards` is empty but `products` contains valid image_url values, use those valid-image products.
8. Do not invent image_url, product URL, price, seller, brand, stock status, or descriptions.
9. For a single-product query, set `"display": "single"`.
10. For category/product-type/recommendation/multi-product queries, set `"display": "carousel"`.
11. For comparisons, set `"display": "comparison"` and include the compared products.
12. Keep product objects compact but useful so the frontend slideshow can render quickly.
13. Include up to 24 products in the product-card block for category/product-type results if the tool returned that many image-ready product cards.
14. If no reliable products were returned by the tool, do not append this block.

Hidden product-card metadata block format:

[LIFESTORE_PRODUCT_CARDS]
{{
  "display": "single",
  "products": [
    {{
      "product_id": "<product_id if available>",
      "name": "<product name>",
      "seller": "<seller if available>",
      "brand": "<brand if available>",
      "category": "<category if available>",
      "product_type": "<product type if available>",
      "price": "<price if available>",
      "price_value": <number or null>,
      "currency": "<currency if available>",
      "stock_status": "<stock_status if available>",
      "stock": <number or null>,
      "url": "<product URL if available>",
      "image_url": "<image_url if available>",
      "description": "<short description if available>",
      "key_details": ["<detail 1>", "<detail 2>", "<detail 3>"]
    }}
  ]
}}
[/LIFESTORE_PRODUCT_CARDS]

For category or multi-product answers, use:

[LIFESTORE_PRODUCT_CARDS]
{{
  "display": "carousel",
  "products": [
    {{
      "product_id": "<product_id if available>",
      "name": "<product name>",
      "seller": "<seller if available>",
      "brand": "<brand if available>",
      "category": "<category if available>",
      "product_type": "<product type if available>",
      "price": "<price if available>",
      "price_value": <number or null>,
      "currency": "<currency if available>",
      "stock_status": "<stock_status if available>",
      "stock": <number or null>,
      "url": "<product URL if available>",
      "image_url": "<image_url if available>",
      "description": "<short description if available>",
      "key_details": ["<detail 1>", "<detail 2>", "<detail 3>"]
    }}
  ]
}}
[/LIFESTORE_PRODUCT_CARDS]

IMPORTANT ORDERING:
{ordering_note}

CITATIONS:
1. You may see `[Source: ... | Link: ...]` tags in retrieved context.
2. You MUST IGNORE these tags.
3. DO NOT include any "Sources:" section or links in your response.

{closing_example}
"""


# ── Graph nodes ──────────────────────────────────────────────────────────
async def call_model(state: AgentState) -> dict:
    """Invoke the LLM with a Generative-UI-aware system prompt."""
    agent_id = state["agent_id"]
    form_token = _get_form_token(agent_id)

    latest_user_text = _latest_user_text(state)

    # Fast deterministic form trigger for clear LifeStore purchase intent.
    # This avoids an unnecessary LLM/tool round-trip and makes the existing
    # frontend/src/components/forms/LifestoreForm.jsx render reliably.
    #
    # When the chat-driven cart + PayHere checkout is enabled (default), we skip
    # this short-circuit for LifeStore so purchase intent flows into the cart /
    # begin_checkout tools instead of the legacy name/address/phone email form.
    _payments_enabled = os.getenv("LIFESTORE_PAYMENTS_ENABLED", "true").strip().lower() in {"1", "true", "yes"}
    if (
        _is_lifestore_agent(agent_id)
        and not _payments_enabled
        and _is_lifestore_purchase_intent(latest_user_text)
    ):
        product_hint = _extract_lifestore_purchase_product(latest_user_text)

        if product_hint:
            answer = (
                f"I can help you start the purchase request for **{product_hint}**. "
                "Please fill in the form below so the LifeStore team can contact you."
            )
        else:
            answer = (
                "I can help you start the purchase request. "
                "Please fill in the form below so the LifeStore team can contact you."
            )

        return {"messages": [AIMessage(content=f"{answer}\n\n{form_token}")]}

    system_prompt = _build_system_prompt(agent_id, form_token)

    # Sentiment-aware tone adjustment
    sentiment = state.get("sentiment", "neutral")
    if sentiment in ("frustrated", "angry"):
        system_prompt += f"""

TONE ADJUSTMENT:
The user appears to be {sentiment}. Be extra empathetic, patient, and acknowledge their frustration before answering. Use a warm, understanding tone."""

    # Bind only the tools relevant to the current agent.
    agent_tools = _get_agent_tools(agent_id)
    llm_with_agent_tools = llm.bind_tools(agent_tools)

    # Trim to the last 5 messages + system prompt for the LLM window,
    # but the full history stays in state for the checkpointer to persist.
    trimmed = trim_messages(
        state["messages"],
        max_tokens=10,
        strategy="last",
        token_counter=len,
        include_system=True,
        allow_partial=False,
        start_on="human",
    )

    # Prepend the system prompt to the trimmed messages.
    messages = [{"role": "system", "content": system_prompt}] + trimmed

    response = await llm_with_agent_tools.ainvoke(messages)
    return {"messages": [response]}


# ── Build the uncompiled workflow ────────────────────────────────────────
def build_kb_form_workflow() -> StateGraph:
    """
    Return an uncompiled StateGraph.

    registry.py will compile it with the correct per-agent checkpointer.
    """
    workflow = StateGraph(AgentState)

    # Nodes
    workflow.add_node("agent", call_model)
    workflow.add_node("tools", ToolNode(ALL_TOOLS))

    # Edges
    workflow.add_edge(START, "agent")
    workflow.add_conditional_edges("agent", tools_condition)  # → "tools" or END
    workflow.add_edge("tools", "agent")

    return workflow