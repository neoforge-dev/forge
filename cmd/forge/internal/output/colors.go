// Package output provides colored printing utilities
package output

import (
	"fmt"
	"os"
)

// ANSI color codes
const (
	colorReset  = "\033[0m"
	colorRed    = "\033[31m"
	colorGreen  = "\033[32m"
	colorYellow = "\033[33m"
	colorBlue   = "\033[34m"
	colorCyan   = "\033[36m"
)

// PrintError prints a red error message
func PrintError(msg string, args ...interface{}) {
	fmt.Fprintf(os.Stderr, "%s❌ Error:%s %s\n", colorRed, colorReset, fmt.Sprintf(msg, args...))
}

// PrintWarning prints a yellow warning message
func PrintWarning(msg string, args ...interface{}) {
	fmt.Fprintf(os.Stderr, "%s⚠️  Warning:%s %s\n", colorYellow, colorReset, fmt.Sprintf(msg, args...))
}

// PrintSuccess prints a green success message
func PrintSuccess(msg string, args ...interface{}) {
	fmt.Printf("%s✅ %s%s\n", colorGreen, fmt.Sprintf(msg, args...), colorReset)
}

// PrintInfo prints a blue info message
func PrintInfo(msg string, args ...interface{}) {
	fmt.Printf("%sℹ️  %s%s\n", colorBlue, fmt.Sprintf(msg, args...), colorReset)
}

// PrintProgress prints a cyan progress message
func PrintProgress(msg string, args ...interface{}) {
	fmt.Printf("%s⏳ %s%s\n", colorCyan, fmt.Sprintf(msg, args...), colorReset)
}

// Color helpers for strings
func Red(s string) string   { return colorRed + s + colorReset }
func Green(s string) string { return colorGreen + s + colorReset }
func Yellow(s string) string { return colorYellow + s + colorReset }
func Blue(s string) string  { return colorBlue + s + colorReset }
func Cyan(s string) string  { return colorCyan + s + colorReset }
