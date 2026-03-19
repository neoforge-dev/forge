
---
name: content-library-producer
description: Create a complete 50-piece problem-focused content library for any MVP project, including strategy, blog posts, social media, emails, and downloadable resources.
auto_execute: true
disable-model-invocation: false
allowed-tools: [Read, Write, Bash]
---

# Content Library Producer

## Purpose
Produce a complete, publication-ready content library (50 pieces) for any MVP project following the problem-focused marketing philosophy: **80% problem exploration, 20% actionable advice**.

## When to Use
- Before launching a new MVP (content-first validation)
- When expanding marketing for an existing product
- When targeting a new audience segment

## Required Inputs
Before invoking this skill, gather:
1. **Project name** and brief description
2. **Target audience** (developers, parents, investors, students, etc.)
3. **Core problem** the product solves
4. **Product location** in the codebase (for context)

## Output Structure
Creates directory: `docs/marketing/content/{project-slug}/`

| File | Content | Pieces |
|------|---------|--------|
| `CONTENT_STRATEGY.md` | Personas, pillars, calendar | 1 |
| `blog-posts.md` | 10 complete posts (800-1500 words) | 10 |
| `social-posts.md` | 20 platform-specific posts | 20 |
| `email-sequences.md` | 10 nurture emails | 10 |
| `downloadable-resources.md` | 10 PDF resource outlines | 10 |
| **Total** | | **50 pieces** |

## Workflow

### Step 1: Create Content Strategy (CONTENT_STRATEGY.md)
```
1. Define 4 target personas with:
   - Demographics and situation
   - Inner dialogue (what they're thinking)
   - Current failed solutions
   - Content preferences

2. Identify 6 core problems from multiple angles:
   - The emotional reality
   - Why current solutions fail
   - Specific pain points with data

3. Analyze 6-8 current solutions:
   - Why people try them
   - Why they fail
   - Content angle to address

4. Create 5 content pillars (10 pieces each):
   - Name and focus area
   - Target persona
   - Key messages

5. Map 50 content pieces to pillars
6. Create 12-week distribution calendar
7. Define success metrics
```

### Step 2: Create Blog Posts (blog-posts.md)
For each of 10 posts (2 per pillar):
```
- Compelling title (SEO-friendly)
- Meta description (150 chars)
- Full article (800-1500 words)
- H2/H3 subheadings
- Key takeaways section
- Soft CTA (no hard sell)
```

### Step 3: Create Social Posts (social-posts.md)
Create 20 posts split by platform:
```
Developer audience: LinkedIn/Twitter (10) + Reddit/HN (10)
Parent audience: Pinterest (10) + Facebook Groups (10)
B2B audience: LinkedIn (10) + Twitter threads (10)
Student audience: TikTok/Instagram (10) + Pinterest (10)
```

### Step 4: Create Email Sequences (email-sequences.md)
10 nurture emails following this flow:
```
1. Welcome - acknowledge their struggle
2-5. Problem validation (1 per pillar)
6-8. Quick tips and wins
9. Soft product intro
10. Invitation to try
```
Each email: subject line, preview text, 200-400 word body, CTA

### Step 5: Create Downloadable Resources (downloadable-resources.md)
10 PDF resources (lead magnets):
```
- 2-6 pages each
- Actionable checklists, guides, templates
- Full content outlines with copy
- Designed for email capture
```

## Content Philosophy

### Do:
- Speak to the emotional reality of problems
- Use specific numbers and data
- Share authentic failure stories
- Respect audience intelligence
- Provide value without the product

### Don't:
- Mention product in 80% of content
- Use hype words ("revolutionary", "game-changing")
- Make generic claims
- Hard sell before email 9
- Use stock photo language

## Voice Guidelines by Audience

| Audience | Tone | Platforms |
|----------|------|-----------|
| Developers | Technical, honest, no BS | Twitter, Reddit, HN |
| Parents | Empathetic, practical | Pinterest, Facebook |
| Investors | Professional, data-driven | LinkedIn, Twitter |
| Students | Relatable, punchy | TikTok, Instagram |

## Token Limit Handling
If content exceeds token limits:
1. Split into multiple agent calls
2. Create blog posts in 2 batches (5 each)
3. Create resources with concise outlines (300-600 words each)

## Example Invocation

```
I need a content library for [Project Name].

Context:
- Product: [Brief description]
- Audience: [Target users]
- Core Problem: [What it solves]
- Location: [codebase path]

Please create the 50-piece content library following the content-library-producer skill.
```

## Quality Checklist

Before completing, verify:
- [ ] All 5 files created
- [ ] Strategy has 4 personas, 6 problems, 5 pillars
- [ ] 10 complete blog posts (800+ words each)
- [ ] 20 social posts (platform-appropriate)
- [ ] 10 emails with full copy
- [ ] 10 resources with actionable content
- [ ] 80/20 problem/advice ratio maintained
- [ ] No product mentions until email 9
- [ ] Voice matches target audience

## Notion Integration

For human-in-the-loop review workflow, use Notion storage:

### Configuration
Set environment variables or configure in CLAUDE.md:
```bash
export NOTION_BRIEFS_DATABASE_ID="your-briefs-db-id"
export NOTION_CONTENT_DATABASE_ID="your-content-db-id"
```

### Workflow with Notion
1. Create briefs in Notion database (or via harness)
2. Run `/content batch <domain> --notion` to generate
3. Review drafts in Notion
4. Add feedback in "Human Feedback" field
5. Harness regenerates automatically
6. Approve when node-2sfied

### Python Integration
```python
from forge_harness import (
    ContentHarness,
    create_storage,
    GenerationConfig,
)

# Create harness
harness = ContentHarness(
    domain="codeswiftr-com",
    project="interview-simulator",
    forge_root=Path("/path/to/FORGE"),
)

# Use Notion storage
storage = create_storage(
    "notion",
    briefs_database_id="abc123",
    content_database_id="def456",
)

# Or file storage (default)
storage = create_storage("file", base_dir="./content")
```

## Priority Tiers

Content generation follows the FORGE tier model:

| Tier | Allocation | Focus |
|------|------------|-------|
| 1 (60%) | CodeSwiftr | Full 50-piece library |
| 2 (25%) | LeanVibe, NeoForge | 20-30 pieces each |
| 3 (15%) | All others | 1 lead magnet each |

Use `/content-priorities` to check current allocation.

## Related Skills
- `content-machine` - Generation workflow with feedback loops
- `update-broadcaster` - Announce new content
- `forge docs` - Consult for project context
- `frontend-design` - Create landing pages for content
