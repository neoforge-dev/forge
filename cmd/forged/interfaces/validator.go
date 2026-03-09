package interfaces

import (
	"fmt"
	"reflect"
)

// InterfaceValidator checks that implementations satisfy interfaces.
// Call this at startup to ensure all interfaces are properly implemented.
type InterfaceValidator struct {
	errors []error
}

// NewValidator creates a new interface validator.
func NewValidator() *InterfaceValidator {
	return &InterfaceValidator{
		errors: make([]error, 0),
	}
}

// Validate checks that an implementation satisfies an interface.
// Usage: validator.Validate((*Interface)(nil), &Implementation{})
func (v *InterfaceValidator) Validate(interfaceType, implementation any) *InterfaceValidator {
	interfaceT := reflect.TypeOf(interfaceType).Elem()
	implT := reflect.TypeOf(implementation)

	// If implementation is a pointer, dereference it
	if implT.Kind() == reflect.Ptr {
		implT = implT.Elem()
	}

	// Check if implementation implements interface
	if !implT.Implements(interfaceT) {
		v.errors = append(v.errors,
			fmt.Errorf("%s does not implement %s", implT.Name(), interfaceT.Name()))
	}

	return v
}

// ValidatePointer checks that a pointer implementation satisfies an interface.
func (v *InterfaceValidator) ValidatePointer(interfaceType, implementation any) *InterfaceValidator {
	interfaceT := reflect.TypeOf(interfaceType).Elem()
	implT := reflect.TypeOf(implementation)

	// Check if pointer to implementation implements interface
	if !implT.Implements(interfaceT) {
		v.errors = append(v.errors,
			fmt.Errorf("%s does not implement %s", implT.String(), interfaceT.Name()))
	}

	return v
}

// HasErrors returns true if any validation errors were found.
func (v *InterfaceValidator) HasErrors() bool {
	return len(v.errors) > 0
}

// Errors returns all validation errors.
func (v *InterfaceValidator) Errors() []error {
	return v.errors
}

// Error returns a formatted error with all validation failures.
func (v *InterfaceValidator) Error() string {
	if !v.HasErrors() {
		return ""
	}

	msg := "Interface validation failed:\n"
	for _, err := range v.errors {
		msg += fmt.Sprintf("  - %v\n", err)
	}
	return msg
}

// ReturnError returns an error if there are validation errors.
func (v *InterfaceValidator) ReturnError() error {
	if v.HasErrors() {
		return fmt.Errorf("%s", v.Error())
	}
	return nil
}

// Compile-time interface assertions for common types.
// These ensure that implementations satisfy interfaces at compile time.
// Copy these to your implementation files.

// Example usage in implementation files:
//
// var _ interfaces.ContextManager = (*ContextManager)(nil)
// var _ interfaces.RoyalJelly = (*RoyalJelly)(nil)
// var _ interfaces.PatrolSystem = (*PatrolSystem)(nil)
// var _ interfaces.TaskQueue = (*TaskStore)(nil)
