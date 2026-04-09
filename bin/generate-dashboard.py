#!/usr/bin/env python3
"""
FORGE Portfolio Dashboard Generator — The Bridge
Generates a single-page HTML dashboard from repo state.

Usage:
    uv run bin/generate-dashboard.py
    uv run bin/generate-dashboard.py --output path/to/output.html
"""

import argparse
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    import yaml
except ImportError:
    print("PyYAML not found. Install with: uv add pyyaml", file=sys.stderr)
    sys.exit(1)

REPO_ROOT = Path(__file__).resolve().parent.parent
DOMAINS_YAML = REPO_ROOT / "config" / "domains.yaml"
CONTEXT_DIR = REPO_ROOT / ".forge" / "context"
KILLS_DIR = REPO_ROOT / ".forge" / "kills"
OUTPUT_PATH = REPO_ROOT / "apps" / "dashboard" / "index.html"

# Status color mapping
STATUS_COLORS = {
    "deploy-ready": ("#22c55e", "green"),
    "near-ready": ("#22c55e", "green"),
    "building": ("#eab308", "yellow"),
    "idea": ("#eab308", "yellow"),
    "paused": ("#eab308", "yellow"),
    "inactive": ("#6b7280", "gray"),
    "killed": ("#ef4444", "red"),
}

ACTIVE_BADGE_COLOR = "#22c55e"
PAUSED_BADGE_COLOR = "#eab308"
KILLED_BADGE_COLOR = "#ef4444"
INACTIVE_BADGE_COLOR = "#6b7280"


def load_domains() -> dict:
    """Load domain config from config/domains.yaml."""
    with open(DOMAINS_YAML) as f:
        data = yaml.safe_load(f)
    domains = data.get("domains", {})

    # Inject missing domains if context exists
    if "card-games" not in domains and (CONTEXT_DIR / "card-games").exists():
        domains["card-games"] = {
            "display_name": "Card Games",
            "owner": "gaea",
            "active": True,
            "products": ["Septica"],
            "business": {
                "deploy_status": "near-ready",
                "current_mrr": 0,
                "target_mrr": 1000,
            }
        }

    if "vto" not in domains and (CONTEXT_DIR / "codeswiftr-com" / "forge-tryon-context.md").exists():
        domains["vto"] = {
            "display_name": "VTO (Virtual Try-On)",
            "owner": "nova",
            "active": True,
            "products": ["forge-tryon"],
            "business": {
                "deploy_status": "deploy-ready",
                "current_mrr": 0,
                "target_mrr": 15000,
            }
        }

    return domains


def count_kills() -> int:
    """Count kill records in .forge/kills/ (excluding TEMPLATE.md)."""
    if not KILLS_DIR.exists():
        return 0
    kills = [
        f for f in KILLS_DIR.glob("*.md")
        if f.name.upper() != "TEMPLATE.MD"
    ]
    return len(kills)


def get_recent_commits(n: int = 10) -> list[dict]:
    """Get recent git commits as list of {hash, date, message}."""
    try:
        result = subprocess.run(
            ["git", "log", f"--pretty=format:%h|%ad|%s", f"--date=short", f"-{n}"],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
            timeout=10,
        )
        commits = []
        for line in result.stdout.strip().splitlines():
            if "|" in line:
                parts = line.split("|", 2)
                if len(parts) == 3:
                    commits.append({
                        "hash": parts[0].strip(),
                        "date": parts[1].strip(),
                        "message": parts[2].strip(),
                    })
        return commits
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return []


def get_constitution_summary(domain_key: str) -> str | None:
    """Extract mission line from constitution.md if present."""
    for subdir in [domain_key]:
        path = CONTEXT_DIR / subdir / "constitution.md"
        if path.exists():
            text = path.read_text(errors="replace")
            for line in text.splitlines():
                line = line.strip()
                if line.startswith(">") and len(line) > 3:
                    return line.lstrip("> ").strip()
    return None


