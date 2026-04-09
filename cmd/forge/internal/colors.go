// colors.go — Catppuccin Mocha color registry for FORGE CLI output.
//
// Node colors and model accent colors follow the Catppuccin Mocha palette.
// Reference: forge-shared/modules/node-colors.md
//
// Usage:
//
//	fmt.Printf("%s%s%s", colors.NodeColor("prya"), text, colors.ColorReset)
//
// Colors are applied only to specific identifiers (node names, agent IDs),
// never to entire rows or JSON/CSV output.
package internal

import (
	"fmt"
	"os"
	"strings"
)

// ColorReset resets the terminal foreground color.
const ColorReset = "\033[0m"

// nodeColors maps node hostnames to ANSI 256-color codes (Catppuccin Mocha).
var nodeColors = map[string]int{
	"prya": 111, // Blue   (#89b4fa)
	"sati": 114, // Green  (#a6e3a1)
	"nova": 141, // Mauve  (#cba6f7)
	"vega": 216, // Peach  (#fab387)
	"gaea": 79,  // Teal   (#94e2d5)
}

// modelColors maps model/agent name prefixes to ANSI 256-color codes (Catppuccin Mocha).
var modelColors = map[string]int{
	"claude":   141, // Mauve   (#cba6f7) — Anthropic brand purple
	"gemini":   117, // Sky     (#89dceb) — Google blue-ish
	"kimi":     218, // Pink    (#f5c2e7) — Moonshot pink
	"minimax":  223, // Yellow  (#f9e2af) — Warm, approachable
	"opencode": 114, // Green   (#a6e3a1) — Open-source green
	"codex":    216, // Peach   (#fab387) — OpenAI warm
	"cursor":   111, // Blue    (#89b4fa) — Cursor brand blue
	"pi":       79,  // Teal    (#94e2d5) — Friendly, calm
	"glm":      211, // Red     (#f38ba8) — Zhipu red
	"kilo":     147, // Lavender(#b4befe) — Soft, distinctive
}

// colorEnabled reports whether ANSI colors should be emitted.
// Honors the NO_COLOR convention (https://no-color.org/).
func colorEnabled() bool {
	return os.Getenv("NO_COLOR") == ""
}

// NodeColor returns the ANSI escape sequence to set the foreground color for
// the named node. Returns an empty string when NO_COLOR is set or the node is
// unknown.
func NodeColor(node string) string {
	if !colorEnabled() {
		return ""
	}
	// Normalize: strip domain suffix (e.g. "prya.local" → "prya")
	host := strings.SplitN(strings.ToLower(node), ".", 2)[0]
	if code, ok := nodeColors[host]; ok {
		return fmt.Sprintf("\033[38;5;%dm", code)
	}
	return ""
}

// ModelColor returns the ANSI escape sequence to set the foreground color for
// the named model or agent. Matching is prefix-based so "kimi-2" resolves to
// the "kimi" entry. Returns an empty string when NO_COLOR is set or the model
// is unknown.
func ModelColor(model string) string {
	if !colorEnabled() {
		return ""
	}
	name := strings.ToLower(model)
	// Exact match first.
	if code, ok := modelColors[name]; ok {
		return fmt.Sprintf("\033[38;5;%dm", code)
	}
	// Prefix match: "kimi-2" → "kimi", "gemini-pro" → "gemini", etc.
	for key, code := range modelColors {
		if strings.HasPrefix(name, key) {
			return fmt.Sprintf("\033[38;5;%dm", code)
		}
	}
	return ""
}

// ColorNode wraps text with the node color and a trailing reset.
// Returns plain text when colors are disabled or the node is unknown.
func ColorNode(node, text string) string {
	c := NodeColor(node)
	if c == "" {
		return text
	}
	return c + text + ColorReset
}

// ColorModel wraps text with the model/agent color and a trailing reset.
// Returns plain text when colors are disabled or the model is unknown.
func ColorModel(model, text string) string {
	c := ModelColor(model)
	if c == "" {
		return text
	}
	return c + text + ColorReset
}
