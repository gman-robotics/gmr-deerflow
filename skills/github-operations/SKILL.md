# GitHub Operations Skill

**Description:** Full lifecycle for GitHub operations — cloning, branching with worktrees,
conventional commits, PR creation, and code review. Uses the shared `/mnt/repos` volume so
repos are cloned once and reused across tasks via git worktrees, keeping the main clone clean.

**Prerequisites:** `git` and `gh` are available in the sandbox. GitHub App credentials
(`GITHUB_APP_ID`, `GITHUB_APP_INSTALLATION_ID`, `GITHUB_APP_PRIVATE_KEY_B64`) are injected
into the sandbox environment by the AIO sandbox config.

Authentication uses short-lived GitHub App installation tokens (valid 1 hour, auto-expiring).
Generate a fresh token at the start of every task — never cache or persist it.

---

## Step 0 — Generate a GitHub App Installation Token

Run this before any `git` or `gh` command. It generates a short-lived token from the App
credentials and configures `gh` to use it.

```bash
# Install PyJWT if not present (required for RS256 signing)
pip install --quiet PyJWT cryptography 2>/dev/null || true

# Generate installation token and export as GH_TOKEN
export GH_TOKEN=$(python3 - <<'EOF'
import base64, os, time, sys
try:
    import jwt
except ImportError:
    sys.exit("PyJWT not available — run: pip install PyJWT cryptography")
try:
    import requests
except ImportError:
    sys.exit("requests not available")

app_id        = os.environ["GITHUB_APP_ID"]
install_id    = os.environ["GITHUB_APP_INSTALLATION_ID"]
private_key   = base64.b64decode(os.environ["GITHUB_APP_PRIVATE_KEY_B64"]).decode()

now = int(time.time())
payload = {"iat": now - 60, "exp": now + 600, "iss": int(app_id)}
jwt_token = jwt.encode(payload, private_key, algorithm="RS256")

resp = requests.post(
    f"https://api.github.com/app/installations/{install_id}/access_tokens",
    headers={
        "Authorization": f"Bearer {jwt_token}",
        "Accept": "application/vnd.github.v3+json",
    },
)
resp.raise_for_status()
print(resp.json()["token"])
EOF
)

if [ -z "$GH_TOKEN" ]; then
  echo "ERROR: Failed to generate GitHub App installation token."
  exit 1
fi

# Authenticate gh CLI with the installation token
echo "$GH_TOKEN" | gh auth login --with-token

# Also configure git credential helper to use it for HTTPS pushes
git config --global url."https://x-access-token:${GH_TOKEN}@github.com/".insteadOf "https://github.com/"

echo "GitHub App authentication successful."
gh auth status
```

**Token lifetime:** 1 hour. For tasks longer than 1 hour, re-run Step 0 to refresh.

---

## Step 1 — Clone a Repository (Once Per Repo)

Clone to the shared volume so it persists across tasks. Always check before re-cloning:

```bash
REPO_URL="https://github.com/owner/repo.git"
REPO_NAME="repo"
CLONE_PATH="/mnt/repos/${REPO_NAME}"

if [ -d "${CLONE_PATH}/.git" ]; then
  echo "Repo already cloned at ${CLONE_PATH}. Fetching latest..."
  git -C "${CLONE_PATH}" fetch origin
else
  echo "Cloning ${REPO_URL} to ${CLONE_PATH}..."
  git clone "${REPO_URL}" "${CLONE_PATH}"
fi
```

---

## Step 2 — Create a Worktree Per Task

**Never work directly in the main clone.** Use a worktree for every task so branches stay
isolated and the main clone remains clean for future checkouts.

```bash
REPO_NAME="repo"
BRANCH="feat/my-task-description"
# Use thread ID or a short unique slug to avoid worktree path collisions
WORKTREE_PATH="/mnt/repos/${REPO_NAME}-wt-${THREAD_ID:-$(date +%s)}"

cd "/mnt/repos/${REPO_NAME}"
git fetch origin

# Create new branch from origin/main
git worktree add "${WORKTREE_PATH}" -b "${BRANCH}" origin/main

echo "Worktree ready at ${WORKTREE_PATH}"
```

---

## Step 3 — Make Changes and Commit (Conventional Commits)

Use the conventional commit format for all commits:
`<type>(<scope>): <short description>`

