"""dependency-guard's sessions on the deployed agent: find the latest one, or clear them all before a take.

    uv run python scripts/sessions.py latest   # print the newest session's id
    uv run python scripts/sessions.py clear    # cancel the open ones and archive them all: Studio then lists none

Only dependency-guard's sessions are touched: the same API key sees other agents' sessions too (Mistral Vibe's, for
example). The public `agents.list_sessions` doesn't say which agent a session belongs to and can't archive one, so
this uses the SDK's own sessions client, from the version pinned in uv.lock.
"""

import asyncio
import os
import sys

from mistralai import agents
from mistralai.vibe_agents.sdk.transport import remote

AGENT_NAME = os.environ.get("AGENT_NAME", "dependency-guard")


async def listed(sessions: remote.SessionsClient) -> list:
    """The agent's sessions that Studio lists (not archived), newest first."""
    found, cursor = [], None
    while True:
        page = await sessions.list_sessions(limit=50, cursor=cursor)
        found += [item for item in page.items if item.agent_name == AGENT_NAME]
        if not (cursor := page.next_cursor):
            return sorted(found, key=lambda item: item.created_at, reverse=True)


async def main(command: str) -> None:
    async with agents.Client() as client:
        sessions = remote._sessions_client(client._deployment, None, None, client._deployment.pool)
        found = await listed(sessions)
        if command == "latest":
            if not found:
                sys.exit("No dependency-guard session yet: run scripts/demo.sh trigger first")
            print(found[0].agent_session_id)
            return

        agent = agents.Agent.load_from(agent_name=AGENT_NAME, client=client)
        still_open = [item for item in found if item.status_text in ("running", "waiting")]
        for item in still_open:
            await agent.session(str(item.agent_session_id)).cancel()
        for item in found:
            await sessions.archive_session(agent_session_id=item.agent_session_id)
        left = await listed(sessions)
        archived = len(found) - len(left)
        cancelled = f", after cancelling {len(still_open)} that {'was' if len(still_open) == 1 else 'were'} still open"
        print(f"  archived {archived} session{'' if archived == 1 else 's'}{cancelled if still_open else ''}"
              if found else "  no sessions to clear")
        if left:
            sys.exit(f"  {len(left)} sessions are still listed: {', '.join(str(item.agent_session_id) for item in left)}")


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in ("latest", "clear"):
        sys.exit(__doc__)
    asyncio.run(main(sys.argv[1]))