def get_lead_context_snippet(domain_key: str) -> str | None:
    """Extract current state summary from lead-context.md."""
    path = CONTEXT_DIR / domain_key / "lead-context.md"
    if not path.exists():
        return None
    text = path.read_text(errors="replace")
    # Find "## Current State" section and grab first meaningful bullet
    in_section = False
    lines = []
    for line in text.splitlines():
        if line.strip().startswith("## Current State"):
            in_section = True
            continue
        if in_section:
            if line.startswith("##"):
                break
            stripped = line.strip()
            if stripped.startswith("- ") or stripped.startswith("* "):
                bullet = stripped.lstrip("-* ").strip()
                if bullet:
                    lines.append(bullet)
                if len(lines) >= 2:
                    break
    return " · ".join(lines) if lines else None


def get_revenue_path(domain_key: str) -> str:
    """Extract revenue path from context files or use defaults."""
    search_files = [
        CONTEXT_DIR / domain_key / "lead-context.md",
        CONTEXT_DIR / domain_key / "constitution.md",
        CONTEXT_DIR / "codeswiftr-com" / "forge-tryon-context.md" if domain_key == "vto" else None
    ]

    for path in filter(None, search_files):
        if path.exists():
            text = path.read_text(errors="replace")
            for line in text.splitlines():
                l = line.strip().lower()
                if "revenue path:" in l or "monetization:" in l or "path to first dollar:" in l:
                    return line.split(":", 1)[1].strip()
                if "sprint:" in line and "revenue" in line:
                    return line.split(":", 1)[1].strip()

    # Default fallbacks based on domain logic
    fallbacks = {
        "codeswiftr-com": "SEO + B2B Team Sales",
        "brandfocus-ai": "Direct Outreach + Founder Wedge",
        "card-games": "Ads + Ad-Free IAP",
        "vto": "B2B Pilot EUR 5K-15K",
        "babybit-es": "App Store Subscription",
        "calmconnect-io": "Therapist SaaS Fees",
        "kindlyclub-app": "Family Subscription",
    }
    return fallbacks.get(domain_key, "TBD")


def domain_status_badge(domain: dict) -> tuple[str, str]:
    """Return (label, color) for domain active/killed/paused status."""
    active = domain.get("active", False)
    deploy = domain.get("business", {}).get("deploy_status", "unknown")

    if deploy == "killed":
        return ("KILLED", KILLED_BADGE_COLOR)
    if not active:
        if deploy == "paused":
            return ("PAUSED", PAUSED_BADGE_COLOR)
        return ("INACTIVE", INACTIVE_BADGE_COLOR)
    return ("ACTIVE", ACTIVE_BADGE_COLOR)


def deploy_status_color(deploy_status: str) -> str:
    color, _ = STATUS_COLORS.get(deploy_status, ("#6b7280", "gray"))
    return color


def format_mrr(value) -> str:
    try:
        v = int(value)
        if v == 0:
            return "$0"
        return f"${v:,}"
    except (TypeError, ValueError):
        return "$0"


