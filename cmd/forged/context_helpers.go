package main

import (
	"context"
	"database/sql"
	"encoding/json"
	"log"
)

// findLatestEnvelopeForAgent finds the latest envelope for an agent (lowercase for internal use)
func findLatestEnvelopeForAgent(ctx context.Context, agentID string) (*ContextEnvelope, error) {
	db := getDBConn()
	if db == nil {
		return nil, sql.ErrNoRows
	}

	var content string
	err := db.QueryRowContext(ctx, `
		SELECT content FROM context_envelopes
		WHERE agent_id = ?
		ORDER BY created_at DESC
		LIMIT 1
	`, agentID).Scan(&content)

	if err != nil {
		return nil, err
	}

	var envelope ContextEnvelope
	if err := json.Unmarshal([]byte(content), &envelope); err != nil {
		return nil, err
	}

	return &envelope, nil
}

// getEnvelopeByID retrieves an envelope by ID (lowercase for internal use)
func getEnvelopeByID(ctx context.Context, envelopeID string) (*ContextEnvelope, error) {
	db := getDBConn()
	if db == nil {
		return nil, sql.ErrNoRows
	}

	var content string
	err := db.QueryRowContext(ctx, `
		SELECT content FROM context_envelopes WHERE id = ?
	`, envelopeID).Scan(&content)

	if err != nil {
		return nil, err
	}

	var envelope ContextEnvelope
	if err := json.Unmarshal([]byte(content), &envelope); err != nil {
		return nil, err
	}

	return &envelope, nil
}

// sendBootstrapToAgent sends bootstrap context to an agent
func sendBootstrapToAgent(agentID, taskID string, envelope *ContextEnvelope) {
	log.Printf("[Bootstrap] Sending context to agent %s for task %s (envelope: %s)",
		agentID, taskID, envelope.ID)
	// In a real implementation, this would dispatch the envelope to the agent
	// via WebSocket or other mechanism
}
