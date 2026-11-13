"""The Slack approval guardrail, fed the tool calls and the Slack connector's replies it sees in a session: no Slack, no
key. The replies have the connector's shapes.
"""

import json
from datetime import timedelta

import pytest
from mistralai import agents

from approval import REACTIONS, SEND, SlackApproval, publishes

APPROVER, SOMEONE = "U0APPROVER", "U0SOMEONE"
DM, REQUEST = "D0EXAMPLE", "1794586229.958919"
PUSH = "cd repo && git push -u origin HEAD"
OPEN_PR = "cd repo && gh pr create --title 'Upgrade chonkconf' --body-file pr.md"
NO_REACTIONS = "No reactions found on this message."
TICK = f":white_check_mark: \u00d7 1 \u2014 confidence ({APPROVER})"
CROSS = f":x: \u00d7 1 \u2014 confidence ({APPROVER})"


def reacted(*lines: str) -> str:
    return "Reactions on message:\n\n" + "\n".join(lines)


def call(tool_id: str, **arguments) -> agents.hook.PreToolCall:
    name = tool_id.split(".")[-1]
    return agents.hook.PreToolCall(turn_id="t", tool_name=name, tool_id=tool_id, tool_call_id="c", arguments=arguments)


def refused(decision) -> bool:
    return isinstance(decision, agents.HookDecision) and decision.kind == "skip"


async def run(guard: SlackApproval, tool_id: str, reply: dict, **arguments):
    """Send one tool call through the guardrail and, if it runs, its reply. Returns the guardrail's decision."""
    decision = await guard.pre_tool_call(None, call(tool_id, **arguments))
    if not refused(decision):
        content = [agents.TextContentBlock(text=json.dumps(reply))]
        result = agents.ToolResult(content=content, structured_content=reply, is_error=False)
        effective = decision.effective_arguments if decision else arguments
        name = tool_id.split(".")[-1]
        await guard.post_tool_call(
            None,
            agents.hook.PostToolCall(
                turn_id="t", tool_name=name, tool_id=tool_id, tool_call_id="c", arguments=effective, tool_result=result
            ),
        )
    return decision


async def ask(guard: SlackApproval):
    link = f"https://example.slack.com/archives/{DM}/p{REQUEST.replace('.', '')}"
    sent = {"message_link": link, "message_context": {"message_ts": REQUEST, "channel_id": DM}}
    return await run(guard, SEND, sent, channel_id="approver", message="🛡️ **dependency-guard** wants to open a pull request")


async def check(guard: SlackApproval, answer: str):
    return await run(guard, REACTIONS, {"result": answer}, channel_id="approver", message_ts="1.1")


async def shell(guard: SlackApproval, command: str, returncode: int = 0):
    return await run(guard, "file_system.bash", {"returncode": returncode, "stdout": "", "stderr": ""}, command=command)


async def publish(guard: SlackApproval):
    return await shell(guard, f"{PUSH} && {OPEN_PR.removeprefix('cd repo && ')}")


async def test_publishing_needs_a_request_first():
    decision = await publish(SlackApproval(approver=APPROVER))
    assert refused(decision) and "slack_send_message" in decision.message


async def test_the_request_only_goes_to_the_approver():
    guard = SlackApproval(approver=APPROVER)
    decision = await guard.pre_tool_call(None, call(SEND, channel_id="C0GENERAL", message="Hi", draft_id="Dr0"))
    assert decision.effective_arguments == {"channel_id": APPROVER, "message": "Hi"}


async def test_reactions_are_only_read_on_the_request():
    guard = SlackApproval(approver=APPROVER)
    assert refused(await check(guard, NO_REACTIONS))  # there's nothing to read before the request
    await ask(guard)
    decision = await check(guard, NO_REACTIONS)
    assert decision.effective_arguments == {"channel_id": DM, "message_ts": REQUEST}


async def test_replies_only_go_in_the_request_thread():
    guard = SlackApproval(approver=APPROVER)
    assert refused(await guard.pre_tool_call(None, call(SEND, channel_id=DM, thread_ts="1.1", message="Opened")))
    await ask(guard)
    decision = await guard.pre_tool_call(None, call(SEND, channel_id="C0GENERAL", thread_ts="1.1", message="Opened"))
    assert decision.effective_arguments == {"channel_id": DM, "thread_ts": REQUEST, "message": "Opened"}


async def test_the_approvers_tick_opens_one_pull_request():
    guard = SlackApproval(approver=APPROVER)
    await ask(guard)
    await check(guard, NO_REACTIONS)
    assert refused(await publish(guard))
    await check(guard, reacted(TICK))
    assert await shell(guard, PUSH) is None  # pushing first doesn't use the ✅ up
    assert await shell(guard, OPEN_PR) is None
    await check(guard, reacted(TICK))  # the same ✅, read again
    decision = await shell(guard, PUSH)
    assert refused(decision) and "new request" in decision.message


async def test_a_failed_pull_request_keeps_the_approval():
    guard = SlackApproval(approver=APPROVER)
    await ask(guard)
    await check(guard, reacted(TICK))
    assert await shell(guard, OPEN_PR, returncode=1) is None
    assert await shell(guard, OPEN_PR) is None


async def test_only_the_approvers_tick_counts():
    guard = SlackApproval(approver=APPROVER)
    await ask(guard)
    await check(guard, reacted(f":white_check_mark: \u00d7 1 \u2014 someone ({SOMEONE})"))
    assert refused(await publish(guard))


async def test_a_cross_means_no():
    guard = SlackApproval(approver=APPROVER)
    await ask(guard)
    await check(guard, reacted(TICK, CROSS))
    decision = await publish(guard)
    assert refused(decision) and "❌" in decision.message


async def test_a_new_request_needs_a_new_answer():
    guard = SlackApproval(approver=APPROVER)
    await ask(guard)
    await check(guard, reacted(TICK))
    await ask(guard)
    assert refused(await publish(guard))


async def test_the_wait_ends():
    guard = SlackApproval(approver=APPROVER, wait_minutes=15)
    await ask(guard)
    guard.requested_at -= timedelta(minutes=16)
    decision = await check(guard, NO_REACTIONS)
    assert refused(decision) and "15 minutes" in decision.message


async def test_other_slack_tools_are_refused():
    guard = SlackApproval(approver=APPROVER)
    for tool in ("slack_add_reaction", "slack_read_channel", "slack_search_public_and_private", "slack_send_message_draft"):
        assert refused(await guard.pre_tool_call(None, call(f"connector_slack.{tool}")))


@pytest.mark.parametrize(
    ("command", "published"),
    [
        ("git push -u origin HEAD", True),
        ("cd repo && git -C repo push origin deps/chonkconf-2.0.0", True),
        ("gh pr create --title 'Upgrade chonkconf' --body-file pr.md", True),
        ("gh pr -R megaconfidence/pixel-gallery create --fill", True),
        ("git commit -m 'push the fix'", False),
        ("gh pr list --search create", False),
        ("git status && uv run pytest", False),
    ],
)
def test_what_counts_as_publishing(command: str, published: bool):
    assert publishes(command) == published
