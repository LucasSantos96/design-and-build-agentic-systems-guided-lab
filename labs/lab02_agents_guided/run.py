import argparse
import asyncio
import json
import os
from typing import Any, Dict, List
import uuid
import importlib

# Load environment variables from a local `.env` (if present) before importing
# libraries that may read API keys from the environment.
from dotenv import load_dotenv
from pathlib import Path

load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=True)

from agents import Runner
from agents.exceptions import InputGuardrailTripwireTriggered
from agents.run import RunConfig

from agents.items import (
    MessageOutputItem, ToolCallItem, ToolCallOutputItem,
    HandoffCallItem, HandoffOutputItem, ItemHelpers
)

def write_agent_model_answers(records: List[Dict[str, Any]], output_path: str) -> None:
    """
    Write agent run results to a JSONL file compatible with RAG eval format.

    Each record is persisted as a JSON line with the following shape:
        {
            "item": {
                "input": str,
                "expected_answer": Optional[str],
                "expected_tool": Optional[str],
                "expected_category": Optional[str],
                "model_answer": str,
                "model_toolcall": str
            }
        }

    Notes
    -----
    - The shape mirrors `labs/data/rag_model_answer.jsonl` with one addition:
      the extra key `model_toolcall` capturing the model's chosen tool(s) and/or handoff(s).
    - `model_toolcall` is a string summarizing the sequence of all tool calls and handoffs
      observed for the question, joined with " -> ". If none were observed, the value is "none".
    - This function attempts to extract all tool calls and handoffs from each record, supporting
      both a precomputed "model_toolcall" field or, if not present, by inspecting a "trace" or
      similar field if available.

    Parameters
    ----------
    records: List[Dict[str, Any]]
        A list of dicts, each representing a run result. Each dict should contain at least the keys
        shown above under the "item" object. If a "trace" or "steps" field is present, it will be
        used to reconstruct the model_toolcall string if not already provided.
    output_path: str
        Destination path for the JSONL file. The file will be created or overwritten.
    """
    # Ensure parent directory exists before writing
    parent_dir = os.path.dirname(os.path.abspath(output_path))
    if parent_dir and not os.path.exists(parent_dir):
        os.makedirs(parent_dir, exist_ok=True)

    def extract_model_toolcall(rec: Dict[str, Any]) -> str:
        """
        Extracts a string summarizing all tool calls and handoffs for a record.
        If 'model_toolcall' is present, uses it. Otherwise, attempts to reconstruct
        from a 'trace' or 'steps' field if available.
        """
        # If already present, use it
        if "model_toolcall" in rec and rec["model_toolcall"]:
            return rec["model_toolcall"]

        # Attempt to reconstruct from a trace or steps field
        toolcall_sequence = []

        # Common field names for traces/steps
        trace = rec.get("trace") or rec.get("steps") or []

        # If trace is a string, try to parse as JSON
        if isinstance(trace, str):
            try:
                trace = json.loads(trace)
            except Exception:
                trace = []

        # If trace is a list, iterate and extract tool/handoff names
        if isinstance(trace, list):
            for step in trace:
                # Tool call: look for a 'tool_name' or 'tool' key
                tool_name = step.get("tool_name") or step.get("tool")
                if tool_name:
                    toolcall_sequence.append(str(tool_name))
                # Handoff: look for a 'handoff_to' or 'handoff' key
                handoff_name = step.get("handoff_to") or step.get("handoff")
                if handoff_name:
                    toolcall_sequence.append(f"handoff:{handoff_name}")

        # If nothing found, return "none"
        if not toolcall_sequence:
            return "none"
        return " -> ".join(toolcall_sequence)

    try:
        with open(output_path, "w", encoding="utf-8") as f:
            for rec in records:
                # Defensive copy and structure enforcement
                item = {
                    "input": rec.get("input", ""),
                    "expected_answer": rec.get("expected_answer"),
                    "expected_tool": rec.get("expected_tool"),
                    "expected_category": rec.get("expected_category"),
                    "model_answer": rec.get("model_answer", ""),
                    # Always extract all tool calls and handoffs for this question
                    "model_toolcall": extract_model_toolcall(rec),
                }
                f.write(json.dumps({"item": item}, ensure_ascii=False) + "\n")
    except Exception as e:
        # Fail gracefully but surface context for debugging
        print(f"Error while writing agent model answers JSONL to {output_path}: {e}")

def _load_router_module(use_solution: bool):
    """Dynamically load the routers module.

    If use_solution is True, load `.router_solution`; otherwise load `.router`.
    This lets us keep a single runner and switch implementations at runtime.
    """
    module_name = ".router_solution" if use_solution else ".router"
    return importlib.import_module(module_name, package=__package__)

