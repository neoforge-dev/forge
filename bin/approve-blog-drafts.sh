#!/bin/bash
# Move blog post drafts to approved/ — blog posts are low-risk, no human review needed
# Usage: bash bin/approve-blog-drafts.sh [--dry-run]

set -euo pipefail
DRY_RUN="${1:-}"
MOVED=0

for domain_dir in .forge/content-queue/*/; do
  domain=$(basename "$domain_dir")
  drafts_dir="${domain_dir}drafts"
  approved_dir="${domain_dir}approved"

  [ -d "$drafts_dir" ] || continue

  for draft in "$drafts_dir"/*.md; do
    [ -f "$draft" ] || continue
    filename=$(basename "$draft")

    # Skip outreach/email content (needs human review)
    if echo "$filename" | grep -qiE "outreach|email|dm-template|pitch"; then
      echo "SKIP (needs human): $draft"
      continue
    fi

    if [ "$DRY_RUN" = "--dry-run" ]; then
      echo "WOULD APPROVE: $draft → $approved_dir/$filename"
    else
      mkdir -p "$approved_dir"
      mv "$draft" "$approved_dir/$filename"
      echo "APPROVED: $draft → $approved_dir/$filename"
    fi
    MOVED=$((MOVED + 1))
  done
done

echo "---"
echo "Total: $MOVED items $([ "$DRY_RUN" = "--dry-run" ] && echo '(dry run)' || echo 'approved')"
