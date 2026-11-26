"""Tool invocation over REST, including the OpenAI-compatible tool-calling shape."""

import json

from fastapi import APIRouter, Body, Depends
from pydantic import BaseModel

from ..errors import BridgeError
from ..services import Services
from ..tools import REGISTRY
from ..tools.base import Actor
from .deps import current_actor, get_services

router = APIRouter(prefix="/api/v1")


def tool_catalog() -> list[dict]:
    return [
        {"name": spec.name, "description": spec.description, "input_schema": spec.input_schema, "read_only": spec.read_only}
        for spec in REGISTRY.values()
    ]


@router.get("/tools")
async def list_tools(_: Actor = Depends(current_actor)):
    return {"tools": tool_catalog()}


@router.post("/tools/{name}")
async def call_tool(name: str, arguments: dict = Body(default_factory=dict), actor: Actor = Depends(current_actor),
                    services: Services = Depends(get_services)):
    return await services.gateway.call(actor, name, arguments)


@router.get("/openai/tools")
async def openai_tools(_: Actor = Depends(current_actor)):
    """Definitions to pass as `tools` to any OpenAI-compatible chat completions API."""
    return {
        "tools": [
            {"type": "function",
             "function": {"name": t["name"], "description": t["description"], "parameters": t["input_schema"]}}
            for t in tool_catalog()
        ]
    }


class FunctionCall(BaseModel):
    name: str
    arguments: str = "{}"


class ToolCall(BaseModel):
    id: str
    type: str = "function"
    function: FunctionCall


class ToolCalls(BaseModel):
    tool_calls: list[ToolCall]


@router.post("/openai/tool_calls")
async def openai_tool_calls(body: ToolCalls, actor: Actor = Depends(current_actor),
                            services: Services = Depends(get_services)):
    """Execute a model's `tool_calls` in order and return the matching `tool` messages."""
    messages = []
    for call in body.tool_calls[:32]:
        try:
            arguments = json.loads(call.function.arguments or "{}")
            if not isinstance(arguments, dict):
                raise ValueError("arguments must be a JSON object")
            result = await services.gateway.call(actor, call.function.name, arguments)
        except BridgeError as exc:
            result = exc.to_dict()
        except ValueError as exc:
            result = {"error": "invalid_input", "message": str(exc)}
        messages.append({"role": "tool", "tool_call_id": call.id, "content": json.dumps(result, ensure_ascii=False)})
    return {"messages": messages}
