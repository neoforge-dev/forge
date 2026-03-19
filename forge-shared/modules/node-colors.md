# FORGE Node Color Convention

**Last Updated:** 2026-02-28

All FORGE fleet nodes use a consistent color scheme derived from the
[Catppuccin Mocha](https://github.com/catppuccin/catppuccin) palette.
Colors appear in the tmux status bar, Glance TUI, Command Center dashboard,
and the CLI `NODE_CONFIG` dictionary.

---

## Color Assignments

| Node | Hex | Catppuccin Name | Role |
|------|-----|-----------------|------|
| **node-1** | `#89b4fa` | Blue | Hub / Command Center |
| **node-2** | `#a6e3a1` | Green | Workhorse |
| **node-3** | `#cba6f7` | Mauve | Power / iOS |
| **node-4** | `#fab387` | Peach | Auxiliary |
| **node-5** | `#94e2d5` | Teal | Education / Mobile |

### Palette Reference

The full Catppuccin Mocha palette is documented at
<https://catppuccin.com/palette>. We use the "Mocha" (dark) variant
exclusively. The hex values above are taken directly from the palette
specification and should not be approximated.

---

## Where Colors Are Used

### 1. CLI `NODE_CONFIG` (`harness/forge_harness/cli_v2/node.py`)

Each node entry has an `accent` field storing the hex code:

```python
NODE_CONFIG = {
    "node-1": {
        "accent": "#89b4fa",   # Catppuccin Mocha Blue
        ...
    },
    "node-2": {
        "accent": "#a6e3a1",   # Catppuccin Mocha Green
        ...
    },
    "node-3": {
        "accent": "#cba6f7",   # Catppuccin Mocha Mauve
        ...
    },
    "node-4": {
        "accent": "#fab387",   # Catppuccin Mocha Peach
        ...
    },
    "node-5": {
        "accent": "#94e2d5",   # Catppuccin Mocha Teal
        ...
    },
}
```

Rich console output uses these values for status indicators, table borders,
and node labels.

### 2. Glance TUI

The `forge glance` TUI reads `NODE_CONFIG` accent colors to render
per-node status cards. Each card's border and header text use the node's
assigned color.

### 3. Command Center Dashboard

The CC frontend (`harness/command_center/`) applies node colors to:
- Agent grid cards (border accent)
- Node selector dropdown (color dot)
- Fleet topology view (connection lines)

CSS custom properties are generated from the hex values at runtime.

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

%if "#{==:#{host_short},node-1}"
    # node-1 — Blue (#89b4fa) — Hub/Command Center
    set -g status-style "bg=#1e1e2e,fg=#cdd6f4"
    set -g status-left "#[bg=#89b4fa,fg=#1e1e2e,bold] node-1 #[default] "
    set -g pane-active-border-style "fg=#89b4fa"
%elif "#{==:#{host_short},node-2}"
    # node-2 — Green (#a6e3a1) — Workhorse
    set -g status-style "bg=#1e1e2e,fg=#cdd6f4"
    set -g status-left "#[bg=#a6e3a1,fg=#1e1e2e,bold] node-2 #[default] "
    set -g pane-active-border-style "fg=#a6e3a1"
%elif "#{==:#{host_short},node-3}"
    # node-3 — Mauve (#cba6f7) — Power/iOS
    set -g status-style "bg=#1e1e2e,fg=#cdd6f4"
    set -g status-left "#[bg=#cba6f7,fg=#1e1e2e,bold] node-3 #[default] "
    set -g pane-active-border-style "fg=#cba6f7"
%elif "#{==:#{host_short},code-node-4}"
    # node-4 — Peach (#fab387) — Auxiliary
    set -g status-style "bg=#1e1e2e,fg=#cdd6f4"
    set -g status-left "#[bg=#fab387,fg=#1e1e2e,bold] node-4 #[default] "
    set -g pane-active-border-style "fg=#fab387"
%elif "#{==:#{host_short},node-5}"
    # node-5 — Teal (#94e2d5) — Education/Mobile
    set -g status-style "bg=#1e1e2e,fg=#cdd6f4"
    set -g status-left "#[bg=#94e2d5,fg=#1e1e2e,bold] node-5 #[default] "
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
- Vega's actual hostname is `code-node-4`, hence the match on that string.
- The `%if` / `%elif` syntax requires tmux 3.0+ (all FORGE nodes meet this).
- To reload after editing: `tmux source-file ~/.tmux.conf`.

---

## Adding a New Node

1. Choose an unused Catppuccin Mocha color from the palette.
2. Add the entry to `NODE_CONFIG` in `harness/forge_harness/cli_v2/node.py`.
3. Add a `%elif` block to the tmux snippet above.
4. Update this document with the new mapping.
5. If the CC dashboard uses hardcoded node lists, update those as well.