def escape_html(text: str) -> str:
    return (
        text
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def build_domain_card(domain_key: str, domain: dict) -> str:
    display_name = escape_html(domain.get("display_name", domain_key))
    business = domain.get("business", {})
    deploy_status = business.get("deploy_status", "unknown")
    current_mrr = format_mrr(business.get("current_mrr", 0))
    target_mrr = format_mrr(business.get("target_mrr", 0))
    products = domain.get("products", []) or []
    owner = domain.get("owner", "—")

    badge_label, badge_color = domain_status_badge(domain)
    d_color = deploy_status_color(deploy_status)

    constitution = get_constitution_summary(domain_key)
    lead_note = get_lead_context_snippet(domain_key)
    rev_path = get_revenue_path(domain_key)

    products_html = ""
    if products:
        product_items = "".join(
            f'<li>{escape_html(str(p))}</li>' for p in products
        )
        products_html = f'<ul class="product-list">{product_items}</ul>'
    else:
        products_html = '<p class="no-products">No active products</p>'

    mission_html = ""
    if constitution:
        mission_html = f'<p class="mission">{escape_html(constitution)}</p>'

    lead_html = ""
    if lead_note:
        lead_html = f'<p class="lead-note">{escape_html(lead_note)}</p>'

    return f"""
    <div class="domain-card" data-status="{badge_label.lower()}">
      <div class="card-header">
        <span class="domain-name">{display_name}</span>
        <span class="badge" style="background:{badge_color}">{badge_label}</span>
      </div>
      {mission_html}
      {lead_html}
      <div class="card-body">
        <div class="meta-row">
          <span class="label">Owner</span>
          <span class="value">{escape_html(owner)}</span>
        </div>
        <div class="meta-row">
          <span class="label">Deploy</span>
          <span class="value" style="color:{d_color}">{escape_html(deploy_status)}</span>
        </div>
        <div class="meta-row">
          <span class="label">Rev Path</span>
          <span class="value" style="color:#60a5fa; font-weight:600;">{escape_html(rev_path)}</span>
        </div>
        <div class="meta-row">
          <span class="label">MRR</span>
          <span class="value mrr">{current_mrr} <span class="target">/ {target_mrr}</span></span>
        </div>
        <div class="products-section">
          <span class="label">Products</span>
          {products_html}
        </div>
      </div>
    </div>"""


def build_stats_bar(domains: dict, kills: int) -> str:
    total = len(domains)
    active = sum(1 for d in domains.values() if d.get("active", False))
    killed_count = sum(
        1 for d in domains.values()
        if d.get("business", {}).get("deploy_status") == "killed"
    )
    # Use file-based kill count if higher (catches granular product kills)
    killed_display = max(kills, killed_count)
    total_mrr = sum(
        int(d.get("business", {}).get("current_mrr", 0) or 0)
        for d in domains.values()
    )

    return f"""
    <div class="stats-bar">
      <div class="stat">
        <span class="stat-value">{total}</span>
        <span class="stat-label">Total Domains</span>
      </div>
      <div class="stat">
        <span class="stat-value" style="color:#22c55e">{active}</span>
        <span class="stat-label">Active</span>
      </div>
      <div class="stat">
        <span class="stat-value" style="color:#ef4444">{killed_display}</span>
        <span class="stat-label">Products Killed</span>
      </div>
      <div class="stat">
        <span class="stat-value" style="color:#22c55e">{format_mrr(total_mrr)}</span>
        <span class="stat-label">Total MRR</span>
      </div>
    </div>"""


def build_activity_section(commits: list[dict]) -> str:
    if not commits:
        return '<div class="activity"><p class="no-data">No recent commits found.</p></div>'

    rows = ""
    for c in commits:
        rows += f"""
      <tr>
        <td class="commit-hash">{escape_html(c['hash'])}</td>
        <td class="commit-date">{escape_html(c['date'])}</td>
        <td class="commit-msg">{escape_html(c['message'])}</td>
      </tr>"""

    return f"""
    <div class="activity">
      <table class="commit-table">
        <thead>
          <tr>
            <th>Hash</th>
            <th>Date</th>
            <th>Message</th>
          </tr>
        </thead>
        <tbody>{rows}
        </tbody>
      </table>
    </div>"""


def generate_html(domains: dict, kills: int, commits: list[dict]) -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    # Sort domains by active status then name
    sorted_keys = sorted(
        domains.keys(),
        key=lambda k: (not domains[k].get("active", False), domains[k].get("display_name", k))
    )

    cards_html = "\n".join(
        build_domain_card(k, domains[k]) for k in sorted_keys
    )
    stats_html = build_stats_bar(domains, kills)
    activity_html = build_activity_section(commits)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>FORGE Portfolio — The Bridge</title>
  <style>
    *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}

    :root {{
      --bg: #111827;
      --bg-card: #1f2937;
      --bg-card-hover: #263141;
      --border: #374151;
      --text: #f9fafb;
      --text-muted: #9ca3af;
      --text-dim: #6b7280;
      --green: #22c55e;
      --yellow: #eab308;
      --red: #ef4444;
      --font-mono: 'JetBrains Mono', 'Fira Code', 'Cascadia Code', ui-monospace, monospace;
      --font-sans: ui-sans-serif, system-ui, -apple-system, sans-serif;
    }}

    body {{
      background: var(--bg);
      color: var(--text);
      font-family: var(--font-mono);
      font-size: 13px;
      line-height: 1.6;
      padding: 0;
      min-height: 100vh;
    }}

    /* Header */
    .site-header {{
      background: #0d1117;
      border-bottom: 1px solid var(--border);
      padding: 20px 24px;
      display: flex;
      align-items: baseline;
      gap: 16px;
      flex-wrap: wrap;
    }}
    .site-title {{
      font-size: 18px;
      font-weight: 700;
      color: var(--text);
      letter-spacing: -0.3px;
    }}
    .site-subtitle {{
      font-size: 11px;
      color: var(--text-dim);
      font-family: var(--font-sans);
    }}
    .generated-at {{
      margin-left: auto;
      font-size: 11px;
      color: var(--text-dim);
      font-family: var(--font-sans);
    }}

    /* Main layout */
    .container {{
      max-width: 1400px;
      margin: 0 auto;
      padding: 24px 16px;
    }}

    /* Stats bar */
    .stats-bar {{
      display: flex;
      gap: 12px;
      flex-wrap: wrap;
      margin-bottom: 32px;
      background: var(--bg-card);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 16px 20px;
    }}
    .stat {{
      display: flex;
      flex-direction: column;
      align-items: center;
      flex: 1;
      min-width: 80px;
    }}
    .stat-value {{
      font-size: 24px;
      font-weight: 700;
      color: var(--text);
    }}
    .stat-label {{
      font-size: 10px;
      color: var(--text-muted);
      text-transform: uppercase;
      letter-spacing: 0.08em;
      font-family: var(--font-sans);
      margin-top: 2px;
    }}

    /* Section headers */
    .section-title {{
      font-size: 11px;
      text-transform: uppercase;
      letter-spacing: 0.1em;
      color: var(--text-dim);
      margin-bottom: 16px;
      padding-bottom: 8px;
      border-bottom: 1px solid var(--border);
    }}

    /* Domain grid */
    .domain-grid {{
      display: grid;
      grid-template-columns: 1fr;
      gap: 16px;
      margin-bottom: 40px;
    }}
    @media (min-width: 640px) {{
      .domain-grid {{ grid-template-columns: repeat(2, 1fr); }}
    }}
    @media (min-width: 1024px) {{
      .domain-grid {{ grid-template-columns: repeat(3, 1fr); }}
    }}
    @media (min-width: 1280px) {{
      .domain-grid {{ grid-template-columns: repeat(4, 1fr); }}
    }}

    /* Domain card */
    .domain-card {{
      background: var(--bg-card);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 16px;
      transition: background 0.15s, border-color 0.15s;
    }}
    .domain-card:hover {{
      background: var(--bg-card-hover);
      border-color: #4b5563;
    }}
    .domain-card[data-status="killed"] {{
      opacity: 0.55;
    }}
    .domain-card[data-status="inactive"] {{
      opacity: 0.65;
    }}

    .card-header {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 10px;
      gap: 8px;
    }}
    .domain-name {{
      font-size: 14px;
      font-weight: 700;
      color: var(--text);
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }}
    .badge {{
      font-size: 9px;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.1em;
      padding: 2px 7px;
      border-radius: 4px;
      color: #000;
      white-space: nowrap;
      flex-shrink: 0;
    }}

    .mission {{
      font-size: 11px;
      color: var(--text-muted);
      font-style: italic;
      margin-bottom: 8px;
      line-height: 1.5;
      font-family: var(--font-sans);
      display: -webkit-box;
      -webkit-line-clamp: 2;
      -webkit-box-orient: vertical;
      overflow: hidden;
    }}
    .lead-note {{
      font-size: 10px;
      color: #6b7280;
      margin-bottom: 10px;
      display: -webkit-box;
      -webkit-line-clamp: 2;
      -webkit-box-orient: vertical;
      overflow: hidden;
      font-family: var(--font-sans);
    }}

    .card-body {{
      display: flex;
      flex-direction: column;
      gap: 6px;
    }}
    .meta-row {{
      display: flex;
      align-items: baseline;
      gap: 8px;
    }}
    .label {{
      font-size: 10px;
      color: var(--text-dim);
      text-transform: uppercase;
      letter-spacing: 0.08em;
      min-width: 52px;
      flex-shrink: 0;
    }}
    .value {{
      font-size: 12px;
      color: var(--text-muted);
    }}
    .mrr {{
      color: var(--text);
      font-weight: 600;
    }}
    .target {{
      font-weight: 400;
      color: var(--text-dim);
      font-size: 11px;
    }}

    .products-section {{
      margin-top: 4px;
    }}
    .products-section .label {{
      display: block;
      margin-bottom: 4px;
    }}
    .product-list {{
      list-style: none;
      padding: 0;
      display: flex;
      flex-direction: column;
      gap: 2px;
    }}
    .product-list li {{
      font-size: 11px;
      color: #6b7280;
      padding-left: 10px;
      position: relative;
    }}
    .product-list li::before {{
      content: "›";
      position: absolute;
      left: 0;
      color: var(--text-dim);
    }}
    .no-products {{
      font-size: 11px;
      color: var(--text-dim);
      font-style: italic;
    }}

    /* Activity / commits */
    .activity-section {{
      margin-bottom: 40px;
    }}
    .activity {{
      background: var(--bg-card);
      border: 1px solid var(--border);
      border-radius: 8px;
      overflow-x: auto;
    }}
    .commit-table {{
      width: 100%;
      border-collapse: collapse;
    }}
    .commit-table th {{
      text-align: left;
      font-size: 10px;
      text-transform: uppercase;
      letter-spacing: 0.08em;
      color: var(--text-dim);
      padding: 10px 16px;
      border-bottom: 1px solid var(--border);
      font-family: var(--font-sans);
    }}
    .commit-table td {{
      padding: 8px 16px;
      border-bottom: 1px solid #1f2937;
      vertical-align: top;
    }}
    .commit-table tr:last-child td {{
      border-bottom: none;
    }}
    .commit-table tr:hover td {{
      background: #1a2332;
    }}
    .commit-hash {{
      color: #60a5fa;
      font-size: 12px;
      white-space: nowrap;
      font-family: var(--font-mono);
    }}
    .commit-date {{
      color: var(--text-dim);
      font-size: 11px;
      white-space: nowrap;
    }}
    .commit-msg {{
      color: var(--text-muted);
      font-size: 12px;
      max-width: 600px;
    }}
    .no-data {{
      padding: 20px;
      color: var(--text-dim);
      text-align: center;
    }}

    /* Footer */
    .site-footer {{
      text-align: center;
      padding: 24px;
      font-size: 10px;
      color: var(--text-dim);
      font-family: var(--font-sans);
      border-top: 1px solid var(--border);
    }}
  </style>
