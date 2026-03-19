---
name: frontend-design
description: Create distinctive, production-grade frontend interfaces for FORGE MVPs. Builds landing pages, dashboards, and apps with high design quality while avoiding generic AI aesthetics. Supports React/Vite, Lit PWA, and HTMX stacks.
---


# Frontend Design for FORGE Portfolio

Create distinctive, production-grade frontend interfaces that convert visitors into customers. This skill guides creation of landing pages, dashboards, and web applications that avoid generic "AI slop" aesthetics while shipping fast.

## When to Use

- Building landing pages for any FORGE domain (codeswiftr, thebrightharbor, adguild, etc.)
- Creating dashboards for SaaS products (Tech Diligence, Interview Simulator, StudyFlow)
- Implementing PWA frontends with Lit or HTMX
- Designing React components for complex applications
- Refreshing existing UI to increase conversion rates

## Tech Stack Context

| Stack | Use Case | Projects |
|-------|----------|----------|
| **React + Vite + Tailwind** | Complex dashboards, SPAs | Interview Simulator, StudyFlow, Tech Diligence |
| **Lit + Vite** | Marketing landing pages, lightweight PWAs | All domain landing pages |
| **HTMX + Tailwind** | Server-rendered, interactive pages | Marketing API, simple tools |

## Design Thinking Process

Before coding, answer these questions to commit to a **BOLD aesthetic direction**:

### 1. Context Analysis
- **Domain**: Which FORGE brand? (codeswiftr = professional/dev, thebrightharbor = educational/warm, adguild = creative/bold, calmconnect = calming/wellness)
- **Purpose**: Landing page (convert), dashboard (retain), tool (utility)?
- **Audience**: CTOs/VCs (professional), Parents (trustworthy), Students (engaging), Developers (clean)?

### 2. Aesthetic Direction
Choose ONE dominant aesthetic per project:

| Aesthetic | Best For | Characteristics |
|-----------|----------|-----------------|
| **Tech Professional** | codeswiftr, leanvibe.dev | Dark mode, monospace accents, data visualizations, sharp corners |
| **Warm Educational** | thebrightharbor | Soft gradients, rounded elements, playful illustrations, accessibility-first |
| **Bold Creative** | adguild, brandfocus | High contrast, dynamic layouts, bold typography, unexpected interactions |
| **Calming Wellness** | calmconnect | Muted pastels, generous whitespace, organic shapes, slow animations |
| **Trust & Safety** | babybit, mumchef | Clean whites, soft blues/greens, clear hierarchy, reassuring copy |

### 3. Differentiation
What makes this interface MEMORABLE? Define ONE signature element:
- A unique micro-interaction
- A distinctive color treatment
- An unexpected layout choice
- A memorable typography pairing

## Frontend Aesthetics Guidelines

### Typography
**NEVER use**: Inter, Roboto, Arial, system-ui defaults, Open Sans

**DO use distinctive pairings**:
```css
/* Tech/Professional */
--font-display: 'JetBrains Mono', 'IBM Plex Mono', 'Space Mono';
--font-body: 'Satoshi', 'General Sans', 'Plus Jakarta Sans';

/* Educational/Friendly */
--font-display: 'Bricolage Grotesque', 'Outfit', 'Sora';
--font-body: 'Nunito', 'Quicksand', 'DM Sans';

/* Bold/Creative */
--font-display: 'Clash Display', 'Cabinet Grotesk', 'Bebas Neue';
--font-body: 'Manrope', 'Work Sans', 'Archivo';

/* Wellness/Calm */
--font-display: 'Fraunces', 'Newsreader', 'Lora';
--font-body: 'Source Sans 3', 'Karla', 'Atkinson Hyperlegible';
```

Load via Google Fonts or Fontsource. Always include `font-display: swap`.

### Color Systems

**Commit to a palette** - don't use safe defaults:

