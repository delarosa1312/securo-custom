#!/usr/bin/env bash
# Create a branch linked to a GitHub issue (GitHub only links a branch it creates) and switch this worktree to it.
# Usage: fork/tools/new-branch.sh <issue> <slug>
# FORK_BRANCH_TYPE (default feat) is the prefix: feat, fix, chore, docs, sync. FORK_BASE (default main) is the base.
set -euo pipefail
issue="${1:?usage: new-branch.sh <issue> <slug>}"
slug="${2:?usage: new-branch.sh <issue> <slug>}"
type="${FORK_BRANCH_TYPE:-feat}"
base="${FORK_BASE:-main}"
name="$type/$issue-$slug"

url="$(git remote get-url origin)"
repo="${url#*github.com[:/]}"
repo="${repo%.git}"
owner="${repo%%/*}"
repo_name="${repo##*/}"

if git ls-remote --exit-code --heads origin "$name" >/dev/null 2>&1; then
  echo "origin/$name already exists; switching to it"
else
  git fetch -q origin "$base"
  oid="$(git rev-parse "origin/$base")"
  ids="$(gh api graphql -f query='query($o:String!,$r:String!,$n:Int!){repository(owner:$o,name:$r){id issue(number:$n){id}}}' \
    -f o="$owner" -f r="$repo_name" -F n="$issue" --jq '.data.repository | .id + " " + .issue.id')"
  gh api graphql -f query='mutation($i:ID!,$r:ID!,$o:GitObjectID!,$n:String!){createLinkedBranch(input:{issueId:$i,repositoryId:$r,oid:$o,name:$n}){linkedBranch{ref{name}}}}' \
    -f i="${ids#* }" -f r="${ids% *}" -f o="$oid" -f n="$name" --jq '.data.createLinkedBranch.linkedBranch.ref.name' >/dev/null
  echo "created origin/$name from $base ($oid), linked to #$issue"
fi
git fetch -q origin "$name"
if git show-ref --verify --quiet "refs/heads/$name"; then
  git switch "$name"
else
  git switch -c "$name" --track "origin/$name"
fi
