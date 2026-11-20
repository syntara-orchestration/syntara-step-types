"""Run the agent decision loop by calling SDK host operations."""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from typing import Any

from syntara_plugin.runtime import ActionStep, ExecutionContext

from ._models import AgentRuntimeInput, AgentRuntimeOutput

_MAX_TOTAL_TOOL_CALLS = 32
_MAX_TRACE_INPUT_CHARS = 2_048
_MAX_TRACE_TOOL_OUTPUT_CHARS = 5_000


def _trace_tool_input(arguments: dict[str, Any]) -> dict[str, Any]:
    """Keep persisted traces bounded while the full arguments go to AO."""
    encoded = json.dumps(arguments, ensure_ascii=False, separators=(",", ":"))
    if len(encoded) <= _MAX_TRACE_INPUT_CHARS:
        return arguments
    return {"truncated": True, "preview": encoded[:_MAX_TRACE_INPUT_CHARS]}


def _execute_tool_call(
    context: ExecutionContext,
    call: Any,
    turn_index: int,
    messages: list[dict[str, Any]],
    tools_used: list[str],
    tool_trace: list[dict[str, Any]],
) -> None:
    """Validate and execute one model-issued, AO-authorized MCP call."""
    if not isinstance(call, dict):
        raise RuntimeError("AO returned an invalid model tool request.")
    call_id = call.get("tool_call_id")
    handle = call.get("handle")
    arguments = call.get("arguments")
    name = call.get("name")
    if (
        not isinstance(call_id, str)
        or not call_id
        or not isinstance(handle, str)
        or not handle
        or not isinstance(name, str)
        or not name
    ):
        raise RuntimeError("AO returned an invalid model tool request.")
    if not isinstance(arguments, dict):
        raise RuntimeError("AO returned invalid tool arguments.")

    tool_trace.append(
        {
            "type": "tool_call",
            "tool_name": name,
            "tool_input": _trace_tool_input(arguments),
            "call_id": call_id,
            "content": f"Calling {name}",
            "timestamp": datetime.now(UTC).isoformat(),
        }
    )
    tool_started_at = time.perf_counter()
    result = context.toolbox.call_mcp_tool(
        {
            "turn_index": turn_index,
            "tool_call_id": call_id,
            "handle": handle,
            "arguments": arguments,
        }
    )
    result_content = result.get("content", "")
    messages.append(
        {
            "role": "tool",
            "tool_call_id": call_id,
            "name": name,
            "content": result_content,
            "is_error": bool(result.get("is_error", False)),
        }
    )
    tools_used.append(name)
    tool_output = str(result_content)
    tool_trace.append(
        {
            "type": "tool_result",
            "tool_name": name,
            "tool_output": tool_output[:_MAX_TRACE_TOOL_OUTPUT_CHARS],
            "status": "failed" if result.get("is_error") else "success",
            "call_id": call_id,
            "content": tool_output[:200],
            "timestamp": datetime.now(UTC).isoformat(),
            "duration_ms": int((time.perf_counter() - tool_started_at) * 1000),
        }
    )


def _execute_tool_calls(
    context: ExecutionContext,
    response: dict[str, Any],
    turn_index: int,
    model_started_at: float,
    messages: list[dict[str, Any]],
    tools_used: list[str],
    tool_trace: list[dict[str, Any]],
) -> bool:
    """Execute all tool calls from a model turn and report whether any existed."""
    tool_calls = response.get("tool_calls", [])
    if not isinstance(tool_calls, list):
        raise RuntimeError("AO returned an invalid model response.")
    if not tool_calls:
        return False
    if len(tools_used) + len(tool_calls) > _MAX_TOTAL_TOOL_CALLS:
        raise RuntimeError("Agent tool call limit was reached.")

    reasoning = response.get("content")
    if isinstance(reasoning, str) and reasoning.strip():
        tool_trace.append(
            {
                "type": "reasoning",
                "timestamp": datetime.now(UTC).isoformat(),
                "content": reasoning[:_MAX_TRACE_TOOL_OUTPUT_CHARS],
                "duration_ms": int((time.perf_counter() - model_started_at) * 1000),
            }
        )
    for call in tool_calls:
        _execute_tool_call(context, call, turn_index, messages, tools_used, tool_trace)
    return True


def _record_usage(response: dict[str, Any], usage_log: list[dict[str, Any]]) -> None:
    """Append a model usage record when the provider returned one."""
    usage = response.get("usage")
    if isinstance(usage, dict):
        usage_log.append(usage)


def _extract_structured_output(
    context: ExecutionContext,
    inputs: AgentRuntimeInput,
    content: Any,
    turn_index: int,
    usage_log: list[dict[str, Any]],
    previous_response_metadata: dict[str, Any],
) -> tuple[Any, Any, dict[str, Any], int]:
    """Ask the host for a schema-only extraction after a tool-enabled turn."""
    response_schema = inputs.response_schema
    if response_schema is None:
        raise RuntimeError("Structured extraction requires a response schema.")
    if turn_index >= inputs.max_model_calls:
        raise RuntimeError("Agent model call limit was reached before structured extraction.")
    extraction_messages = [
        {
            "role": "system",
            "content": (
                "Extract and format the following information into this exact JSON schema:\n\n"
                f"```json\n{json.dumps(response_schema, indent=2)}\n```\n\n"
                "Use exactly the property names from the schema. "
                "Do not include any text outside the JSON object."
            ),
        },
        {"role": "user", "content": f"Extract from this text:\n\n{content}"},
    ]
    turn_index += 1
    extracted = context.toolbox.model_turn(
        {
            "turn_index": turn_index,
            "messages": extraction_messages,
            "tools": [],
            "mode": "structured_extract",
            "response_schema": response_schema,
        }
    )
    _record_usage(extracted, usage_log)
    response_metadata = extracted.get("response_metadata")
    final_response_metadata = (
        response_metadata if isinstance(response_metadata, dict) else previous_response_metadata
    )
    parsed_output = extracted.get("parsed_output")
    if parsed_output is not None:
        return (
            parsed_output,
            extracted.get("structured_output_metadata"),
            final_response_metadata,
            turn_index,
        )
    return content, {"fallback_strategy_used": "none"}, final_response_metadata, turn_index


