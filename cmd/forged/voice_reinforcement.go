//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"context"
	"database/sql"
	"encoding/json"
	"fmt"
	"log"
	"strings"
	"time"

	"github.com/oklog/ulid/v2"
)

// voiceCommandLog records every voice command for analysis and reinforcement.
type voiceCommandLog struct {
	ID         string
	SessionID  string
	RawText    string
	Intent     string
	Confidence float64
	Entities   map[string]string
	Executed   bool
	Success    bool
	Error      string
	Result     map[string]interface{}
	CreatedAt  time.Time
	ExecutedAt *time.Time
}

// logVoiceCommand inserts a voiceCommandLog into the voice_commands table.
func logVoiceCommand(db *sql.DB, entry voiceCommandLog) error {
	if db == nil {
		return fmt.Errorf("logVoiceCommand: db is nil — ensure daemon is running")
	}
	if entry.ID == "" {
		entry.ID = ulid.Make().String()
	}

	entitiesJSON := []byte("{}")
	if entry.Entities != nil {
		if b, err := json.Marshal(entry.Entities); err == nil {
			entitiesJSON = b
		}
	}
	resultJSON := []byte("null")
	if entry.Result != nil {
		if b, err := json.Marshal(entry.Result); err == nil {
			resultJSON = b
		}
	}

	_, err := db.Exec(`
		INSERT INTO voice_commands
			(id, session_id, raw_text, intent, confidence, entities,
			 executed, success, error, result, created_at, executed_at)
		VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
		entry.ID, entry.SessionID, entry.RawText, entry.Intent, entry.Confidence,
		string(entitiesJSON), entry.Executed, entry.Success, entry.Error,
		string(resultJSON), entry.CreatedAt, entry.ExecutedAt,
	)
	return err
}

// ---------------------------------------------------------------------------
// Voice analytics
// ---------------------------------------------------------------------------

// VoiceAnalytics holds aggregated statistics over the voice_commands table.
type VoiceAnalytics struct {
	TotalCommands       int              `json:"total_commands"`
	SuccessRate         float64          `json:"success_rate"`
	AvgConfidence       float64          `json:"avg_confidence"`
	TopIntents          map[string]int   `json:"top_intents"`
	FailedTerms         []FailedTerm     `json:"failed_terms"`
	PhoneticSuggestions []string         `json:"phonetic_suggestions"`
}

// FailedTerm is a raw text fragment that failed intent matching, plus diagnostics.
type FailedTerm struct {
	Term         string `json:"term"`
	Count        int    `json:"count"`
	NearestMatch string `json:"nearest_match"`
}

// getVoiceAnalytics queries the voice_commands table and returns aggregated stats.
func getVoiceAnalytics(db *sql.DB) (*VoiceAnalytics, error) {
	if db == nil {
		return nil, fmt.Errorf("getVoiceAnalytics: db is nil")
	}

	analytics := &VoiceAnalytics{
		TopIntents:          make(map[string]int),
		FailedTerms:         []FailedTerm{},
		PhoneticSuggestions: []string{},
	}

	// Total, success rate, average confidence — last 1000 commands.
	err := db.QueryRow(`
		SELECT
			COUNT(*),
			COALESCE(AVG(CASE WHEN success = 1 THEN 1.0 ELSE 0.0 END), 0),
			COALESCE(AVG(confidence), 0)
		FROM voice_commands
		ORDER BY created_at DESC
		LIMIT 1000`).Scan(
		&analytics.TotalCommands,
		&analytics.SuccessRate,
		&analytics.AvgConfidence,
	)
	if err != nil {
		return nil, fmt.Errorf("getVoiceAnalytics: aggregate query failed: %w", err)
	}

	// Top intents.
	rows, err := db.Query(`
		SELECT intent, COUNT(*) as cnt
		FROM voice_commands
		WHERE intent != ''
		GROUP BY intent
		ORDER BY cnt DESC
		LIMIT 20`)
	if err == nil {
		defer rows.Close()
		for rows.Next() {
			var intent string
			var cnt int
			if rows.Scan(&intent, &cnt) == nil {
				analytics.TopIntents[intent] = cnt
			}
		}
	}

	// Failed terms — commands with intent=unknown or success=0 with non-empty raw_text.
	failRows, err := db.Query(`
		SELECT raw_text, COUNT(*) as cnt
		FROM voice_commands
		WHERE (intent = 'unknown' OR success = 0) AND raw_text != ''
		GROUP BY raw_text
		ORDER BY cnt DESC
		LIMIT 20`)
	if err == nil {
		defer failRows.Close()
		vocab := defaultVoiceVocabulary
		allTerms := make([]string, 0, len(vocab.Agents)+len(vocab.Nodes)+len(vocab.Commands))
		allTerms = append(allTerms, vocab.Agents...)
		allTerms = append(allTerms, vocab.Nodes...)
		allTerms = append(allTerms, vocab.Commands...)

		phoneticSet := make(map[string]bool)
		for failRows.Next() {
			var rawText string
			var cnt int
			if failRows.Scan(&rawText, &cnt) != nil {
				continue
			}
			// Find nearest known term for each token in the failed text.
			tokens := strings.Fields(strings.ToLower(rawText))
			bestNearestMatch := ""
			bestConf := 0.0
			for _, tok := range tokens {
				if isStopWord(tok) {
					continue
				}
				if m, conf := matchTerm(tok, allTerms); conf > bestConf {
					bestConf = conf
					bestNearestMatch = m
					// Suggest adding the misspelled term as a phonetic variant.
					if conf >= 0.5 && conf < 0.85 {
						key := tok + "->" + m
						if !phoneticSet[key] {
							phoneticSet[key] = true
							analytics.PhoneticSuggestions = append(
								analytics.PhoneticSuggestions,
								fmt.Sprintf("add variant %q for %q (confidence %.2f)", tok, m, conf),
							)
						}
					}
				}
			}
			analytics.FailedTerms = append(analytics.FailedTerms, FailedTerm{
				Term:         rawText,
				Count:        cnt,
				NearestMatch: bestNearestMatch,
			})
		}
	}

	return analytics, nil
}

// ---------------------------------------------------------------------------
// Voice health patrol
// ---------------------------------------------------------------------------

// voiceHealthPatrol is registered with the PatrolSystem and runs every 2 min.
// It checks command success rate, confidence trends, and failed terms.
func voiceHealthPatrol(ctx context.Context, db *sql.DB) error {
	if db == nil {
		return fmt.Errorf("voiceHealthPatrol: db is nil")
	}

	// Check success rate of last 100 commands.
	var total int
	var successRate float64
	err := db.QueryRowContext(ctx, `
		SELECT COUNT(*), COALESCE(AVG(CASE WHEN success=1 THEN 1.0 ELSE 0.0 END),0)
		FROM (
			SELECT success FROM voice_commands ORDER BY created_at DESC LIMIT 100
		)`).Scan(&total, &successRate)
	if err != nil {
		return fmt.Errorf("voiceHealthPatrol: stats query failed: %w", err)
	}

	if total == 0 {
		// No voice commands yet — nothing to alert on.
		return nil
	}

	if successRate < 0.80 {
		log.Printf("[voice-health] WARNING: voice command success rate is %.1f%% (last %d commands) — review failed terms via GET /api/voice/analytics",
			successRate*100, total)

		// Publish finding to event bus if available (best-effort).
		publishVoiceHealthAlert(db, successRate, total)
	} else {
		log.Printf("[voice-health] OK: voice command success rate %.1f%% over last %d commands",
			successRate*100, total)
	}

	return nil
}

// publishVoiceHealthAlert publishes a patrol finding to the events table.
func publishVoiceHealthAlert(db *sql.DB, successRate float64, total int) {
	id := ulid.Make().String()
	payload, _ := json.Marshal(map[string]interface{}{
		"patrol":       "voice-health",
		"success_rate": successRate,
		"sample_size":  total,
		"threshold":    0.80,
		"alert":        "success_rate_below_threshold",
	})
	_, err := db.Exec(`
		INSERT INTO events (id, aggregate_id, topic, type, version, payload, timestamp, agent_id, sequence)
		VALUES (?, ?, 'patrol.finding', 'voice.health.alert', 1, ?, CURRENT_TIMESTAMP, 'voice-patrol', 0)
		ON CONFLICT DO NOTHING`,
		id, id, string(payload),
	)
	if err != nil {
		log.Printf("[voice-health] could not publish patrol finding: %v", err)
	}
}
