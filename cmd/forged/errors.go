package main

import (
	"encoding/json"
	"fmt"
	"os"
)

// ErrorCode represents a standardized error code for programmatic handling
type ErrorCode string

const (
	ErrCodeNotFound     ErrorCode = "NOT_FOUND"
	ErrCodeInvalidState ErrorCode = "INVALID_STATE"
	ErrCodeUnauthorized ErrorCode = "UNAUTHORIZED"
	ErrCodeLeaseExpired ErrorCode = "LEASE_EXPIRED"
	ErrCodeDuplicate    ErrorCode = "DUPLICATE"
	ErrCodeTimeout      ErrorCode = "TIMEOUT"
	ErrCodeValidation   ErrorCode = "VALIDATION_ERROR"
	ErrCodeInternal     ErrorCode = "INTERNAL_ERROR"
	ErrCodeUnavailable  ErrorCode = "SERVICE_UNAVAILABLE"
)

// Exit codes for UNIX compatibility
const (
	ExitSuccess      = 0
	ExitGeneralError = 1
	ExitNotFound     = 2
	ExitInvalidState = 3
	ExitUnauthorized = 4
	ExitLeaseExpired = 5
	ExitDuplicate    = 6
	ExitTimeout      = 7
	ExitValidation   = 8
	ExitInternal     = 9
	ExitUnavailable  = 10
)

// ForgeError is a standardized error type for programmatic handling
type ForgeError struct {
	Code       ErrorCode `json:"code"`
	Message    string    `json:"message"`
	Details    string    `json:"details,omitempty"`
	TaskID     string    `json:"task_id,omitempty"`
	Suggestion string    `json:"suggestion,omitempty"`
}

func (e *ForgeError) Error() string {
	if e.Suggestion != "" {
		return fmt.Sprintf("[%s] %s\n\nSuggestion: %s", e.Code, e.Message, e.Suggestion)
	}
	return fmt.Sprintf("[%s] %s", e.Code, e.Message)
}

// JSON returns the error as a JSON string
func (e *ForgeError) JSON() string {
	data, _ := json.MarshalIndent(e, "", "  ")
	return string(data)
}

// ExitCode returns the appropriate UNIX exit code
func (e *ForgeError) ExitCode() int {
	switch e.Code {
	case ErrCodeNotFound:
		return ExitNotFound
	case ErrCodeInvalidState:
		return ExitInvalidState
	case ErrCodeUnauthorized:
		return ExitUnauthorized
	case ErrCodeLeaseExpired:
		return ExitLeaseExpired
	case ErrCodeDuplicate:
		return ExitDuplicate
	case ErrCodeTimeout:
		return ExitTimeout
	case ErrCodeValidation:
		return ExitValidation
	case ErrCodeInternal:
		return ExitInternal
	case ErrCodeUnavailable:
		return ExitUnavailable
	default:
		return ExitGeneralError
	}
}

// IsForgeError checks if an error is a ForgeError
func IsForgeError(err error) (*ForgeError, bool) {
	if fe, ok := err.(*ForgeError); ok {
		return fe, true
	}
	return nil, false
}

// Helper constructors

func NewNotFoundError(resource, id string) error {
	return &ForgeError{
		Code:       ErrCodeNotFound,
		Message:    fmt.Sprintf("%s '%s' was not found", resource, id),
		Suggestion: fmt.Sprintf("Verify the %s identifier and try again, or create it if it does not exist.", resource),
	}
}

func NewInvalidStateError(taskID string, current, expected string) error {
	return &ForgeError{
		Code:       ErrCodeInvalidState,
		Message:    fmt.Sprintf("cannot perform operation: task %s is in state %s (expected %s)", taskID, current, expected),
		TaskID:     taskID,
		Suggestion: fmt.Sprintf("Wait for the task to reach the %s state or choose a different task to operate on.", expected),
	}
}

func NewUnauthorizedError(action string) error {
	return &ForgeError{
		Code:       ErrCodeUnauthorized,
		Message:    fmt.Sprintf("you are not authorized to %s", action),
		Suggestion: "Verify your FORGE credentials and permissions, or contact the system owner to grant access.",
	}
}

func NewLeaseExpiredError(taskID string) error {
	return &ForgeError{
		Code:       ErrCodeLeaseExpired,
		Message:    fmt.Sprintf("the lease has expired for task %s", taskID),
		TaskID:     taskID,
		Suggestion: "Acquire a new lease for this task before continuing, or re-queue the task if it should be picked up again.",
	}
}

func NewDuplicateError(resource, id string) error {
	return &ForgeError{
		Code:       ErrCodeDuplicate,
		Message:    fmt.Sprintf("%s '%s' already exists", resource, id),
		Suggestion: fmt.Sprintf("Use a different %s identifier, or update the existing record instead of creating a new one.", resource),
	}
}

func NewTimeoutError(operation string, duration string) error {
	return &ForgeError{
		Code:       ErrCodeTimeout,
		Message:    fmt.Sprintf("%s timed out after %s", operation, duration),
		Suggestion: "Check v3 server health, network connectivity, and system load, then retry with a longer timeout if needed.",
	}
}

func NewValidationError(field, reason string) error {
	return &ForgeError{
		Code:       ErrCodeValidation,
		Message:    fmt.Sprintf("validation failed for '%s': %s", field, reason),
		Suggestion: fmt.Sprintf("Correct the value for '%s' to satisfy the validation rules and try again.", field),
	}
}

func NewInternalError(details string) error {
	return &ForgeError{
		Code:       ErrCodeInternal,
		Message:    "an internal error occurred in forged",
		Details:    details,
		Suggestion: "This is likely a bug. Capture the full error details and logs, then report it to the FORGE maintainers.",
	}
}

func NewUnavailableError(service string) error {
	return &ForgeError{
		Code:       ErrCodeUnavailable,
		Message:    fmt.Sprintf("service '%s' is currently unavailable", service),
		Suggestion: fmt.Sprintf("Ensure %s is running, healthy, and reachable from this node, then retry the operation.", service),
	}
}

// ExitWithError prints the error and exits with the appropriate code
func ExitWithError(err error) {
	if fe, ok := IsForgeError(err); ok {
		fmt.Fprintln(os.Stderr, fe.Error())
		os.Exit(fe.ExitCode())
	}
	fmt.Fprintln(os.Stderr, err)
	os.Exit(ExitGeneralError)
}

// ExitWithJSON prints the error as JSON and exits with the appropriate code
func ExitWithJSON(err error) {
	if fe, ok := IsForgeError(err); ok {
		fmt.Fprintln(os.Stderr, fe.JSON())
		os.Exit(fe.ExitCode())
	}
	data, _ := json.MarshalIndent(map[string]string{
		"code":    "UNKNOWN",
		"message": err.Error(),
	}, "", "  ")
	fmt.Fprintln(os.Stderr, string(data))
	os.Exit(ExitGeneralError)
}