def load_tests(path: str) -> List[Dict[str, Any]]:
    """
    Read tests as JSONL, where each line is:
      {"item": {"input": str, "expected_answer": str, "expected_tool": str, "expected_category": str, ...}}
    Returns a list of dicts with at least "user" and "expected_tool" keys for each test.
    This is designed to work with the shape of labs/data/sample_02.jsonl.
    """
    out: List[Dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                item = data.get("item", {})
                if not isinstance(item, dict):
                    continue
                test_case = {
                    "user": item.get("input", ""),
                    "expected_tool": item.get("expected_tool", None),
                    "expected_answer": item.get("expected_answer", None),
                    "expected_category": item.get("expected_category", None),
                }
                out.append(test_case)
            except Exception as e:
                print(f"Warning: Skipping malformed line in {path}: {e}")
                continue
    return out

def build_agent_for_variant(variant: str, routers_module):
    """Construct agent(s) for the selected variant using the given routers module."""
    if variant.upper() == "TRIAGE":
        return routers_module.build_triage_routed_agents()
    if variant.upper() == "AGENT_AS_TOOL":
        return routers_module.build_handoff_tool_pattern()
    if variant.upper() in ("GUARDRAIL"):
        return routers_module.build_guardrailed_triage_agent_with_guardrails()

    raise ValueError("Unknown variant; Use TRIAGE or AGENT_AS_TOOL for variant")

async def run_suite(variant: str, tests_path: str, routers_module) -> None:
    """
    Run the agent on a suite of test cases and print results to stdout.
    This function does not perform metrics, tracking, or artifact generation.
    """
    agent = build_agent_for_variant(variant, routers_module)
    tests = load_tests(tests_path)

    # Default trace naming to help locate traces in a shared org.
    # Prefer runtime flag, then module-level TODO, then a safe fallback.

    workflow_name = f"Agents Challenge Variant {variant}"
    # Generate a unique UUID for this run to group related traces together.
    group_id = str(uuid.uuid4())

    run_config = RunConfig(
        workflow_name=workflow_name,
        group_id=group_id,
    )

    print(f"\n Find Agent Traces for workflow_name: {workflow_name} with Group ID: {group_id} at https://platform.openai.com/logs/trace")

    # Accumulate results for JSONL persistence after the loop
    jsonl_records: List[Dict[str, Any]] = []

    for idx, t in enumerate(tests, start=1):
        user = t.get("user", "")

        print("\n" + "-" * 40)
        print(f"Example {idx}")
        print(f"User > {user}")

        # Placeholders for persistence
        model_answer_text: str = ""
        toolcall_parts: List[str] = []

        try:
            result = await Runner.run(agent, user, max_turns=4, run_config=run_config)

            for item in getattr(result, "new_items", []):
                agent_name = getattr(item.agent, "name", "Agent") if hasattr(item, "agent") else "Agent"
                if isinstance(item, MessageOutputItem):
                    text = ItemHelpers.text_message_output(item) or ""
                    print(f"[OUTPUT] {agent_name} > {text}")
                    # Capture the latest assistant-visible message as the model's final answer
                    if text:
                        model_answer_text = text
                elif isinstance(item, ToolCallItem):
                    name = getattr(getattr(item, "raw_item", None), "name", "tool")
                    args = getattr(getattr(item, "raw_item", None), "arguments", None)
                    print(f"[TOOLCALL] {agent_name} > {name} args={args}")
                    # Track tool calls for the JSONL summary
                    if name:
                        toolcall_parts.append(str(name))
                elif isinstance(item, ToolCallOutputItem):
                    print(f"[TOOL RESULT] > {item.output}")
                elif isinstance(item, HandoffCallItem):
                    target = getattr(getattr(item, "raw_item", None), "name", "handoff")
                    print(f"[HANDOFF REQUEST] {agent_name} is requesting to hand off to agent: {target}")
                    # Represent handoffs in the toolcall summary
                    if target:
                        toolcall_parts.append(f"handoff:{target}")
                elif isinstance(item, HandoffOutputItem):
                    src = item.source_agent.name if getattr(item, "source_agent", None) else "Unknown"
                    dst = item.target_agent.name if getattr(item, "target_agent", None) else "Unknown"
                    print(f"[HANDOFF COMPLETE] Conversation control transferred from {src} to {dst}")
                # Skip ReasoningItem to keep output clean

            # Handle guardrail and general exceptions
        except InputGuardrailTripwireTriggered:
            print("[GUARDRAIL]- TRIPWIRE TRIGGERED")
            # Persist a meaningful placeholder for guardrail-triggered interactions
            model_answer_text = "[GUARDRAIL] Tripwire triggered"
            toolcall_parts.append("guardrail_tripwire")
        except Exception as e:
            print(f"Error > {e}")
            # Persist error context to help with post-run analysis
            model_answer_text = f"[ERROR] {e}"
            toolcall_parts.append("error")

        # Build JSONL record for this example
        jsonl_records.append({
            "input": user,
            "expected_tool": t.get("expected_tool"),
            "expected_answer": t.get("expected_answer"),
            "expected_category": t.get("expected_category"),
            "model_answer": model_answer_text,
            # Join observed steps as a single string for compactness, default to "none"
            "model_toolcall": " -> ".join(toolcall_parts) if toolcall_parts else "none",
        })

    # After running all examples, persist the results to labs/data/agent_model_answer.jsonl
    default_output_path = os.path.normpath(os.path.join(os.path.dirname(__file__), "../data/agent_model_answer.jsonl"))
    write_agent_model_answers(jsonl_records, default_output_path)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--variant", required=True, choices=["TRIAGE", "GUARDRAIL", "AGENT_AS_TOOL"])
    parser.add_argument(
        "--solution",
        action="store_true",
        help="Use solution routers (.router_solution). Default uses exercise routers (.router).",
    )
    args = parser.parse_args()

    # Set the default path to sample_02.jsonl for test data
    default_tests_path = os.path.join(os.path.dirname(__file__), "../data/sample_02.jsonl")

    routers_module = _load_router_module(use_solution=args.solution)

    asyncio.run(run_suite(
        args.variant,
        tests_path=default_tests_path,
        routers_module=routers_module,
    ))

if __name__ == "__main__":
    main()