Types: `feat` | `fix` | `docs` | `refactor` | `test` | `chore` | `perf`

```bash
cd "${WORKTREE_PATH}"

# Stage all changes
git add -A

# Or stage specific files
git add path/to/file.py

# Commit with conventional format
git commit -m "feat(auth): add JWT token refresh endpoint"

# Push branch to origin
git push -u origin "${BRANCH}"
```

---

## Step 4 — Create a Pull Request

```bash
gh pr create \
  --repo "owner/repo" \
  --title "feat(auth): add JWT token refresh endpoint" \
  --body "## Summary

Brief description of what changed and why.

## Changes
- Added \`/auth/refresh\` endpoint
- Updated token expiry logic

## Testing
- Run \`pytest tests/test_auth.py\`
- Manual test: POST /auth/refresh with expired token

## Notes
Any caveats, follow-up work, or breaking changes." \
  --head "${BRANCH}" \
  --base main
```

---

## Step 5 — Review a Pull Request

```bash
PR_NUMBER=42

# View PR summary and description
gh pr view "${PR_NUMBER}" --repo "owner/repo"

# View the full diff
gh pr diff "${PR_NUMBER}" --repo "owner/repo"

# View linked issue for context
gh pr view "${PR_NUMBER}" --json closingIssuesReferences \
  --jq '.closingIssuesReferences[0].number' | \
  xargs -I{} gh issue view {} --repo "owner/repo" --json title,body

# Approve the PR
gh pr review "${PR_NUMBER}" --repo "owner/repo" \
  --approve --body "Looks good! The implementation is clean and tests pass."

# Request changes
gh pr review "${PR_NUMBER}" --repo "owner/repo" \
  --request-changes --body "A couple of things to address before merging:

1. Missing error handling in the refresh logic when token is malformed.
2. Please add a test for the expired token edge case."
```

---

## Step 6 — Clean Up Worktree After Merge

```bash
cd "/mnt/repos/${REPO_NAME}"
git worktree remove "${WORKTREE_PATH}" --force
git branch -D "${BRANCH}" 2>/dev/null || true

echo "Worktree cleaned up."
```

---

## Error Handling

### Merge Conflicts
```bash
# Identify conflicted files
git status

# Accept incoming (theirs) or current (ours) for a file
git checkout --theirs path/to/conflicted_file.py
git checkout --ours path/to/conflicted_file.py
git add path/to/conflicted_file.py

# Complete the merge
git commit
```

### GitHub API Rate Limits (HTTP 403/429)
```bash
# Check current rate limit status
gh api rate_limit --jq '.resources.core'

# Add a short delay between bulk operations
for PR in 1 2 3; do
  gh pr view $PR --repo "owner/repo"
  sleep 1
done
```

### Re-authenticate on 401 / Token Expired
```bash
# Token has expired (1hr lifetime) — re-run Step 0 to generate a fresh one.
# The token generation script is idempotent and safe to re-run at any time.
gh auth logout 2>/dev/null || true
# Re-run the full Step 0 block above to generate a new installation token.
```

### Worktree Already Exists
```bash
# List all worktrees
git -C "/mnt/repos/${REPO_NAME}" worktree list

# Remove a stale worktree
git -C "/mnt/repos/${REPO_NAME}" worktree remove "${WORKTREE_PATH}" --force
git -C "/mnt/repos/${REPO_NAME}" worktree prune
```

---

## ML / Research Integration

After running experiments, commit notebooks and results with appropriate commit types:

```bash
cd "${WORKTREE_PATH}"

# Commit experiment results
git add notebooks/ results/ experiments/
git commit -m "docs(experiments): add PyTorch vs JAX benchmark results

- Vision transformer comparison on CIFAR-100
- PyTorch: 87.3% top-1, 142ms/batch
- JAX: 87.1% top-1, 118ms/batch (1.2x faster inference)"

git push origin "${BRANCH}"
```

---

## Best Practices

- Always authenticate (`gh auth login`) before any `gh` command
- Always use worktrees — never modify `/mnt/repos/<repo>` (the main clone) directly
- Use conventional commit format for all commits
- Keep worktrees short-lived: create for a task, remove after PR merge
- Set `THREAD_ID` or a task-specific slug in the worktree path to avoid collisions
- For long-running research tasks, commit intermediate results frequently
