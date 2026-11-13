#!/usr/bin/env bash
# Run the dependency-guard demo. Your terminal starts things, off camera; Studio shows the deployed agent working.
#
#   scripts/demo.sh reset                  Before a take: put the three repositories back to the start and wipe
#                                          every trace of earlier takes (see below)
#   scripts/demo.sh trigger [name]         After you merge chonkconf's fix: publish its advisory, run the daily-check
#                                          GitHub Action now (like the 7:00 schedule), then open the session in Studio
#   scripts/demo.sh start [name]           The same, but start the check straight from here (no Action, quicker)
#   scripts/demo.sh approve | deny         Answer the latest session's Slack request from here: reacts ✅ or ❌ as you
#   scripts/demo.sh steer "message"        Send the latest session a message: it reads it after its current step
#   scripts/demo.sh watch [session]        Follow a session in this terminal
#
# reset archives the agent's sessions (Studio lists none), recreates chonkconf (v1.2.0 on main, its fix in an open
# pull request, a draft advisory) and pixel-gallery (one commit) from your clones of them next to this one, and
# deletes dependency-guard's Actions runs. Keep those clones: their tags and branches are the starting point. It can't
# delete the agent's Slack messages: it counts the ones left in your DM, for you to delete.
# Each name starts a new session: use a new one for every take (the default is take-HHMMSS).
# Add --watch to trigger or start to follow the session here instead of opening Studio (and --once to stop watching
# when its turn ends). Once the tests pass, the agent asks you in Slack: react ✅ to its message to let it open the
# pull request, or ❌ to drop it.
set -euo pipefail

cd "$(dirname "$0")/.."
REPO="megaconfidence/pixel-gallery"
AGENT_REPO="megaconfidence/dependency-guard"
LIBRARY_REPO="megaconfidence/chonkconf"
GALLERY_CLONE="../pixel-gallery"
LIBRARY_CLONE="../chonkconf"
START_TAG="demo-start"               # pixel-gallery's starting commit, in its clone only
LIBRARY_TAG="v1.2.0"                 # chonkconf's vulnerable release, which main starts at
FIX_BRANCH="fix/unsafe-expressions"  # chonkconf's fix: one commit on top of LIBRARY_TAG, opened as a pull request
FIX_TAG="v2.0.0"                     # what chonkconf's release workflow publishes once the fix is merged
FIX_TITLE="fix!: stop running code from config expressions"
ADVISORY="scripts/chonkconf-advisory.json"
GALLERY_DESCRIPTION="Build a static photo gallery from a folder of images and a shareable theme. A demo project for the Mistral Agents SDK."
LIBRARY_DESCRIPTION="A tiny TOML config loader with computed values. A demo library for the Mistral Agents SDK: don't use it in production."
read -r -d '' FIX_BODY <<'EOF' || true
Computed values (`"{{ ... }}"`) are evaluated with Python's `eval()`, so a config file can run any code on the machine that loads it:

```toml
oops = "{{ __import__('os').system('id') }}"
```

This computes `{{ }}` values with a small arithmetic evaluator instead: numbers, `+ - * / // % **`, parentheses and built-in units such as `day` and `MiB`. Anything else raises `UnsafeExpressionError`.

**Breaking:** the `functions=` option is removed, so this ships as 2.0.0. The changelog has an upgrade guide.
EOF

# Use this project's key from .env (not a key exported in your shell), and keep uv on public PyPI.
unset AGENT MISTRAL_API_KEY UV_INDEX_URL UV_DEFAULT_INDEX UV_INDEX_STRATEGY PIP_INDEX_URL
set -a
# shellcheck disable=SC1091
source .env
set +a