```css
/* Tech Dark (codeswiftr) */
--bg-primary: #0a0a0b;
--bg-secondary: #141416;
--accent-primary: #3b82f6;
--accent-secondary: #8b5cf6;
--text-primary: #fafafa;
--text-muted: #71717a;

/* Warm Light (thebrightharbor) */
--bg-primary: #fffbf5;
--bg-secondary: #fff7ed;
--accent-primary: #f97316;
--accent-secondary: #0ea5e9;
--text-primary: #1c1917;
--text-muted: #78716c;

/* Bold Contrast (adguild) */
--bg-primary: #fafaf9;
--accent-primary: #dc2626;
--accent-secondary: #000000;
--highlight: #fef08a;

/* Calm Wellness (calmconnect) */
--bg-primary: #f8fafc;
--bg-secondary: #f1f5f9;
--accent-primary: #0d9488;
--accent-secondary: #7c3aed;
--text-primary: #1e293b;
```

### Motion & Interactions

**High-impact moments** over scattered micro-interactions:

```css
/* Page load stagger - use sparingly but make it count */
@keyframes fadeInUp {
  from { opacity: 0; transform: translateY(20px); }
  to { opacity: 1; transform: translateY(0); }
}

.hero-element { animation: fadeInUp 0.6s ease-out; }
.hero-element:nth-child(2) { animation-delay: 0.1s; }
.hero-element:nth-child(3) { animation-delay: 0.2s; }

/* Hover states that surprise */
.cta-button {
  transition: all 0.2s cubic-bezier(0.34, 1.56, 0.64, 1);
}
.cta-button:hover {
  transform: translateY(-2px) scale(1.02);
  box-shadow: 0 10px 40px -10px var(--accent-primary);
}

/* Scroll-triggered reveals */
[data-reveal] {
  opacity: 0;
  transform: translateY(30px);
  transition: all 0.8s cubic-bezier(0.16, 1, 0.3, 1);
}
[data-reveal].visible {
  opacity: 1;
  transform: translateY(0);
}
```

### Spatial Composition

**Break the grid intentionally**:

```css
/* Asymmetric hero layout */
.hero-grid {
  display: grid;
  grid-template-columns: 1.2fr 0.8fr;
  gap: 4rem;
  align-items: center;
}

/* Overlapping elements for depth */
.feature-card {
  position: relative;
}
.feature-card::before {
  content: '';
  position: absolute;
  inset: -10px;
  background: var(--accent-primary);
  opacity: 0.1;
  border-radius: 1rem;
  transform: rotate(-2deg);
  z-index: -1;
}

/* Generous whitespace */
.section { padding: clamp(4rem, 10vw, 8rem) 0; }
.container { max-width: min(1200px, 90vw); margin: 0 auto; }
```

### Visual Atmosphere

**Create depth and texture**:

```css
/* Gradient mesh backgrounds */
.hero-bg {
  background:
    radial-gradient(ellipse at 20% 50%, rgba(59, 130, 246, 0.15) 0%, transparent 50%),
    radial-gradient(ellipse at 80% 20%, rgba(139, 92, 246, 0.1) 0%, transparent 40%),
    var(--bg-primary);
}

/* Subtle grain overlay */
.grain::after {
  content: '';
  position: fixed;
  inset: 0;
  background: url("data:image/svg+xml,%3Csvg viewBox='0 0 400 400' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.8'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)'/%3E%3C/svg%3E");
  opacity: 0.03;
  pointer-events: none;
  z-index: 1000;
}

/* Glass morphism for cards */
.glass-card {
  background: rgba(255, 255, 255, 0.7);
  backdrop-filter: blur(10px);
  border: 1px solid rgba(255, 255, 255, 0.2);
  border-radius: 1rem;
}
```

## Component Patterns

### Landing Page Hero (Conversion-Focused)

