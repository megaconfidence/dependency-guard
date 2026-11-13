# dependency-guard

An agent that keeps a repository's Python dependencies patched. Every morning it looks for security advisories and new releases, upgrades, runs the tests and fixes what breaks. Then it asks you in Slack, and opens a pull request once you react ✅. A person reviews and merges it.

Built with the [Mistral Agents SDK](https://docs.mistral.ai/studio/agents/overview) and deployed with Mistral Managed Agents. It's the demo agent from a video; it watches [pixel-gallery](https://github.com/megaconfidence/pixel-gallery), which depends on [chonkconf](https://github.com/megaconfidence/chonkconf), a demo library with a real (and intentional) [security advisory](https://github.com/megaconfidence/chonkconf/security/advisories).

## How it works

<img src="docs/architecture.png" alt="The daily check in six numbered steps. 1: every morning, a scheduler on GitHub Actions starts a check on dependency-guard, an agent on Mistral Managed Agents running mistral-large-4. 2: it upgrades and tests the repository in its own Mistral sandbox (clone, uv, pytest). 3: once the tests pass, it asks you first in your Slack DM, then waits for your reaction. 4: you react ✅, or ❌ to drop it. 5: after your ✅, it opens the pull request on GitHub, through Sandbox Proxy. 6: you review and merge it; the agent never merges." width="100%">

- **One session per repository per day.** The scheduler (`.github/workflows/daily-check.yml`) starts each one with `start_session` and a message naming the repository. Studio shows every session live, and `scripts/watch.py` follows one in your terminal, where you can steer it.
- **One sandbox per session.** The agent clones the repository and installs packages in its own `MistralSandbox`, which is deleted when the session closes.
- **No token in the sandbox.** `GitHubBinding` lets `git` and `gh` reach the repositories in `repos.py` through Mistral's Sandbox Proxy, with the GitHub access of the user who starts the session. The token never enters the sandbox.
- **It asks before it publishes.** When the tests pass, the agent messages you in Slack with what it changed and the test results, then checks the message's reactions every 20 seconds. React ✅ and it pushes, opens the pull request and replies with its link. React ❌, or don't answer within 15 minutes, and it drops it. It uses the Slack connector of the person who starts the session, so the message comes from your own account: there are no buttons and no notification, and reactions are the answer.
- **Guardrails, in code.** The `SlackApproval` guardrail in `approval.py` makes asking the only way to publish. `git push` and `gh pr create` are refused until the approver's ✅ (`APPROVER` in `repos.py`), and each ✅ opens one pull request. Its Slack messages only go to the approver, it reads reactions only on its request, and every other Slack tool is refused: the connector acts as you, so it could react ✅ for you. The permission policy in `main.py` refuses merging, force-pushing, pushing to the default branch, creating repositories or forks, and writing with the GitHub API. Both match commands: they stop the agent's way of doing these things, not a command hidden in a script.
- **People merge.** The agent never merges.

| File | What it is |
|---|---|
| `main.py` | The agent: model, instructions, sandbox, permission policy, Slack connector and guardrail |
| `approval.py` | The guardrail that makes publishing wait for the approver's ✅ in Slack |
| `instructions.md` | How it finds advisories and new releases, upgrades, tests, fixes, asks and writes pull requests |
| `repos.py` | The repositories it watches, what its sandbox can reach on GitHub, and who approves its pull requests |
| `scripts/start_checks.py` | Starts today's sessions on the deployed agent (the GitHub Action runs it) |
| `scripts/demo.sh` | Runs the demo: reset, trigger, approve, steer |
| `scripts/slack.py` | Answers a session's Slack request with ✅ or ❌ as you, and counts the agent's messages left in your DM (`demo.sh` uses it) |
| `scripts/watch.py` | Follows a session in your terminal, and steers it |
| `scripts/steer.py` | Sends a session a message from the terminal (`demo.sh steer` uses it) |
| `scripts/sessions.py` | Finds the agent's latest session, or cancels and archives them all (`demo.sh` uses it) |
| `scripts/chonkconf-advisory.json` | The security advisory `demo.sh reset` creates on chonkconf |
| `Dockerfile` | Written by `agents deploy`, plus the session limits |
| `docs/architecture.png` | The diagram above |

## Run it

You need Python 3.12+, [uv](https://docs.astral.sh/uv/), a `MISTRAL_API_KEY` from a workspace role with Workflows access and the **Mistral Cloud User** (`cloud_user`) role, and the GitHub and Slack connectors connected in Mistral Studio. Set `APPROVER` in `repos.py` to your Slack member ID: the agent asks that person before it publishes.

The agent pushes and opens pull requests as the person who starts the session, through the **MistralAI GitHub App**. Connecting the connector only authorizes that app, so also [install it](https://github.com/apps/mistralai/installations/new) on the repositories the agent watches. Without the installation, the agent can clone a public repository, but its push fails with a 403 ("Permission to … denied").

```bash
uv sync
uv run pytest                                                         # no key needed
uv run agents info                                                    # what the CLI loads
uv run python scripts/watch.py --local megaconfidence/pixel-gallery   # run it in this process
```

Deploy it to Mistral, then start a check and follow it:

```bash
uv run agents deploy --hardened
uv run python scripts/watch.py --start megaconfidence/pixel-gallery
```

In `watch.py`, type a line to steer the agent (it reads it after its current step), and use `/cancel` to end the session. `--once` stops watching when the turn ends.

## Running the demo

Your terminal starts things, off camera; Studio shows the deployed agent working. `scripts/demo.sh` uses the key in `.env`, never a key exported in your shell.

The story: chonkconf's maintainer merges a security fix, and dependency-guard patches pixel-gallery.

```bash
scripts/demo.sh reset             # before a take: everything back to the start (see below)
                                  # then merge chonkconf's fix PR on GitHub: its release workflow publishes v2.0.0
scripts/demo.sh trigger take-1    # publish chonkconf's advisory, run the daily-check GitHub Action now (like the
                                  # 7:00 schedule), then open the session in Studio
scripts/demo.sh start take-1      # or start the check straight from here (no Action, quicker), then open Studio
                                  # when it asks in Slack, react ✅ to its message: it opens the pull request
scripts/demo.sh approve           # or answer from here: reacts ✅ as you (deny reacts ❌)
scripts/demo.sh steer "Also add a test that proves the exploit is gone"
scripts/demo.sh watch             # follow the latest session in this terminal instead
```

Use a new name for every take: the same name returns the same session. Add `--watch` to `trigger` or `start` to follow the session in the terminal instead of opening Studio. `trigger` and `start` refuse to run until chonkconf's fix is merged and released, since the agent would find nothing to fix.

`reset` puts every repository back to the start and leaves nothing from earlier takes on screen. GitHub can't delete a pull request, so it deletes chonkconf and pixel-gallery and creates them again from your clones of them, next to this one:

- **chonkconf:** two commits. `main` has 1.2.0, tagged `v1.2.0` with its release, and the fix (2.0.0) waits in pull request #1 on the `fix/unsafe-expressions` branch, with its checks passed. Merging is squash-only, so `main` ends with two commits, and the branch is deleted. The advisory (`scripts/chonkconf-advisory.json`) is created as a private draft: `trigger` publishes it once the fix is released, as a maintainer would. It gets a new GHSA ID on every reset.
- **pixel-gallery:** one commit, the `demo-start` tag of your clone, which pins chonkconf `v1.2.0`. No pull requests and no runs, so the agent's pull request is #1 again.
- **dependency-guard:** it deletes its GitHub Actions runs and caches.
- **Studio:** it cancels dependency-guard's open sessions and archives all of them, so the Sessions page lists none. Other agents' sessions are never touched. Studio's Workflows → Executions page still lists every session: executions can't be deleted.
- **Slack:** the connector can't delete messages, so it counts the agent's messages left in your DM. Delete them yourself before filming: the replies in each thread first, then the message.

Deleting a repository needs the `delete_repo` scope: `gh auth refresh -h github.com -s delete_repo`. Keep the MistralAI GitHub App installed on all your repositories: a recreated repository is a new one, which a list of selected repositories wouldn't include.

## The daily check

`.github/workflows/daily-check.yml` runs `scripts/start_checks.py` every morning, and on demand from the Actions tab. It needs a `MISTRAL_API_KEY` repository secret. Each repository gets one session a day; to start new sessions on the same day, run the workflow with a run name, or set `CHECK_RUN`.

While the repository variable `DAILY_CHECKS` is `paused`, scheduled runs skip the check (runs you start yourself still check). Pause and resume it with `gh variable set DAILY_CHECKS --body paused` and `gh variable delete DAILY_CHECKS`.
