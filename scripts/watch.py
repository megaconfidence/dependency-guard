"""Follow a dependency-guard session in your terminal: watch it work, and steer it.

    uv run python scripts/watch.py --start megaconfidence/pixel-gallery   # start a check on the deployed agent
    uv run python scripts/watch.py <session-id>                           # follow a session the scheduler started
    uv run python scripts/watch.py --local megaconfidence/pixel-gallery   # run the agent in this process instead

Type a line and press Enter to send it to the agent: it reads it after its current step. /cancel ends the session;
/quit or Ctrl-D stops watching (a deployed session keeps running). With --once, the script stops when the turn ends.
"""

import argparse
import asyncio
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # main.py lives in the project root

from mistralai import agents

AGENT_NAME = os.environ.get("AGENT_NAME", "dependency-guard")
DIM, BOLD, RED, GREEN, ORANGE, RESET = "\033[2m", "\033[1m", "\033[31m", "\033[32m", "\033[38;5;208m", "\033[0m"


def short(text: object, limit: int = 160) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def last_lines(text: str, count: int = 3) -> list[str]:
    return [line for line in text.splitlines() if line.strip()][-count:]


def describe_result(entry: agents.ToolResultEntry) -> tuple[list[str], bool]:
    """The lines worth showing for a tool result, and whether it failed."""
    content = entry.structured_content
    if isinstance(content, dict) and "returncode" in content:  # a shell command: show the end of its output
        failed = entry.is_error or content["returncode"] != 0
        output = "\n".join(str(content.get(stream) or "") for stream in ("stdout", "stderr"))
        return last_lines(output, 6 if failed else 3), failed
    if isinstance(content, dict) and ("lines_changed" in content or "content" in content):
        return [], entry.is_error  # a file read or edit: the call line says enough
    if isinstance(content, dict) and ("result" in content or "message_link" in content):  # a Slack reply
        return last_lines(str(content.get("result") or content.get("message_link"))), entry.is_error
    text = "\n".join(block.text for block in entry.content if getattr(block, "text", None))
    if not text and content is not None:
        text = content if isinstance(content, str) else json.dumps(content)
    return last_lines(text), entry.is_error


def describe_call(call: agents.ToolCallEntry) -> str:
    args = call.arguments if isinstance(call.arguments, dict) else {}
    tool = call.name.split(".")[-1]
    if call.name == "file_system.bash":
        return f"{ORANGE}${RESET} {short(args.get('command', ''))}"
    if call.name.startswith("file_system."):
        return f"{ORANGE}⏺ {tool}{RESET} {short(args.get('path') or args.get('file_path') or args)}"
    if call.name.startswith("connector_slack."):
        return f"{ORANGE}⏺ {tool}{RESET} {short(args.get('message', ''), 120)}"
    return f"{ORANGE}⏺ {call.name}{RESET} {DIM}{short(json.dumps(args, default=str), 100)}{RESET}"


class Watcher:
    def __init__(self, session: agents.Session, *, once: bool = False) -> None:
        self.session = session
        self.once = once  # stop when the turn ends

    async def follow(self) -> None:
        async for event in self.session.iter_events():
            match event:
                case agents.EntryCompleted(entry=agents.MessageEntry(role="user") as message):
                    print(f"\n{BOLD}you:{RESET} {message.text}")
                case agents.EntryCompleted(entry=agents.MessageEntry(role="assistant") as message):
                    if message.text.strip():
                        print(f"\n{BOLD}agent:{RESET} {message.text}\n")
                case agents.EntryCompleted(entry=agents.ToolCallEntry() as call):
                    print(describe_call(call))
                case agents.EntryCompleted(entry=agents.ToolResultEntry() as result):
                    lines, failed = describe_result(result)
                    for line in lines:
                        print(f"{RED if failed else DIM}    {short(line, 200)}{RESET}")
                case agents.Notice(message=message, level=level) if str(level) != "info":
                    print(f"{ORANGE}notice ({level}): {message}{RESET}")
                case agents.TurnEnded(state=state):
                    outcome = type(state).__name__
                    detail = f": {state.error.message}" if isinstance(state, agents.Failed) else ""
                    print(f"\n{GREEN if outcome == 'Completed' else RED}turn {outcome.lower()}{detail}{RESET}")
                    if self.once:
                        return
                    print(f"{DIM}Type a message to keep going, /cancel to end the session, /quit to stop watching.{RESET}")

    async def read_input(self) -> None:
        loop = asyncio.get_running_loop()
        lines: asyncio.Queue[str] = asyncio.Queue()
        loop.add_reader(sys.stdin.fileno(), lambda: lines.put_nowait(sys.stdin.readline()))
        try:
            while (line := await lines.get()) != "":  # "" is Ctrl-D
                line = line.strip()
                if line == "/quit":
                    return
                if line == "/cancel":
                    await self.session.cancel()
                    print(f"{DIM}session cancelled{RESET}")
                    return
                if line:
                    await self.session.send_message(line)  # starts a turn, or joins the running one
        finally:
            loop.remove_reader(sys.stdin.fileno())


async def watch(session: agents.Session, *, once: bool = False) -> None:
    print(f"{DIM}session {session.id}{RESET}")
    watcher = Watcher(session, once=once)
    if once:
        await watcher.follow()
        return
    follower = asyncio.create_task(watcher.follow())
    try:
        await watcher.read_input()
    finally:
        follower.cancel()


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("session_id", nargs="?", help="a session to follow")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--start", metavar="REPO", help="start a check on the deployed agent and follow it")
    mode.add_argument("--local", metavar="REPO", help="run the agent in this process and follow it")
    parser.add_argument("--run", help="a name for this run: a new name starts a new session on the same day")
    parser.add_argument("--once", action="store_true", help="stop when the turn ends")
    args = parser.parse_args()

    if args.local:
        from main import agent  # the agent definition, run in this process

        async with agents.local_runtime(program_timeout_seconds=600):
            session = await agent.start_session()
            await session.send_message(f"Daily check for {args.local}.")
            await watch(session, once=args.once)
        return

    agent = agents.Agent.load_from(agent_name=AGENT_NAME)
    if args.start:
        key = f"check:{args.start}:{args.run or datetime.now(UTC).date().isoformat()}"
        session = await agent.start_session(idempotency_key=key)
        await session.send_message(f"Daily check for {args.start}.", idempotency_key=f"{key}:message")
    elif args.session_id:
        session = agent.session(args.session_id)
    else:
        parser.error("give a session id, --start REPO or --local REPO")
    await watch(session, once=args.once)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
