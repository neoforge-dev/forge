---
name: content-publisher
description: Transform content library outlines into publication-ready drafts formatted for specific platforms (WordPress, LinkedIn, Mailchimp, Canva, etc.)
auto_execute: true
disable-model-invocation: false
allowed-tools: [Read, Write]
---

# Content Publisher

## Purpose
Take a content library (created by `content-library-producer`) and produce polished, platform-specific first drafts ready for publication.

## When to Use
- After running `content-library-producer` to create the 50-piece library
- When ready to start publishing content
- When moving from strategy to execution

## Required Inputs
1. **Content library path**: `docs/marketing/content/{project-slug}/`
2. **Output format**: Which platform/format to produce for
3. **Batch selection**: Which content pieces to process

## Output Formats

### Blog Posts → WordPress/Ghost/Medium Ready
```
Output: docs/marketing/content/{project}/drafts/blog/

For each post:
- {slug}.md - Full markdown with frontmatter
- {slug}-meta.json - SEO metadata, tags, categories
```

**Frontmatter format:**
```yaml
---
title: "Post Title"
slug: post-title-slug
date: 2025-12-06
author: [Author Name]
category: [Pillar Name]
tags: [keyword1, keyword2, keyword3]
excerpt: "150 character meta description..."
featured_image: "[placeholder for image prompt]"
---
```

### Social Posts → Platform-Ready
```
Output: docs/marketing/content/{project}/drafts/social/

For each platform:
- linkedin.md - Posts with formatting, hashtags, CTAs
- twitter.md - Threads and single tweets with character counts
- pinterest.json - Pin titles, descriptions, board suggestions
- facebook.md - Group-friendly posts
- reddit.md - Subreddit-specific versions
- tiktok.md - Scripts with hooks, timing notes
```

**LinkedIn format:**
```markdown
## Post 1: [Title]

[Opening hook - first 2 lines visible before "see more"]

[Body content with line breaks for readability]

[Call to action]

#hashtag1 #hashtag2 #hashtag3

---
Timing: [Best day/time]
Engagement prompt: [Question to ask in comments]
```

**Twitter/X format:**
```markdown
## Thread 1: [Title]

🧵 1/8: [Hook tweet - 280 chars max]

2/8: [Content]

3/8: [Content]

...

8/8: [CTA with link placeholder]

---
Single tweet version: [Condensed 280 char version]
```

### Email Sequences → Mailchimp/ConvertKit Ready
```
Output: docs/marketing/content/{project}/drafts/email/

- sequence.json - Full sequence with automation triggers
- email-01.html - HTML email with inline styles
- email-01.txt - Plain text version
```

**Sequence JSON format:**
```json
{
  "sequence_name": "Welcome Sequence",
  "trigger": "signup",
  "emails": [
    {
      "id": 1,
      "delay_days": 0,
      "subject": "Subject line",
      "preview_text": "Preview text",
      "from_name": "Sender Name",
      "html_file": "email-01.html",
      "text_file": "email-01.txt"
    }
  ]
}
```

### Downloadable Resources → Canva/PDF Ready
```
Output: docs/marketing/content/{project}/drafts/resources/

For each resource:
- {resource-slug}.md - Full content with page breaks
- {resource-slug}-design.json - Design specs for Canva/Figma
```

**Design spec format:**
```json
{
  "title": "Resource Title",
  "subtitle": "Subtitle",
  "pages": 4,
  "format": "A4",
  "brand_colors": ["#hex1", "#hex2"],
  "sections": [
    {"page": 1, "type": "cover", "content": "..."},
    {"page": 2, "type": "content", "content": "..."}
  ],
  "cta": "Download more at..."
}
```

## Workflow

### Step 1: Read Source Content
```
Read: docs/marketing/content/{project}/CONTENT_STRATEGY.md
Read: docs/marketing/content/{project}/blog-posts.md
Read: docs/marketing/content/{project}/social-posts.md
Read: docs/marketing/content/{project}/email-sequences.md
Read: docs/marketing/content/{project}/downloadable-resources.md
```

### Step 2: Create Output Directories
```bash
mkdir -p docs/marketing/content/{project}/drafts/{blog,social,email,resources}
```

### Step 3: Process Each Content Type

**For Blog Posts:**
1. Extract each post from blog-posts.md
2. Add frontmatter with SEO metadata
3. Format with proper markdown headings
4. Add image placeholders with AI prompts
5. Generate slug and filename
6. Create meta.json with keywords

**For Social Posts:**
1. Split by platform
2. Add platform-specific formatting
3. Add hashtags (researched/relevant)
4. Add timing recommendations
5. Add engagement prompts
6. Check character limits

**For Email Sequences:**
1. Convert to HTML with inline styles
2. Create plain text versions
3. Generate sequence.json
4. Add merge tags ({{first_name}}, etc.)
5. Add unsubscribe footers

**For Resources:**
1. Expand outlines to full content
2. Add page break markers
3. Create design specifications
4. Add brand placeholders
5. Create Canva/Figma-ready specs

### Step 4: Create Publishing Checklist
```
Output: docs/marketing/content/{project}/drafts/PUBLISHING_CHECKLIST.md
```

## Batch Processing

Process content in batches to avoid token limits:

```
Batch 1: Blog posts 1-5
Batch 2: Blog posts 6-10
Batch 3: Social posts (LinkedIn + Twitter)
Batch 4: Social posts (Pinterest + Facebook + Reddit)
Batch 5: Email sequence
Batch 6: Resources 1-5
Batch 7: Resources 6-10
```

## Example Invocation

```
I need to publish the Interview Simulator content library.

Please use the content-publisher skill to create:
1. WordPress-ready blog posts
2. LinkedIn and Twitter posts
3. Mailchimp email sequence
4. Canva-ready PDF resources

Source: docs/marketing/content/interview-simulator/
```

## Platform-Specific Guidelines

### LinkedIn
- First 2 lines are critical (before "see more")
- Use line breaks liberally
- End with engagement question
- 3-5 relevant hashtags
- Best times: Tue-Thu, 8-10am

### Twitter/X
- 280 character limit
- Threads for complex topics
- Use 🧵 for thread indicator
- Retweet-friendly formatting
- Best times: Mon-Fri, 12-3pm

### Pinterest
- SEO-rich descriptions (500 chars)
- Keyword-focused titles
- Board recommendations
- Vertical image specs (1000x1500)

### Email
- Subject: 40-60 characters
- Preview: 40-90 characters
- Mobile-first formatting
- Clear single CTA
- P.S. line for important info

## Quality Checklist

Before completing:
- [ ] All drafts have correct frontmatter/metadata
- [ ] Character limits respected
- [ ] Platform formatting applied
- [ ] Hashtags/keywords included
- [ ] CTAs are clear and actionable
- [ ] Image placeholders included
- [ ] Publishing checklist created

## Related Skills
- `content-library-producer` - Creates the source content
- `frontend-design` - Creates landing pages
- `update-broadcaster` - Announces published content
