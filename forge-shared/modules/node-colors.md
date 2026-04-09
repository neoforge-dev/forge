# FORGE Node Color Convention

**Last Updated:** 2026-03-20

All FORGE fleet nodes use a consistent color scheme derived from the
[Catppuccin Mocha](https://github.com/catppuccin/catppuccin) palette.
Colors appear in the tmux status bar, Glance TUI, forged dashboard,
and the CLI `NODE_CONFIG` dictionary.

---

## Color Assignments

| Node | Hex | Catppuccin Name | Role |
|------|-----|-----------------|------|
| **prya** | `#89b4fa` | Blue | Hub / forged daemon |
| **sati** | `#a6e3a1` | Green | Workhorse |
| **nova** | `#cba6f7` | Mauve | Power / iOS |
| **vega** | `#fab387` | Peach | Auxiliary |
| **gaea** | `#94e2d5` | Teal | Education / Mobile |

### Palette Reference

The full Catppuccin Mocha palette is documented at
<https://catppuccin.com/palette>. We use the "Mocha" (dark) variant
exclusively. The hex values above are taken directly from the palette
specification and should not be approximated.

---

## Where Colors Are Used

### 1. Go CLI Color Registry (`cmd/forge/internal/colors.go`)

The canonical color registry lives in `cmd/forge/internal/colors.go` (Go). The Python `cli_v2/node.py` was deleted in ADR-040 — do not reference it.

Each node has an `accent` hex mapped in the Go registry. Rich console output uses these values for status indicators, table borders, and node labels.

### 2. Glance TUI

The `forge glance` TUI reads `NODE_CONFIG` accent colors to render
per-node status cards. Each card's border and header text use the node's
assigned color.

### 3. forged HTMX Dashboard

The forged UI (`http://localhost:8081/ui`) applies node colors to:
- Agent grid cards (border accent)
- Node selector dropdown (color dot)
- Fleet topology view (connection lines)

### 4. tmux Status Bar

The tmux status bar on each node uses its accent color for the hostname
segment. See the integration snippet below.

---

## tmux Integration Snippet

Add the following to `~/.tmux.conf` (or source it from a shared dotfile).
It uses `%if` / `%elif` host matching so the same config works on every node.

```tmux
# ── FORGE node accent colors (Catppuccin Mocha) ──────────────────────
# Automatically sets status-bar accent based on hostname.
# Source: forge-shared/modules/node-colors.md

%if "#{==:#{host_short},prya}"
    # prya — Blue (#89b4fa) — Hub/forged daemon
    set -g status-style "bg=#1e1e2e,fg=#cdd6f4"
    set -g status-left "#[bg=#89b4fa,fg=#1e1e2e,bold] prya #[default] "
    set -g pane-active-border-style "fg=#89b4fa"
%elif "#{==:#{host_short},sati}"
    # sati — Green (#a6e3a1) — Workhorse
    set -g status-style "bg=#1e1e2e,fg=#cdd6f4"
    set -g status-left "#[bg=#a6e3a1,fg=#1e1e2e,bold] sati #[default] "
    set -g pane-active-border-style "fg=#a6e3a1"
%elif "#{==:#{host_short},nova}"
    # nova — Mauve (#cba6f7) — Power/iOS
    set -g status-style "bg=#1e1e2e,fg=#cdd6f4"
    set -g status-left "#[bg=#cba6f7,fg=#1e1e2e,bold] nova #[default] "
    set -g pane-active-border-style "fg=#cba6f7"
%elif "#{==:#{host_short},code-vega}"
    # vega — Peach (#fab387) — Auxiliary
    set -g status-style "bg=#1e1e2e,fg=#cdd6f4"
    set -g status-left "#[bg=#fab387,fg=#1e1e2e,bold] vega #[default] "
    set -g pane-active-border-style "fg=#fab387"
%elif "#{==:#{host_short},gaea}"
    # gaea — Teal (#94e2d5) — Education/Mobile
    set -g status-style "bg=#1e1e2e,fg=#cdd6f4"
    set -g status-left "#[bg=#94e2d5,fg=#1e1e2e,bold] gaea #[default] "
    set -g pane-active-border-style "fg=#94e2d5"
%else
    # Unknown node — default Catppuccin surface
    set -g status-style "bg=#1e1e2e,fg=#cdd6f4"
    set -g status-left "#[bg=#585b70,fg=#cdd6f4,bold] #{host_short} #[default] "
    set -g pane-active-border-style "fg=#585b70"
%endif

# Background color for all nodes: Catppuccin Mocha Base (#1e1e2e)
# Text color for all nodes: Catppuccin Mocha Text (#cdd6f4)
```

**Notes:**
- `#{host_short}` is the tmux equivalent of `hostname -s`.
- Vega's actual hostname is `code-vega`, hence the match on that string.
- The `%if` / `%elif` syntax requires tmux 3.0+ (all FORGE nodes meet this).
- To reload after editing: `tmux source-file ~/.tmux.conf`.

---

## Model / Agent Accent Colors

Model and agent type identifiers in the CLI also receive subtle Catppuccin Mocha
color hints. These are applied via prefix matching so variants like `kimi-2` or
`gemini-pro` resolve correctly.

| Model / Provider | Hex | ANSI 256 | Catppuccin Name | Reasoning |
|-----------------|-----|----------|-----------------|-----------|
| `claude` / Anthropic | `#cba6f7` | 141 | Mauve | Anthropic brand purple |
| `gemini` / Google | `#89dceb` | 117 | Sky | Google blue-ish |
| `kimi` / Moonshot | `#f5c2e7` | 218 | Pink | Moonshot pink |
| `minimax` | `#f9e2af` | 223 | Yellow | Warm, approachable |
| `opencode` | `#a6e3a1` | 114 | Green | Open-source green |
| `codex` / OpenAI | `#fab387` | 216 | Peach | OpenAI warm |
| `cursor` | `#89b4fa` | 111 | Blue | Cursor brand blue |
| `pi` | `#94e2d5` | 79 | Teal | Friendly, calm |
| `glm` / Zhipu | `#f38ba8` | 211 | Red | Zhipu red |
| `kilo` | `#b4befe` | 147 | Lavender | Soft, distinctive |

The Go registry lives in `cmd/forge/internal/colors.go`.

### Color Application Rules

- Colors are applied **only** to node name and agent/model identifier columns.
- Entire rows, JSON output, and CSV output are **never** colored.
- The `NO_COLOR` environment variable disables all color output (https://no-color.org/).
- Colors appear in: `forge status`, `forge agent list`, `forge fleet list`, `forge node list`.

---

## Adding a New Node

1. Choose an unused Catppuccin Mocha color from the palette.
2. Add the node to `nodeColors` in `cmd/forge/internal/colors.go`.
4. Add a `%elif` block to the tmux snippet above.
5. Update this document with the new mapping.
6. If the forged dashboard uses hardcoded node lists, update those as well.
