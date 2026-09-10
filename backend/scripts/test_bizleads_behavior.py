import asyncio
import logging
import sys
import os
from unittest.mock import AsyncMock, patch
import httpx

# Add backend directory to Python path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# Configure logging to print to console
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s | %(message)s",
    stream=sys.stdout
)

# Import the service function to test
from services.bizleads import submit_bizlead

async def run_scenario(name, mock_status_code, mock_body_text, simulate_exception=False):
    print(f"\n==========================================")
    print(f" SCENARIO: {name}")
    print(f"==========================================")
    
    mock_response = httpx.Response(
        status_code=mock_status_code,
        content=mock_body_text.encode('utf-8') if mock_body_text is not None else b""
    )
    
    # We patch the post method of httpx.AsyncClient
    if simulate_exception:
        mock_post = AsyncMock(side_effect=httpx.ConnectError("Connection timed out"))
    else:
        mock_post = AsyncMock(return_value=mock_response)
        
    with patch("httpx.AsyncClient.post", mock_post):
        await submit_bizlead(
            name="Test User",
            phone="0711234567",
            email="test@example.com",
            city="Colombo",
            product="slt-fiber",
            note="Test note details"
        )
        
        # Verify the post parameters
        if mock_post.called:
            args, kwargs = mock_post.call_args
            print("Payload sent to BizLeads:")
            for k, v in kwargs.get("data", {}).items():
                print(f"  {k}: {v}")

async def main():
    # 1. Success Scenario (returns empty body or standard text, status code 200)
    await run_scenario(
        name="Successful submission (API returns empty body)",
        mock_status_code=200,
        mock_body_text=""
    )

    # 2. Duplicate Entry Scenario (API returns "0")
    await run_scenario(
        name="Duplicate entry (API returns '0')",
        mock_status_code=200,
        mock_body_text="0"
    )

    # 3. HTTP Error Scenario (API returns non-200)
    await run_scenario(
        name="HTTP error (API returns status code 500)",
        mock_status_code=500,
        mock_body_text="Internal Server Error"
    )

    # 4. Network Failure Scenario (Connection/Timeout Exception)
    await run_scenario(
        name="Network failure / Exception thrown",
        mock_status_code=200,
        mock_body_text="",
        simulate_exception=True
    )

if __name__ == "__main__":
    asyncio.run(main())
