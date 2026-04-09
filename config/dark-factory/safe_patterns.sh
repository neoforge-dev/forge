# Dark Factory Safe Patterns
# Updated: 2026-04-05 (S195 Phase 1 execution# This file defines patterns that the Dark Factory will autonom review and flag (pre-approval). If any pattern matches, the agent will run that task.
# Only apply patterns matching the file path are not file contents.
# Use `--check-tmux` for git-safe dispatch.

# PATrols: Consolidated (see docs/patrol-kill-matrix.md)

# Dispatch Templates (see .forge/dispatch-templates/)
# Use `--require-live` if agent needs direct CLI access.

# Content generation patterns (DEPRECATED - S195 P5)
# These patterns are now blocked. Content generation has been pivoted to deploy-support.
blog|content|seo|social_media|marketing|outreach|linkedin
newsletter|email_capture

# Deploy-support patterns (NEW - S195 P5)
# These patterns are approved for auto-dispatch
smoke.test
deploy.verify
health.check
uptime
regression
analytics
posthog
email.verify
dispatch.reliable
queue.depth
result.monitor
context.sync
xnode.sync
patrol.kill

# Pattern syntax
^blog(content|seo|content)?
^content\.(blog|newsletter)?
^(smoke|test|deploy.verify)
^(analytics|posthog|email.*)
^uptime.*$
^regression.*$

# Example safe pattern usage:
#   - `blog/posts/launch-announcement.md` -> BLOCKED (content)
#   - `smoke.test/mirrably-api.sh` -> ALLOW (deploy support verification)
#   - `analytics/poststog-setup.md` -> ALLOW (measurement)

# How to use in dispatch templates:
# In the `purpose` field,# In the `capabilities` list
# In the `--check-tmux` flag
# In the `--require-live` flag if the pattern matches

# Example template snippet:
```markdown
purpose: "Verify health check endpoint forcapabilities:
  - can curl health endpoints
  - verify service responds 200 OK
```
```
```
```
