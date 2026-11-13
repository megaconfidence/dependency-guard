"""The permission policy and the Slack approval guardrail, with a scripted model and an in-process Bash sandbox: no API
key, no network.

Each command starts with an `echo`, so its output shows whether it was allowed to run.
"""

from mistralai import agents

from approval import SlackApproval
from main import APPROVAL, POLICY

PUBLISH = "git push -u origin HEAD && gh pr create --title 'Upgrade chonkconf to 2.0.0' --body-file /tmp/pr.md"


def approved() -> SlackApproval:
    """The guardrail once the approver has reacted ✅ to a request."""
    return APPROVAL.model_copy(
        update={"channel": "D0EXAMPLE", "request_ts": "1794586229.958919", "requested_at": agents.now(), "approved": True}
    )


async def shell_output(command: str, guardrail: SlackApproval = APPROVAL) -> str:
    """Run one scripted shell command through the policy and the guardrail, and return what it printed."""
    agent = agents.Agent(
        model="mistral-large-4",
        name="policy-test",
        environment=agents.BashkitSandbox(),
        permission_policy=POLICY,
        guardrails=[guardrail],
        builtins=agents.Builtins(subagents=False, background_processes=False),
    ).override(
        model=agents.testing.ScriptedModel([agents.testing.call("bash", command=command), agents.testing.reply("Done.")])
    )
    state = await agent.run("Daily check for megaconfidence/pixel-gallery.")
    assert isinstance(state, agents.Completed)
    printed = []
    async for event in agent.session(state.session_id).iter_events():
        match event:
            case agents.EntryCompleted(entry=agents.ToolResultEntry(structured_content={"stdout": str() as stdout})):
                printed.append(stdout)
            case agents.TurnEnded():
                return "".join(printed)
    return "".join(printed)


async def test_other_commands_run():
    assert "checking" in await shell_output("echo checking")


async def test_publishing_waits_for_approval():
    assert "publishing" not in await shell_output(f"echo publishing && {PUBLISH}")


async def test_publishing_runs_once_approved():
    assert "publishing" in await shell_output(f"echo publishing && {PUBLISH}", approved())


async def test_merging_is_refused_even_once_approved():
    assert "merging" not in await shell_output("echo merging && gh pr merge 1 --squash", approved())


async def test_pushing_to_main_is_refused_even_once_approved():
    assert "pushing" not in await shell_output("echo pushing && git push origin main", approved())
