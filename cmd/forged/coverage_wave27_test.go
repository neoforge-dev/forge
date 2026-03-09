//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"bytes"
	"context"
	"database/sql"
	"fmt"
	"os"
	"testing"
	"time"
)

// Wave 27: completion.go HandleCompletion + patrol.go executeContextPatrol (previously 0%)

func TestHandleCompletion_Bash_W27(t *testing.T) {
	cm := NewCompletionManager()
	// Capture stdout
	old := os.Stdout
	r, w, _ := os.Pipe()
	os.Stdout = w

	done := make(chan struct{})
	var buf bytes.Buffer
	go func() {
		defer close(done)
		buf.ReadFrom(r)
	}()

	cm.HandleCompletion([]string{"bash"})

	w.Close()
	os.Stdout = old
	<-done

	if len(buf.String()) == 0 {
		t.Error("expected non-empty bash completion output")
	}
}

func TestHandleCompletion_Zsh_W27(t *testing.T) {
	cm := NewCompletionManager()
	old := os.Stdout
	r, w, _ := os.Pipe()
	os.Stdout = w

	done := make(chan struct{})
	var buf bytes.Buffer
	go func() {
		defer close(done)
		buf.ReadFrom(r)
	}()

	cm.HandleCompletion([]string{"zsh"})

	w.Close()
	os.Stdout = old
	<-done

	if len(buf.String()) == 0 {
		t.Error("expected non-empty zsh completion output")
	}
}

func TestHandleCompletion_Fish_W27(t *testing.T) {
	cm := NewCompletionManager()
	old := os.Stdout
	r, w, _ := os.Pipe()
	os.Stdout = w

	done := make(chan struct{})
	var buf bytes.Buffer
	go func() {
		defer close(done)
		buf.ReadFrom(r)
	}()

	cm.HandleCompletion([]string{"fish"})

	w.Close()
	os.Stdout = old
	<-done

	if len(buf.String()) == 0 {
		t.Error("expected non-empty fish completion output")
	}
}

func TestNewPatrolSystem_W27(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	ps := NewPatrolSystem(db)
	if ps == nil {
		t.Fatal("expected non-nil PatrolSystem")
	}
	if ps.stop == nil {
		t.Error("expected non-nil stop channel")
	}
}

func TestExecuteContextPatrol_W27(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	ps := NewPatrolSystem(db)

	executed := make(chan struct{}, 1)
	patrol := ContextAwarePatrol{
		Patrol: Patrol{
			ID:       "test-context-patrol",
			Name:     "Test Context Patrol",
			Schedule: 1 * time.Hour,
			Action: func(ctx context.Context, db *sql.DB) error {
				select {
				case executed <- struct{}{}:
				default:
				}
				return nil
			},
		},
	}

	ps.executeContextPatrol(patrol)

	select {
	case <-executed:
		// good
	case <-time.After(5 * time.Second):
		t.Error("executeContextPatrol action was not called")
	}
}

func TestExecuteContextPatrol_Error_W27(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	ps := NewPatrolSystem(db)

	// Patrol that returns an error — should not panic
	patrol := ContextAwarePatrol{
		Patrol: Patrol{
			ID:       "test-error-patrol",
			Name:     "Test Error Patrol",
			Schedule: 1 * time.Hour,
			Action: func(ctx context.Context, db *sql.DB) error {
				return fmt.Errorf("simulated patrol error")
			},
		},
	}

	// Should not panic even with error
	ps.executeContextPatrol(patrol)
}