def _fallback_from_direct_structured_output(
    context: ExecutionContext,
    inputs: AgentRuntimeInput,
    messages: list[dict[str, Any]],
    turn_index: int,
    usage_log: list[dict[str, Any]],
) -> tuple[Any, Any, dict[str, Any], int]:
    """Retry an unparsed direct structured response as a standard model turn."""
    if turn_index >= inputs.max_model_calls:
        raise RuntimeError("Agent model call limit was reached before structured output fallback.")
    turn_index += 1
    response = context.toolbox.model_turn(
        {
            "turn_index": turn_index,
            "messages": messages,
            "tools": [],
            "mode": "standard",
            "response_schema": None,
        }
    )
    _record_usage(response, usage_log)
    response_metadata = response.get("response_metadata")
    final_response_metadata = response_metadata if isinstance(response_metadata, dict) else {}
    return (
        response.get("content", ""),
        response.get("structured_output_metadata"),
        final_response_metadata,
        turn_index,
    )


def _finalize_response(
    context: ExecutionContext,
    inputs: AgentRuntimeInput,
    response: dict[str, Any],
    mode: str,
    messages: list[dict[str, Any]],
    turn_index: int,
    usage_log: list[dict[str, Any]],
) -> tuple[Any, Any, dict[str, Any], int]:
    """Resolve structured output behavior and return the final model content."""
    content = response.get("parsed_output")
    if content is None:
        content = response.get("content", "")
    metadata = response.get("structured_output_metadata")
    response_metadata = response.get("response_metadata")
    final_response_metadata = response_metadata if isinstance(response_metadata, dict) else {}

    if inputs.response_schema is not None and mode == "standard":
        return _extract_structured_output(
            context, inputs, content, turn_index, usage_log, final_response_metadata
        )
    if (
        inputs.response_schema is not None
        and mode == "structured_direct"
        and response.get("parsed_output") is None
    ):
        return _fallback_from_direct_structured_output(
            context, inputs, messages, turn_index, usage_log
        )
    return content, metadata, final_response_metadata, turn_index


class AgentRuntimeStep(ActionStep[AgentRuntimeInput, AgentRuntimeOutput]):
    """Run model turns and authorized MCP calls within one workload invocation."""

    def run(self, inputs: AgentRuntimeInput, context: ExecutionContext) -> AgentRuntimeOutput:
        """Advance the conversation until the model returns a final answer."""
        messages = [dict(message) for message in inputs.messages]
        tools = [dict(tool) for tool in inputs.tools]
        turn_index = 0
        response: dict[str, Any] = {}
        final_response_metadata: dict[str, Any] = {}
        metadata: Any = None
        tool_trace: list[dict[str, Any]] = []
        usage_log: list[dict[str, Any]] = []
        tools_used: list[str] = []
        started_at = time.perf_counter()

        while turn_index < inputs.max_model_calls:
            if context.cancelled:
                raise RuntimeError("Agent invocation was cancelled.")
            turn_index += 1
            mode = "standard"
            if inputs.response_schema is not None and not tools and turn_index == 1:
                mode = "structured_direct"
            model_started_at = time.perf_counter()
            response = context.toolbox.model_turn(
                {
                    "turn_index": turn_index,
                    "messages": messages,
                    "tools": tools,
                    "mode": mode,
                    "response_schema": inputs.response_schema,
                }
            )
            raw_metadata = response.get("response_metadata")
            final_response_metadata = raw_metadata if isinstance(raw_metadata, dict) else {}
            _record_usage(response, usage_log)
            assistant_message = response.get("assistant_message")
            if isinstance(assistant_message, dict) and (
                mode == "standard" or response.get("parsed_output") is not None
            ):
                messages.append(assistant_message)

            has_tool_calls = _execute_tool_calls(
                context,
                response,
                turn_index,
                model_started_at,
                messages,
                tools_used,
                tool_trace,
            )
            if has_tool_calls:
                continue

            content, metadata, final_response_metadata, turn_index = _finalize_response(
                context, inputs, response, mode, messages, turn_index, usage_log
            )
            if not isinstance(content, (str, dict)):
                raise RuntimeError("AO returned an invalid final response.")
            return AgentRuntimeOutput(
                content=content,
                last_turn_index=turn_index,
                total_duration_ms=int((time.perf_counter() - started_at) * 1000),
                response_metadata=final_response_metadata,
                structured_output_metadata=metadata,
                tools_used=tools_used,
                tool_trace=tool_trace,
                llm_token_usage_log=usage_log,
            )

        raise RuntimeError("Agent model call limit was reached before a final answer.")
