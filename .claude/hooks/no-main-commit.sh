#!/usr/bin/env bash
# Hook: Block git commit on main/master branch
# Type: PreToolUse (Bash)
# Exit 0 = pass through, Exit 2 = block

if ! command -v jq &>/dev/null; then
  exit 0
fi

INPUT=$(cat)
CMD=$(echo "$INPUT" | jq -r '.tool_input.command // empty')

if [ -z "$CMD" ]; then
  exit 0
fi

# Only check commands where git commit appears in command position
# (start of command or after a separator), not anywhere in the string.
# Prevents false positives when quoted text merely mentions committing
# (e.g. an issue body passed to gh issue create).
if ! echo "$CMD" | grep -qE "(^|[;&|]\(?)\s*(command\s+)?git\s+(-[A-Za-z]+\s+(\"[^\"]*\"|'[^']*'|\S+)\s+)*commit"; then
  exit 0
fi

# Determine which repo the commit targets, in precedence order:
#   1. a `git -C <dir>` flag (git honors this over the cwd, even with a leading cd)
#   2. a leading `cd <dir>`
#   3. the current working directory
# Handles quoted ("..." / '...') and unquoted directory arguments.
GIT_C_DIR=$(echo "$CMD" | grep -oE "[[:space:]]-C[[:space:]]+(\"[^\"]+\"|'[^']+'|[^ ;&|]+)" | head -1 | sed -E "s/^[[:space:]]*-C[[:space:]]+//; s/^[\"']//; s/[\"']$//")
CD_DIR=$(echo "$CMD" | grep -oE 'cd\s+[^ ;&|]+' | head -1 | sed 's/^cd\s*//')

if [ -n "$GIT_C_DIR" ]; then
  TARGET_DIR="$GIT_C_DIR"
else
  TARGET_DIR="$CD_DIR"
fi

if [ -n "$TARGET_DIR" ]; then
  # Expand a leading ~ without command substitution. Using eval here would
  # execute any $(...) or backtick payload embedded in the untrusted -C arg.
  TARGET_DIR="${TARGET_DIR/#\~/$HOME}"
  BRANCH=$(git -C "$TARGET_DIR" rev-parse --abbrev-ref HEAD 2>/dev/null)
  # If the -C dir did not resolve to a git repo, fall back to the cwd branch.
  if [ -z "$BRANCH" ]; then
    BRANCH=$(git rev-parse --abbrev-ref HEAD 2>/dev/null)
  fi
else
  BRANCH=$(git rev-parse --abbrev-ref HEAD 2>/dev/null)
fi

if [ "$BRANCH" = "main" ] || [ "$BRANCH" = "master" ]; then
  echo "BLOCKED: Cannot commit directly to $BRANCH. Create a feature branch first (e.g., git checkout -b fix/description)." >&2
  exit 2
fi

exit 0
