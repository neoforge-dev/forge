// Package errors provides standardized error types for FORGE CLI.
package errors

import (
	"fmt"
)

// Exit codes for CLI
const (
	ExitSuccess       = 0
	ExitGeneralError  = 1
	ExitMisuse        = 2
	ExitDaemonOffline = 3
	ExitPermission    = 4
	ExitNotFound      = 5
	ExitTimeout       = 6
)

// CLIError represents a structured CLI error
type CLIError struct {
	Code    int
	Type    string
	Message string
	Details map[string]interface{}
}

func (e *CLIError) Error() string {
	return e.Message
}

// New creates a new CLI error
func New(code int, errType string, message string) *CLIError {
	return &CLIError{
		Code:    code,
		Type:    errType,
		Message: message,
		Details: make(map[string]interface{}),
	}
}

// WithDetail adds a detail to the error
func (e *CLIError) WithDetail(key string, value interface{}) *CLIError {
	e.Details[key] = value
	return e
}

// Common error constructors

func DaemonNotRunning() *CLIError {
	return New(ExitDaemonOffline, "daemon_offline",
		"FORGE daemon is not running. Start it with 'forge daemon start' or use --offline")
}

func NotFound(resourceType string, id string) *CLIError {
	return New(ExitNotFound, "not_found",
		fmt.Sprintf("%s not found: %s", resourceType, id))
}

func PermissionDenied(action string) *CLIError {
	return New(ExitPermission, "permission_denied",
		fmt.Sprintf("Permission denied: %s", action))
}

func Timeout(operation string) *CLIError {
	return New(ExitTimeout, "timeout",
		fmt.Sprintf("Operation timed out: %s", operation))
}
