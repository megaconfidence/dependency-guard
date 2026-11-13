"""Your side of the Slack approval, from the terminal: answer the agent's request, or count its old messages.

    uv run python scripts/slack.py approve <session-id>   # react ✅ to the session's request, as if you tapped it
    uv run python scripts/slack.py deny <session-id>      # react ❌
    uv run python scripts/slack.py leftovers              # count dependency-guard's messages still in your DM

It calls your Slack connector through the Mistral API, with MISTRAL_API_KEY (demo.sh uses the one in .env).
"""

import asyncio
import json
import os
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # approval.py and repos.py live in the project root

from mistralai import agents

import approval
from repos import APPROVER

AGENT_NAME = os.environ.get("AGENT_NAME", "dependency-guard")
TOOLS = "https://api.mistral.ai/v1/connectors/slack/tools"


def slack(tool: str, **arguments) -> dict:
    """Call a tool of your Slack connector, and return its reply."""
    response = httpx.post(
        f"{TOOLS}/{tool}/call",
        headers={"Authorization": f"Bearer {os.environ['MISTRAL_API_KEY']}"},
        json={"arguments": arguments},
        timeout=60,
    )
    response.raise_for_status()
    body = response.json()
    text = "".join(block.get("text", "") for block in body.get("content", []))
    if body.get("metadata", {}).get("mcp_meta", {}).get("isError"):
        sys.exit(f"Slack {tool} failed: {text}")
    return json.loads(text)


async def latest_request(session_id: str) -> tuple[str, str, str] | None:
    """The session's latest approval request so far: its channel, timestamp and first line."""
    session = agents.Agent.load_from(agent_name=AGENT_NAME).session(session_id)
    asked: dict[str, str] = {}
    request = None
    events = aiter(session.iter_events())
    while True:
        try:
            event = await asyncio.wait_for(anext(events), timeout=3)  # quiet for 3 s: we've caught up
        except (TimeoutError, StopAsyncIteration):
            return request
        match event:
            case agents.EntryCompleted(entry=agents.ToolCallEntry(name=approval.SEND, arguments=dict() as args) as call):
                if not args.get("thread_ts"):
                    asked[call.tool_call_id] = (str(args.get("message", "")).strip().splitlines() or [""])[0]
            case agents.EntryCompleted(entry=agents.ToolResultEntry(tool_call_id=call_id) as result) if call_id in asked:
                if sent := approval.sent_message(result):
                    request = (*sent, asked[call_id])


def answer(session_id: str, emoji: str) -> None:
    request = asyncio.run(latest_request(session_id))
    if not request:
        sys.exit("It hasn't asked yet: try again once it messages you in Slack")
    channel, ts, first_line = request
    slack("slack_add_reaction", channel_id=channel, message_ts=ts, emoji=emoji)
    print(f"Reacted :{emoji}: to its request ({first_line}). It checks every 20 seconds.")


def leftovers() -> None:
    history = slack("slack_read_channel", channel_id=APPROVER, limit=100)
    count = str(history.get("messages", "")).count("*dependency-guard*")
    if count:
        messages, them = ("message", "it") if count == 1 else ("messages", "them")
        print(f"  Slack: {count} dependency-guard {messages} still in your DM: delete {them} before filming"
              " (the connector can't)")
    else:
        print("  Slack: no dependency-guard messages in your DM")


if __name__ == "__main__":
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    if command in ("approve", "deny") and len(sys.argv) == 3:
        answer(sys.argv[2], approval.APPROVE if command == "approve" else approval.DENY)
    elif command == "leftovers" and len(sys.argv) == 2:
        leftovers()
    else:
        sys.exit(__doc__)
