"""Start today's check for every watched repository on the deployed agent, then exit.

The scheduled GitHub Action runs this every morning. Each repository gets one session a day: the idempotency key makes
a second run on the same day return the same session instead of starting another one.

    uv run python scripts/start_checks.py                  # every repository in repos.WATCHED
    uv run python scripts/start_checks.py owner/name       # just this one
    CHECK_RUN=take-2 uv run python scripts/start_checks.py # new sessions on the same day
"""

import asyncio
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # repos.py lives in the project root

from mistralai import agents

from repos import WATCHED

AGENT_NAME = os.environ.get("AGENT_NAME", "dependency-guard")


async def start(agent: agents.Agent, repo: str, run: str) -> str:
    key = f"check:{repo}:{run}"
    session = await agent.start_session(idempotency_key=key)
    await session.send_message(f"Daily check for {repo}.", idempotency_key=f"{key}:message")
    return session.id


async def main(repos: list[str]) -> None:
    run = os.environ.get("CHECK_RUN") or datetime.now(UTC).date().isoformat()
    summary = [f"## Dependency checks ({run})", ""]
    async with agents.Client() as client:
        agent = agents.Agent.load_from(agent_name=AGENT_NAME, client=client)
        for repo in repos:
            session_id = await start(agent, repo, run)
            print(f"{repo}: session {session_id}")
            summary.append(f"- `{repo}`: watch it with `uv run python scripts/watch.py {session_id}`")
    if path := os.environ.get("GITHUB_STEP_SUMMARY"):
        Path(path).write_text("\n".join(summary) + "\n", encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:] or list(WATCHED)))