py() { uv run --quiet --frozen --default-index https://pypi.org/simple python "$@"; }

latest_session() { py scripts/sessions.py latest; }

plural() { if [[ $1 == 1 ]]; then echo "$1 $2"; else echo "$1 ${2}s"; fi; }

# Open the session in Studio, or follow it here with --watch.
show() {
  local session=$1; shift
  if [[ " $* " == *" --watch "* ]]; then
    local args=()
    for arg in "$@"; do [[ $arg == --watch ]] || args+=("$arg"); done
    py scripts/watch.py "$session" ${args[@]+"${args[@]}"}
    return
  fi
  local url="https://console.mistral.ai/managed-agents/sessions/$session"
  echo "Session $session"
  echo "Watch it in Studio: $url"
  echo "When it asks in Slack, react ✅ to its message (or run scripts/demo.sh approve)"
  if command -v open >/dev/null; then open "$url"; fi
}

# The starting point of each repository, from the clones. Checked before anything is deleted.
starting_points() {
  gallery=$(git -C "$GALLERY_CLONE" rev-parse --verify --quiet "$START_TAG^{commit}") ||
    { echo "Can't find the $START_TAG tag in $GALLERY_CLONE" >&2; exit 1; }
  library=$(git -C "$LIBRARY_CLONE" rev-parse --verify --quiet "$LIBRARY_TAG^{commit}") ||
    { echo "Can't find the $LIBRARY_TAG tag in $LIBRARY_CLONE" >&2; exit 1; }
  fix=$(git -C "$LIBRARY_CLONE" rev-parse --verify --quiet "refs/heads/$FIX_BRANCH") ||
    { echo "Can't find the $FIX_BRANCH branch in $LIBRARY_CLONE" >&2; exit 1; }
  [[ "$(git -C "$LIBRARY_CLONE" rev-parse "$fix^")" == "$library" ]] ||
    { echo "$FIX_BRANCH in $LIBRARY_CLONE should be one commit on top of $LIBRARY_TAG" >&2; exit 1; }
  git -C "$GALLERY_CLONE" show "$START_TAG:uv.lock" | grep -q "#$library\"" ||
    { echo "pixel-gallery's uv.lock at $START_TAG doesn't pin chonkconf $LIBRARY_TAG (${library:0:7})" >&2; exit 1; }
  [[ -f $ADVISORY ]] || { echo "Missing $ADVISORY" >&2; exit 1; }
}

require_delete_scope() {
  gh auth status 2>&1 | grep -q delete_repo ||
    { echo "Your gh login can't delete repositories: run gh auth refresh -h github.com -s delete_repo" >&2; exit 1; }
}

# Delete a repository if it exists, and print its description, to reuse.
delete_repo() {
  local repo=$1
  if gh api "repos/$repo" --silent 2>/dev/null; then
    gh api "repos/$repo" --jq '.description // empty'
    gh repo delete "$repo" --yes >/dev/null
    for _ in $(seq 1 15); do gh api "repos/$repo" --silent 2>/dev/null || break; sleep 2; done
  fi
}

# Create a repository with Actions off, so pushing its starting commits starts no runs.
create_repo() {
  local repo=$1 description=$2
  for attempt in 1 2 3 4 5; do
    gh repo create "$repo" --public --description "$description" >/dev/null 2>&1 && break
    [[ $attempt == 5 ]] && { echo "Couldn't create $repo" >&2; exit 1; }
    sleep 3  # the old name takes a moment to free up
  done
  gh api -X PUT "repos/$repo/actions/permissions" -F enabled=false >/dev/null
}

actions_on() { gh api -X PUT "repos/$1/actions/permissions" -F enabled=true >/dev/null; }

# A version's section of chonkconf's changelog, at a commit of the clone.
changelog_notes() {
  git -C "$LIBRARY_CLONE" show "$1:CHANGELOG.md" |
    awk -v version="$2" 'index($0, "## " version) == 1 { found = 1; next } found && /^## / { exit } found'
}

# Wait for a pull request's checks to start, then to pass.
wait_for_checks() {
  local pr=$1
  for _ in $(seq 1 30); do
    [[ "$(gh pr view "$pr" --json statusCheckRollup --jq '.statusCheckRollup | length')" -gt 0 ]] && break
    sleep 2
  done
  gh pr checks "$pr" --watch --interval 5 >/dev/null || { echo "The checks of $pr failed" >&2; exit 1; }
}

# chonkconf at its start: v1.2.0 on main with its release, the fix in an open pull request, a draft advisory.
library_is_fresh() {
  [[ "$(gh api "repos/$LIBRARY_REPO" --jq .default_branch 2>/dev/null)" == main ]] &&
    [[ "$(gh api "repos/$LIBRARY_REPO/git/ref/heads/main" --jq .object.sha 2>/dev/null)" == "$library" ]] &&
    [[ "$(gh api "repos/$LIBRARY_REPO/git/ref/heads/$FIX_BRANCH" --jq .object.sha 2>/dev/null)" == "$fix" ]] &&
    [[ "$(gh api "repos/$LIBRARY_REPO/branches" --jq '[.[].name] | sort | join(",")')" == "$FIX_BRANCH,main" ]] &&
    [[ "$(gh api "repos/$LIBRARY_REPO/tags" --jq '[.[].name] | join(",")')" == "$LIBRARY_TAG" ]] &&
    [[ "$(gh release list -R "$LIBRARY_REPO" --json tagName --jq '[.[].tagName] | join(",")')" == "$LIBRARY_TAG" ]] &&
    [[ "$(gh pr list -R "$LIBRARY_REPO" --state all --json state --jq '[.[].state] | join(",")')" == OPEN ]] &&
    [[ "$(gh api "repos/$LIBRARY_REPO/security-advisories" --jq '[.[].state] | join(",")')" == draft ]]
}

recreate_library() {
  local description ghsa pr
  require_delete_scope
  description=$(delete_repo "$LIBRARY_REPO")
  create_repo "$LIBRARY_REPO" "${description:-$LIBRARY_DESCRIPTION}"
  # Squash merges only, so merging the fix leaves main with two commits; GitHub then deletes the fix's branch.
  gh api -X PATCH "repos/$LIBRARY_REPO" -F allow_merge_commit=false -F allow_rebase_merge=false \
    -F allow_squash_merge=true -f squash_merge_commit_title=PR_TITLE -f squash_merge_commit_message=BLANK \
    -F delete_branch_on_merge=true >/dev/null
  git -C "$LIBRARY_CLONE" push --quiet origin "$library:refs/heads/main" "refs/tags/$LIBRARY_TAG" \
    "$fix:refs/heads/$FIX_BRANCH"
  # Pushing two branches into an empty repository can make GitHub pick the fix as its default branch.
  gh api -X PATCH "repos/$LIBRARY_REPO" -f default_branch=main >/dev/null
  changelog_notes "$library" "${LIBRARY_TAG#v}" |
    gh release create "$LIBRARY_TAG" -R "$LIBRARY_REPO" --verify-tag --title "${LIBRARY_TAG#v}" --notes-file - >/dev/null
  ghsa=$(gh api -X POST "repos/$LIBRARY_REPO/security-advisories" --input "$ADVISORY" --jq .ghsa_id)
  actions_on "$LIBRARY_REPO"
  pr=$(gh pr create -R "$LIBRARY_REPO" --base main --head "$FIX_BRANCH" --title "$FIX_TITLE" --body "$FIX_BODY")
  wait_for_checks "$pr"
  git -C "$LIBRARY_CLONE" fetch --quiet --prune --no-tags origin
  echo "  recreated $LIBRARY_REPO: $LIBRARY_TAG on main, its fix in $pr (checks passed), draft advisory $ghsa"
}

# pixel-gallery at its start: one branch at the starting commit, and nothing else.
gallery_is_fresh() {
  [[ "$(gh api "repos/$REPO/branches" --jq '[.[].name] | join(",")' 2>/dev/null)" == main ]] &&
    [[ "$(gh api "repos/$REPO/git/ref/heads/main" --jq .object.sha)" == "$gallery" ]] &&
    [[ "$(gh api "repos/$REPO/tags" --jq length)" == 0 ]] &&
    [[ "$(gh pr list -R "$REPO" --state all --json number --jq length)" == 0 ]] &&
    [[ "$(gh api "repos/$REPO/actions/runs" --jq .total_count)" == 0 ]]
}

recreate_gallery() {
  local description
  require_delete_scope
  description=$(delete_repo "$REPO")
  create_repo "$REPO" "${description:-$GALLERY_DESCRIPTION}"
  git -C "$GALLERY_CLONE" push --quiet origin "$gallery:refs/heads/main"
  actions_on "$REPO"
  git -C "$GALLERY_CLONE" fetch --quiet --prune origin  # never --prune-tags: demo-start is only in the clone
  echo "  recreated $REPO from $START_TAG (${gallery:0:7})"
}

# Delete a repository's GitHub Actions runs and caches.
wipe_actions() {
  local repo=$1 runs caches id status deleted=0
  runs=$(gh api "repos/$repo/actions/runs?per_page=100" --paginate --jq '.workflow_runs[] | "\(.id) \(.status)"')
  while read -r id status; do  # a run can't be deleted while it's going: cancel it first
    if [[ -n $id && $status != completed ]]; then gh run cancel "$id" -R "$repo" >/dev/null 2>&1 || true; fi
  done <<<"$runs"
  while read -r id status; do
    [[ -n $id ]] || continue
    for _ in 1 2 3 4 5 6; do
      if gh api -X DELETE "repos/$repo/actions/runs/$id" >/dev/null 2>&1; then deleted=$((deleted + 1)); break; fi
      sleep 5  # a cancelled run takes a moment to stop
    done
  done <<<"$runs"
  caches=$(gh api "repos/$repo/actions/caches?per_page=100" --paginate --jq '.actions_caches[].id')
  for id in $caches; do gh api -X DELETE "repos/$repo/actions/caches/$id" >/dev/null; done
  echo "  $repo: deleted $(plural "$deleted" "workflow run") and $(plural "$(wc -w <<<"$caches" | tr -d ' ')" cache)"
}

reset() {
  starting_points
  echo "Studio: archiving dependency-guard's sessions"
  py scripts/sessions.py clear

  echo "GitHub: putting the repositories back to the start"
  if library_is_fresh; then echo "  $LIBRARY_REPO is already at its start"; else recreate_library; fi
  if gallery_is_fresh; then echo "  $REPO is already at its start"; else recreate_gallery; fi
  wipe_actions "$AGENT_REPO"

  local pr advisory pin runs
  pr=$(gh pr list -R "$LIBRARY_REPO" --head "$FIX_BRANCH" --json url --jq '.[0].url')
  advisory=$(gh api "repos/$LIBRARY_REPO/security-advisories" --jq '[.[] | "\(.ghsa_id) (\(.state))"] | join(", ")')
  pin=$(gh api "repos/$REPO/contents/pyproject.toml" --jq .content | base64 --decode | grep -o 'tag = "[^"]*"' | cut -d'"' -f2)
  runs=$(($(gh api "repos/$REPO/actions/runs" --jq .total_count) + $(gh api "repos/$AGENT_REPO/actions/runs" --jq .total_count)))
  echo "Ready:"
  echo "  chonkconf: $(plural "$(gh api "repos/$LIBRARY_REPO/commits?sha=main" --jq length)" commit) on main ($LIBRARY_TAG)," \
    "its fix waiting in $pr, advisory $advisory"
  echo "  pixel-gallery: $(plural "$(gh api "repos/$REPO/commits?sha=main" --jq length)" commit)," \
    "$(plural "$(gh pr list -R "$REPO" --state all --json number --jq length)" "pull request"), pinned to chonkconf $pin"
  echo "  $(plural "$runs" "workflow run") in pixel-gallery and dependency-guard; no dependency-guard sessions in Studio"
  py scripts/slack.py leftovers
}

# The agent only has something to do once chonkconf's fix is merged and released. Then publish its advisory, as a
# maintainer would with the fixed release.
fix_released() {
  local i state id
  for i in $(seq 1 40); do
    gh release view "$FIX_TAG" -R "$LIBRARY_REPO" >/dev/null 2>&1 && break
    state=$(gh pr list -R "$LIBRARY_REPO" --state all --head "$FIX_BRANCH" --json state --jq '.[0].state // empty')
    if [[ $state == OPEN ]]; then
      echo "Merge chonkconf's fix first: $(gh pr list -R "$LIBRARY_REPO" --head "$FIX_BRANCH" --json url --jq '.[0].url')" >&2
      exit 1
    fi
    [[ $i == 1 ]] && echo "Waiting for chonkconf's release workflow to publish $FIX_TAG..."
    [[ $i == 40 ]] && { echo "chonkconf $FIX_TAG isn't released: check its release workflow" >&2; exit 1; }
    sleep 3
  done
  for id in $(gh api "repos/$LIBRARY_REPO/security-advisories?state=draft" --jq '.[].ghsa_id'); do
    gh api -X PATCH "repos/$LIBRARY_REPO/security-advisories/$id" -f state=published >/dev/null
    echo "Published chonkconf's advisory $id"
  done
}

trigger() {
  local name="take-$(date +%H%M%S)"
  if [[ $# -gt 0 && $1 != --* ]]; then name=$1; shift; fi
  fix_released

  gh workflow run daily-check.yml -R "$AGENT_REPO" -f run="$name"
  echo "Started the daily-check workflow ($name). Waiting for it to start the session..."
  local run_id=""
  for _ in $(seq 1 30); do
    run_id=$(gh run list -R "$AGENT_REPO" --workflow daily-check.yml --limit 10 --json databaseId,displayTitle \
      --jq "[.[] | select(.displayTitle | endswith(\" $name\"))][0].databaseId // empty")
    [[ -n "$run_id" ]] && break
    sleep 2
  done
  [[ -n "$run_id" ]] || { echo "Couldn't find the workflow run for $name" >&2; exit 1; }
  gh run watch "$run_id" -R "$AGENT_REPO" --exit-status --interval 3 >/dev/null
  local session
  session=$(gh run view "$run_id" -R "$AGENT_REPO" --log | grep -oE 'session [0-9a-f-]{36}' | head -1 | cut -d' ' -f2)
  [[ -n "$session" ]] || { echo "The workflow didn't print a session id" >&2; exit 1; }
  show "$session" "$@"
}

start() {
  local name="take-$(date +%H%M%S)"
  if [[ $# -gt 0 && $1 != --* ]]; then name=$1; shift; fi
  fix_released
  local session
  session=$(CHECK_RUN="$name" py scripts/start_checks.py "$REPO" | grep -oE 'session [0-9a-f-]{36}' | cut -d' ' -f2)
  show "$session" "$@"
}

case "${1:-}" in
  reset) reset ;;
  trigger) shift; trigger "$@" ;;
  start) shift; start "$@" ;;
  approve | deny) session=$(latest_session); py scripts/slack.py "$1" "$session" ;;
  steer) [[ $# -ge 2 ]] || { echo 'Usage: scripts/demo.sh steer "message"' >&2; exit 2; }
    session=$(latest_session); py scripts/steer.py "$session" "$2" ;;
  watch) session=${2:-$(latest_session)}; py scripts/watch.py "$session" ;;
  *) awk 'NR > 1 && /^#/ { sub(/^# ?/, ""); print; next } NR > 1 { exit }' "$0"; exit 2 ;;
esac
