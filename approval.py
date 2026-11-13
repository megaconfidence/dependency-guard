"""Publishing waits for a person's ✅ in Slack.

The agent asks with the Slack connector: it messages the approver, then reads the message's reactions until they react
✅ (open the pull request) or ❌ (drop it). The connector posts from your own Slack account, so there are no buttons:
reactions are the answer. This guardrail makes asking the only way to publish, in code:

- slack_send_message only goes to the approver, and a reply only goes in the request's thread.
- slack_get_reactions only reads the request, until the wait is over.
- Every other Slack tool is refused: the connector acts as you, so slack_add_reaction could approve for you.
- `git push` and `gh pr create` are refused until the approver's ✅, and each ✅ opens one pull request.

Like the permission policy, it matches commands: it stops the agent's way of publishing, not a push hidden in a script.
"""

import json
import re
from datetime import datetime, timedelta
from typing import Any

from mistralai import agents

SEND = "connector_slack.slack_send_message"
REACTIONS = "connector_slack.slack_get_reactions"
APPROVE, DENY = "white_check_mark", "x"  # ✅ and ❌

# Pushing (also `git -C repo push`), and opening a pull request (also `gh pr -R owner/name create`).
OPTIONS = r"(\s+-\S+(\s+[^-\s]\S*)?)*"
PUSH = re.compile(rf"\bgit{OPTIONS}\s+push\b")
OPEN_PR = re.compile(rf"\bgh\s+pr{OPTIONS}\s+create\b")


def publishes(command: str) -> bool:
    return bool(PUSH.search(command) or OPEN_PR.search(command))


def reply(result: Any) -> dict:
    """A Slack tool's reply, from a ToolResult (in a hook) or a ToolResultEntry (in a session's history)."""
    if isinstance(result.structured_content, dict):
        return result.structured_content
    for block in result.content:
        try:
            parsed = json.loads(getattr(block, "text", None) or "")
        except ValueError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return {}


def sent_message(result: Any) -> tuple[str, str] | None:
    """The channel and timestamp of the message slack_send_message sent."""
    context = reply(result).get("message_context") or {}
    if context.get("channel_id") and context.get("message_ts"):
        return context["channel_id"], context["message_ts"]
    return None


def reactions(result: Any) -> dict[str, set[str]]:
    """Who reacted with what, from slack_get_reactions: ":white_check_mark: × 1 — confidence (U0BUM44636U)"."""
    found: dict[str, set[str]] = {}
    for line in str(reply(result).get("result", "")).splitlines():
        if emoji := re.match(r"\s*:([^:\s]+):", line):
            found[emoji[1]] = set(re.findall(r"\(([UW][A-Z0-9]+)\)", line))
    return found


class SlackApproval(agents.Hook):
    """Publishing waits for the approver's ✅ on the agent's Slack request."""

    approver: str  # the Slack member ID whose ✅ counts
    wait_minutes: int = 15

    channel: str | None = None  # the DM the request went to
    request_ts: str | None = None  # the request: the latest message sent outside a thread
    requested_at: datetime | None = None
    approved: bool = False  # the approver reacted ✅ to the request
    denied: bool = False  # they reacted ❌
    opened: bool = False  # the request's pull request is open: anything more needs a new request

    async def pre_tool_call(self, ctx, call):
        args = call.arguments if isinstance(call.arguments, dict) else {}
        if call.tool_id == SEND:
            return self.pin_message(args)
        if call.tool_id == REACTIONS:
            return self.pin_reactions()
        if call.tool_id.startswith("connector_slack."):
            return agents.skip("Only slack_send_message and slack_get_reactions are allowed: use them to ask the approver.")
        if call.tool_id == "file_system.bash" and publishes(str(args.get("command", ""))) and not self.approved:
            return agents.skip(self.why_not())
        return None

    async def post_tool_call(self, ctx, result):
        args = result.arguments if isinstance(result.arguments, dict) else {}
        if result.tool_id == "file_system.bash" and OPEN_PR.search(str(args.get("command", ""))):
            if reply(result.tool_result).get("returncode") == 0:
                self.opened, self.approved = True, False  # each ✅ opens one pull request
        elif result.tool_result.is_error:
            return None
        elif result.tool_id == SEND and not args.get("thread_ts") and (sent := sent_message(result.tool_result)):
            self.channel, self.request_ts = sent  # a new request, which needs a new answer
            self.requested_at, self.approved, self.denied, self.opened = agents.now(), False, False, False
        elif result.tool_id == REACTIONS:
            found = reactions(result.tool_result)
            self.denied = self.approver in found.get(DENY, set())
            self.approved = self.approver in found.get(APPROVE, set()) and not self.denied and not self.opened
        return None

    def pin_message(self, args: dict):
        """A message only goes to the approver, and a reply only in the request's thread."""
        pinned = {key: value for key, value in args.items() if key != "draft_id"}  # draft_id deletes one of your drafts
        pinned["channel_id"] = self.channel or self.approver
        if args.get("thread_ts"):
            if not self.request_ts:
                return agents.skip("Send the request first: replies go in its thread.")
            pinned["thread_ts"] = self.request_ts
        return agents.hook.PreToolCallHookContinue(effective_arguments=pinned)  # all of them: {} would send none

    def pin_reactions(self):
        """Reactions are only read on the request, until the wait is over."""
        if not self.request_ts or not self.requested_at:
            return agents.skip("There's no request yet: send the approver one with slack_send_message.")
        if agents.now() - self.requested_at > timedelta(minutes=self.wait_minutes):
            return agents.skip(
                f"No answer after {self.wait_minutes} minutes: don't publish. Reply in the request's thread, then stop."
            )
        return agents.hook.PreToolCallHookContinue(
            effective_arguments={"channel_id": self.channel, "message_ts": self.request_ts}
        )

    def why_not(self) -> str:
        if self.denied:
            return "The approver reacted ❌: don't publish. Reply in the request's thread, then stop."
        if self.opened:
            return "This request's pull request is open: to publish anything more, send a new request and wait for its ✅."
        if not self.request_ts:
            return "Publishing needs the approver's ✅ in Slack: send them a request with slack_send_message first."
        return "Not approved yet: run `sleep 20`, then read the request's reactions again with slack_get_reactions."
