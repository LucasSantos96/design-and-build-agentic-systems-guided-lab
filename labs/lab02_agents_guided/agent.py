import asyncio
from pathlib import Path

from agents import Agent, Runner

# Load environment variables from a local `.env` (if present) so the Agents SDK
# can pick up OPENAI_API_KEY without manual exports.
from dotenv import load_dotenv

from .tools import (
    FAQ_retrieval,
    cancel_order,
    check_payment_methods,
    lookup_order,
    raise_complaint,
)

load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=True)

from agents import Agent, Runner
from .tools import check_payment_methods

"""
Baseline single-agent exercise for the Agents Lab.

Read the end-to-end instructions in `labs/lab02_agents_guided/README.md`.
This file focuses on a minimal baseline agent and a tiny harness for manual checks.

TODO (implement in this exercise):
- Update and validate tools in `tools.py` so the agent can answer realistic questions
  (order lookup/cancellation, complaints, payment methods, and FAQs).
- Refine the system prompt so the agent asks for missing information, calls one tool at a time,
  and returns concise, user-friendly answers.
- Register the full tool set in `build_baseline_agent()` once implemented.
- Keep `run_once()` single-turn and simple.
- (Optional) Add session persistence with `SQLiteSession` for short multi-turn memory.

If you are stuck, review `agent_solution.py` only as a reference. Do not copy it directly.
You can review traces at https://platform.openai.com/logs/trace while iterating.
"""

# Human-readable agent name used throughout this baseline harness.
NAME = "Baseline Support Agent"

BASELINE_SYSTEM_PROMPT = """\
You are a customer-support assistant for order-related requests.

You have access to the following tools:
- lookup_order: Look up details for a specific order by its order number.
- complaint: Start a complaint intake process for the customer.
- contact_customer_service: Provide customer service contact information.
- check_payment_methods: List the accepted payment methods.
- FAQ_retrieval: Look up an FAQ question and answer pair.

Guidelines:
- Always ask for any missing required information (such as Order Number) before calling a tool.
- Call exactly one tool at a time.
- Keep your answers concise and user-friendly.
- Never list the tools you have access to in your response.
"""

def build_baseline_agent() -> Agent:
    """
    Construct the baseline support agent with a minimal tool set.

    Notes for the exercise:
    - Start with a minimal set (e.g., `check_payment_methods`).
    - After implementing the rest of the tools in `tools.py`, register them here
      (e.g., lookup_order, cancel_order, raise_complaint, FAQ_retrieval).
    - Keep the system prompt concise and focused on asking for missing info,
      calling one tool at a time, and returning short final answers.
    """
    agent = Agent(
    name=NAME,
    instructions=BASELINE_SYSTEM_PROMPT,
    tools=[lookup_order, raise_complaint, check_payment_methods, cancel_order, FAQ_retrieval],
    # Optional: model_config can be set here if you wish to override defaults.
    # model_config=...
)
    return agent
    


async def run_once(agent: Agent, user_input: str) -> str:
    """
    Run a single-turn agent interaction and return the final output string.
    """
    result = await Runner.run(agent, user_input)
    return str(result.final_output)


if __name__ == "__main__":
    async def _main():
        # Example test harness for the agent using the run_once function.
        # This demonstrates how to interact with the agent for five different user queries.

        questions = [
            "What are your customer service hours?",
            "I need to raise a complaint, as I am not happy with my order.",
            "I need to understand my order status, the order number is 123-456.",
            "I need to understand my order status, the order number is A123-456.",
            "I need to cancel my order, the order number is A123-456.",
            "What is the status of my order, the order number is A123-456.",
            "I need to know how to pay for my order.",
        ]

        # Build the agent instance
        agent = build_baseline_agent()

        for question in questions:
            print("-" * 40)
            print(f"User > {question}")
            try:
                out = await run_once(agent, question)
                print("Agent >", out)
            except Exception as e:
                # Friendly error surface for the baseline harness.
                # Tools are expected to raise ValueError on invalid/missing inputs during the exercise.
                print(f"Error > {e}")

    asyncio.run(_main())
