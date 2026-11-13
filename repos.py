"""The repositories dependency-guard watches, what its sandbox can reach on GitHub, and who approves its pull requests."""

# Checked every morning: one session per repository.
WATCHED = ("megaconfidence/pixel-gallery",)

# What git and gh in the sandbox can reach through the GitHub binding: the watched repositories, plus dependencies
# installed from GitHub, whose releases and security advisories the agent reads.
GITHUB_ACCESS = (*WATCHED, "megaconfidence/chonkconf")

# Whose ✅ in Slack lets the agent publish: a Slack member ID (in Slack, open your profile, then ⋮ → Copy member ID).
# The agent messages them with the Slack connector of the person who starts the session.
APPROVER = "U0BUM44636U"
