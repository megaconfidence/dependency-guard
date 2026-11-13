"""Send a dependency-guard session a message from the terminal, off camera, while Studio shows it on screen.

    uv run python scripts/steer.py <session-id> "Also add a test that proves the exploit is gone"

The agent reads it after its current step: it joins the running turn, or starts a new one.
"""

import asyncio
import os
import sys

from mistralai import agents

AGENT_NAME = os.environ.get("AGENT_NAME", "dependency-guard")


async def main(session_id: str, text: str) -> None:
    session = agents.Agent.load_from(agent_name=AGENT_NAME).session(session_id)
    turn = await session.send_message(text)
    print("sent: it joins the running turn" if not turn.started else "sent: it started a new turn")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    asyncio.run(main(sys.argv[1], " ".join(sys.argv[2:])))
