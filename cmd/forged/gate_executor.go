// cmd/forged/gate_executor.go
package main

import (
    "context"
    "database/sql"
    "encoding/json"
    "fmt"
    "net/http"
    "os/exec"
    "strings"
    "time"
)

// QualityGate represents a quality gate to be executed
type QualityGate struct {
    ID          string        `json:"id"`
    Type        string        `json:"type"` // "command", "http", "checkpoint"
    Command     string        `json:"command,omitempty"`
    URL         string        `json:"url,omitempty"`
    Method      string        `json:"method,omitempty"`
    Expected    string        `json:"expected,omitempty"`
    Timeout     time.Duration `json:"timeout"`
}

// QualityGateResult represents the result of gate execution
type QualityGateResult struct {
    Passed   bool          `json:"passed"`
    Output   string        `json:"output"`
    Error    string        `json:"error,omitempty"`
    Duration time.Duration `json:"duration"`
}

// GateExecutor executes quality gates
type GateExecutor interface {
    ExecuteGate(ctx context.Context, gate QualityGate) (*QualityGateResult, error)
}

// CommandGateExecutor executes shell command gates
type CommandGateExecutor struct{}

func (e *CommandGateExecutor) ExecuteGate(ctx context.Context, gate QualityGate) (*QualityGateResult, error) {
    start := time.Now()
    
    timeout := gate.Timeout
    if timeout == 0 {
        timeout = 30 * time.Second
    }
    
    ctx, cancel := context.WithTimeout(ctx, timeout)
    defer cancel()
    
    cmd := exec.CommandContext(ctx, "sh", "-c", gate.Command)
    output, err := cmd.CombinedOutput()
    
    result := &QualityGateResult{
        Duration: time.Since(start),
        Output:   string(output),
    }
    
    if err != nil {
        result.Passed = false
        result.Error = err.Error()
        return result, nil
    }
    
    if gate.Expected != "" && !strings.Contains(string(output), gate.Expected) {
        result.Passed = false
        result.Error = fmt.Sprintf("expected %q not found in output", gate.Expected)
        return result, nil
    }
    
    result.Passed = true
    return result, nil
}

// HTTPGateExecutor executes HTTP endpoint gates
type HTTPGateExecutor struct {
    Client *http.Client
}

func NewHTTPGateExecutor() *HTTPGateExecutor {
    return &HTTPGateExecutor{
        Client: &http.Client{Timeout: 30 * time.Second},
    }
}

func (e *HTTPGateExecutor) ExecuteGate(ctx context.Context, gate QualityGate) (*QualityGateResult, error) {
    start := time.Now()
    
    method := gate.Method
    if method == "" {
        method = "GET"
    }
    
    req, err := http.NewRequestWithContext(ctx, method, gate.URL, nil)
    if err != nil {
        return &QualityGateResult{Passed: false, Error: err.Error()}, nil
    }
    
    resp, err := e.Client.Do(req)
    if err != nil {
        return &QualityGateResult{Passed: false, Error: err.Error()}, nil
    }
    defer resp.Body.Close()
    
    // Check expected status or body
    passed := resp.StatusCode >= 200 && resp.StatusCode < 300
    if gate.Expected != "" {
        // Parse expected as JSON status check
        var expected map[string]interface{}
        if json.Unmarshal([]byte(gate.Expected), &expected) == nil {
            if status, ok := expected["status"].(float64); ok {
                passed = resp.StatusCode == int(status)
            }
        }
    }
    
    return &QualityGateResult{
        Passed:   passed,
        Duration: time.Since(start),
    }, nil
}

// CheckpointGateExecutor verifies state checkpoints
type CheckpointGateExecutor struct {
    DB *sql.DB
}

func (e *CheckpointGateExecutor) ExecuteGate(ctx context.Context, gate QualityGate) (*QualityGateResult, error) {
    start := time.Now()
    
    // Parse checkpoint query from Expected field
    // Expected format: "count:5" or "exists:true"
    
    result := &QualityGateResult{
        Duration: time.Since(start),
    }
    
    if strings.HasPrefix(gate.Expected, "count:") {
        expectedCount := strings.TrimPrefix(gate.Expected, "count:")
        var actualCount int
        err := e.DB.QueryRowContext(ctx, gate.Command).Scan(&actualCount)
        if err != nil {
            result.Passed = false
            result.Error = err.Error()
            return result, nil
        }
        result.Passed = fmt.Sprintf("%d", actualCount) == expectedCount
        result.Output = fmt.Sprintf("count=%d", actualCount)
    } else if gate.Expected == "exists:true" {
        var exists bool
        err := e.DB.QueryRowContext(ctx, gate.Command).Scan(&exists)
        result.Passed = err == nil && exists
    } else {
        result.Passed = false
        result.Error = "unknown checkpoint type"
    }
    
    return result, nil
}
