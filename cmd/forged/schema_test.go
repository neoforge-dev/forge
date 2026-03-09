// Schema validation test: ensures database schema YAML is loadable and contains required tables.
package main

import (
	"os"
	"path/filepath"
	"testing"

	"gopkg.in/yaml.v3"
)

type schemaDoc struct {
	Database string            `yaml:"database"`
	Version  string            `yaml:"version"`
	Tables   map[string]struct {
		Description string `yaml:"description"`
		Columns    []struct {
			Name string `yaml:"name"`
			Type string `yaml:"type"`
		} `yaml:"columns"`
	} `yaml:"tables"`
}

// Required tables that must exist in v3_schema.yaml (single source of truth).
var requiredTables = []string{"tasks", "agents", "context_envelopes"}

// TestSchemaValidation validates that the schema YAML loads and defines required tables.
func TestSchemaValidation(t *testing.T) {
	// Locate .forge/schema/v3_schema.yaml relative to repo root
	repoRoot := os.Getenv("FORGE_ROOT")
	if repoRoot == "" {
		cwd, _ := os.Getwd()
		// From cmd/forged we go up twice to repo root
		for i := 0; i < 5; i++ {
			p := cwd
			for j := 0; j < i; j++ {
				p = filepath.Dir(p)
			}
			if _, err := os.Stat(filepath.Join(p, ".forge", "schema", "v3_schema.yaml")); err == nil {
				repoRoot = p
				break
			}
		}
	}
	if repoRoot == "" {
		t.Skip("FORGE_ROOT not set and v3_schema.yaml not found (run from repo root or set FORGE_ROOT)")
	}

	schemaPath := filepath.Join(repoRoot, ".forge", "schema", "v3_schema.yaml")
	data, err := os.ReadFile(schemaPath)
	if err != nil {
		t.Fatalf("failed to read schema: %v", err)
	}

	var doc schemaDoc
	if err := yaml.Unmarshal(data, &doc); err != nil {
		t.Fatalf("failed to parse schema YAML: %v", err)
	}

	if doc.Database == "" {
		t.Error("schema must define database")
	}
	if doc.Version == "" {
		t.Error("schema must define version")
	}
	if len(doc.Tables) == 0 {
		t.Fatal("schema must define at least one table")
	}

	for _, name := range requiredTables {
		if _, ok := doc.Tables[name]; !ok {
			t.Errorf("schema missing required table: %s", name)
		}
	}

	for tableName, table := range doc.Tables {
		if table.Description == "" {
			t.Errorf("table %s missing description", tableName)
		}
		if len(table.Columns) == 0 {
			t.Errorf("table %s has no columns", tableName)
		}
	}
}