```jsx
// React + Tailwind example
function Hero() {
  return (
    <section className="relative min-h-[90vh] flex items-center overflow-hidden">
      {/* Gradient background */}
      <div className="absolute inset-0 bg-gradient-to-br from-slate-900 via-slate-800 to-slate-900" />
      <div className="absolute inset-0 bg-[radial-gradient(ellipse_at_top_right,_var(--tw-gradient-stops))] from-blue-600/20 via-transparent to-transparent" />

      <div className="container relative z-10">
        <div className="max-w-3xl">
          {/* Eyebrow */}
          <span className="inline-block px-3 py-1 text-sm font-medium text-blue-400 bg-blue-500/10 rounded-full mb-6 animate-fade-in">
            New: AI-Powered Analysis
          </span>

          {/* Headline - make it count */}
          <h1 className="text-5xl md:text-7xl font-bold text-white mb-6 leading-[1.1] tracking-tight">
            Technical Due Diligence
            <span className="block text-transparent bg-clip-text bg-gradient-to-r from-blue-400 to-purple-400">
              in Days, Not Weeks
            </span>
          </h1>

          {/* Subhead - one clear value prop */}
          <p className="text-xl text-slate-300 mb-8 max-w-xl">
            AI-powered code analysis that surfaces hidden risks before they become costly surprises. Trusted by VCs and CTOs.
          </p>

          {/* CTA - single clear action */}
          <div className="flex flex-wrap gap-4">
            <a href="#book" className="group inline-flex items-center gap-2 px-8 py-4 bg-blue-600 text-white font-semibold rounded-lg hover:bg-blue-500 transition-all hover:translate-y-[-2px] hover:shadow-xl hover:shadow-blue-600/25">
              Book Discovery Call
              <ArrowRight className="w-4 h-4 group-hover:translate-x-1 transition-transform" />
            </a>
            <a href="#how" className="px-8 py-4 text-slate-300 font-medium hover:text-white transition-colors">
              See How It Works
            </a>
          </div>
        </div>
      </div>
    </section>
  );
}
```

### Dashboard Card (Data-Dense)

```jsx
function MetricCard({ title, value, change, trend, chart }) {
  return (
    <div className="group relative bg-slate-800/50 backdrop-blur border border-slate-700/50 rounded-xl p-6 hover:border-slate-600/50 transition-colors">
      <div className="flex items-start justify-between mb-4">
        <div>
          <p className="text-sm text-slate-400 mb-1">{title}</p>
          <p className="text-3xl font-bold text-white tracking-tight">{value}</p>
        </div>
        <span className={`inline-flex items-center gap-1 px-2 py-1 text-xs font-medium rounded-full ${
          trend === 'up' ? 'text-emerald-400 bg-emerald-500/10' : 'text-red-400 bg-red-500/10'
        }`}>
          {trend === 'up' ? <TrendingUp className="w-3 h-3" /> : <TrendingDown className="w-3 h-3" />}
          {change}
        </span>
      </div>

      {/* Sparkline chart */}
      <div className="h-12 opacity-60 group-hover:opacity-100 transition-opacity">
        {chart}
      </div>
    </div>
  );
}
```

## Anti-Patterns to AVOID

### Generic AI Aesthetics
```css
/* NEVER do this */
font-family: Inter, system-ui, sans-serif; /* Generic */
background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); /* AI purple gradient */
border-radius: 8px; /* Safe, boring */
box-shadow: 0 4px 6px rgba(0,0,0,0.1); /* Default shadow */
```

### Cookie-Cutter Patterns
- Hero with centered text + two buttons + stock photo
- Purple-to-blue gradients on white backgrounds
- Cards with identical border-radius and shadows
- Generic "Get Started" / "Learn More" CTAs

### Conversion Killers
- Multiple competing CTAs
- Walls of text without visual hierarchy
- Low-contrast text
- Slow page loads (optimize images, lazy load)
- Missing social proof

## Output Checklist

- [ ] Distinctive typography pairing (not Inter/Roboto/Arial)
- [ ] Committed color palette with dominant + accent
- [ ] At least one memorable visual element
- [ ] Page load animation sequence
- [ ] Hover states that delight
- [ ] Mobile-responsive (test at 375px)
- [ ] Lighthouse score > 90 (performance)
- [ ] Single clear CTA per section
- [ ] Social proof visible above fold
- [ ] Fast time-to-interactive (< 3s)

## Integration with FORGE Workflow

1. **Reference domain design system** if exists: `{project}/docs/DESIGN_SYSTEM.md`
2. **Use existing components** from `marketing-template/` for landing pages
3. **Update progress** in `{project}/docs/progress.md` after implementation
4. **Test across breakpoints**: 375px (mobile), 768px (tablet), 1280px (desktop)
5. **Validate with Lighthouse** before deployment

## Quick Start Commands

```bash
# Landing page with Lit + Vite
cd marketing-template && npm run dev

# React dashboard
cd {project}/frontend && npm run dev

# Build for production
npm run build && npm run preview
```

