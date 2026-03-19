//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"testing"
)

// ---------------------------------------------------------------------------
// Wave 205: patrol.go — PatrolSystem + pure helpers
// Targets:
//   NewPatrolSystem          (patrol.go:46)  — non-nil
//   PatrolSystem.SetContextManager (patrol.go:56)
//   PatrolSystem.SetRoyalJelly     (patrol.go:61)
//   PatrolSystem.AddContextPatrol  (patrol.go:66)
//   StandardPatrols          (patrol.go:73)  — non-empty
//   PatrolSystem.ListPatrols (patrol.go:866) — matches StandardPatrols
//   PatrolSystem.RunPatrolByID(patrol.go:871) — not found
//   blastRadiusFromResult    (patrol.go:1074) — empty/valid JSON/no fields
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// NewPatrolSystem
// ---------------------------------------------------------------------------

func TestWave205_NewPatrolSystem_NonNil(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	ps := NewPatrolSystem(db)
	if ps == nil {
		t.Error("expected non-nil PatrolSystem")
	}
}

// ---------------------------------------------------------------------------
// SetContextManager / SetRoyalJelly / AddContextPatrol
// ---------------------------------------------------------------------------

func TestWave205_SetContextManager_NilNoPanic(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	ps := NewPatrolSystem(db)
	ps.SetContextManager(nil) // nil is acceptable — just must not panic
}

func TestWave205_SetRoyalJelly_NoPanic(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	ps := NewPatrolSystem(db)
	rj := NewRoyalJelly(db, nil)
	ps.SetRoyalJelly(rj)
}

// ---------------------------------------------------------------------------
// StandardPatrols
// ---------------------------------------------------------------------------

func TestWave205_StandardPatrols_NonEmpty(t *testing.T) {
	patrols := StandardPatrols()
	if len(patrols) == 0 {
		t.Error("expected non-empty StandardPatrols list")
	}
	// Each patrol must have a non-empty ID and Schedule > 0
	for _, p := range patrols {
		if p.ID == "" {
			t.Errorf("patrol has empty ID: %+v", p)
		}
		if p.Schedule == 0 {
			t.Errorf("patrol %q has zero schedule", p.ID)
		}
	}
}

// ---------------------------------------------------------------------------
// ListPatrols
// ---------------------------------------------------------------------------

func TestWave205_ListPatrols_MatchesStandard(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	ps := NewPatrolSystem(db)
	patrols := ps.ListPatrols()
	standard := StandardPatrols()
	if len(patrols) != len(standard) {
		t.Errorf("expected %d patrols, got %d", len(standard), len(patrols))
	}
}

// ---------------------------------------------------------------------------
// RunPatrolByID — not found
// ---------------------------------------------------------------------------

func TestWave205_RunPatrolByID_NotFound(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	ps := NewPatrolSystem(db)
	found := ps.RunPatrolByID("nonexistent-patrol-id-xyz")
	if found {
		t.Error("expected RunPatrolByID to return false for nonexistent ID")
	}
}

// ---------------------------------------------------------------------------
// blastRadiusFromResult
// ---------------------------------------------------------------------------

func TestWave205_BlastRadiusFromResult_Empty(t *testing.T) {
	r := blastRadiusFromResult("")
	if r != 1 {
		t.Errorf("expected 1 for empty result, got %d", r)
	}
}

func TestWave205_BlastRadiusFromResult_FilesChanged(t *testing.T) {
	r := blastRadiusFromResult(`{"files_changed": 5}`)
	if r != 5 {
		t.Errorf("expected 5 for files_changed=5, got %d", r)
	}
}

func TestWave205_BlastRadiusFromResult_BlastRadiusField(t *testing.T) {
	r := blastRadiusFromResult(`{"blast_radius": 3}`)
	if r != 3 {
		t.Errorf("expected 3 for blast_radius=3, got %d", r)
	}
}

func TestWave205_BlastRadiusFromResult_InvalidJSON(t *testing.T) {
	r := blastRadiusFromResult("not-json")
	if r != 1 {
		t.Errorf("expected 1 fallback for invalid JSON, got %d", r)
	}
}

func TestWave205_BlastRadiusFromResult_NoFields(t *testing.T) {
	r := blastRadiusFromResult(`{"other": "field"}`)
	if r != 1 {
		t.Errorf("expected 1 fallback for JSON with no relevant fields, got %d", r)
	}
}
