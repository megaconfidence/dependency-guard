"""dependency-guard: keeps a repository's Python dependencies patched and up to date.

Every morning a scheduler starts one session per watched repository (scripts/start_checks.py). Each session gets its
own Mistral sandbox, where the agent clones the repository, looks for security advisories and new releases, upgrades,
runs the tests and fixes what breaks. Then it asks a person in Slack, and opens the pull request once they react ✅.
"""

from pathlib import Path

from mistralai import agents

from approval import SlackApproval
from repos import APPROVER, GITHUB_ACCESS

HERE = Path(__file__).parent

POLICY = agents.PermissionPolicy(
    default="allow",
    rules=[
        agents.PermissionRule(
            tools=["file_system.bash"],
            decision="allow",
            # Never from the shell: merge, force-push, push to the default branch, create repositories or forks,
            # or write with the GitHub API. Reading with `gh api` is fine.
            denylist=[
                "*gh pr merge*",
                "*push --force*",
                "*push -f*",
                "*push origin main*",
                "*push -u origin main*",
                "*:main*",
                "*gh repo fork*",
                "*gh repo create*",
                "*gh api*-X*",
                "*gh api*--method*",
                "*gh api*-f *",
                "*gh api*-F *",
                "*gh api*--input*",
            ],
        ),
    ],
)

# Publishing waits for the approver's ✅ in Slack: see approval.py.
APPROVAL = SlackApproval(approver=APPROVER)

agent = agents.Agent(
    model="mistral-large-4",
    name="dependency-guard",
    description="Keeps a repository's Python dependencies patched: upgrades, tests, fixes, and opens pull requests you approve in Slack.",
    instructions=(HERE / "instructions.md").read_text(encoding="utf-8"),
    environment=agents.MistralSandbox(
        network=agents.Network.all(),  # package indexes, and GitHub through the binding
        header_bindings=(agents.GitHubBinding(repos=GITHUB_ACCESS),),
        hooks=[agents.pattern.sandbox_hook.GitIdentity(name="Dependency Guard", email="dependency-guard@example.com")],
        default_exec_timeout_seconds=600,  # the default is 30 s: too short for installs and test suites
        idle_timeout_seconds=600,  # sleep after 10 idle minutes
        ttl_seconds=6 * 60 * 60,  # a backstop, longer than the session limits in the Dockerfile
    ),
    permission_policy=POLICY,
    # The Slack connector of the person who starts the session, to ask the approver before publishing. It acts as
    # that person, so the guardrail lets it do nothing else. auto_auth=False: a missing sign-in never stalls a check.
    connectors=[agents.connector("slack", auto_auth=False)],
    guardrails=[APPROVAL],
    # No subagents, and no background processes: the only way to run a command is file_system.bash, which the
    # policy and the guardrail cover.
    builtins=agents.Builtins(subagents=False, background_processes=False),
    max_iterations=200,  # waiting for the answer takes two steps every 20 seconds
)