</head>
<body>

  <header class="site-header">
    <span class="site-title">FORGE Portfolio</span>
    <span class="site-subtitle">The Bridge — State Dashboard</span>
    <span class="generated-at">Generated: {timestamp}</span>
  </header>

  <main class="container">

    {stats_html}

    <section>
      <p class="section-title">Domains ({len(domains)})</p>
      <div class="domain-grid">
        {cards_html}
      </div>
    </section>

    <section class="activity-section">
      <p class="section-title">Recent Activity</p>
      {activity_html}
    </section>

  </main>

  <footer class="site-footer">
    FORGE Portfolio &middot; The Bridge &middot; {timestamp}
  </footer>

</body>
</html>"""


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate FORGE Portfolio Dashboard — The Bridge"
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=OUTPUT_PATH,
        help=f"Output HTML file path (default: {OUTPUT_PATH})",
    )
    args = parser.parse_args()

    output_path: Path = args.output.resolve()

    print("Loading domains from config/domains.yaml...")
    domains = load_domains()
    print(f"  Found {len(domains)} domains")

    print("Counting killed products...")
    kills = count_kills()
    print(f"  Found {kills} kill records")

    print("Fetching recent git commits...")
    commits = get_recent_commits(10)
    print(f"  Found {len(commits)} commits")

    print("Generating HTML...")
    html = generate_html(domains, kills, commits)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html, encoding="utf-8")
    print(f"  Dashboard written to: {output_path}")
    print(f"  File size: {len(html):,} bytes")
    print("Done.")


if __name__ == "__main__":
    main()
