# Agent-Friendly CLI Standard

**Version:** 1.0  
**Created:** 2026-02-05  
**Purpose:** Standardize CLI output for human + AI agent workflows

---

## Core Principles

1. **All commands MUST support `--json` flag** for structured output
2. **JSON output MUST follow a consistent schema**
3. **Error messages MUST include error codes** (not just text)
4. **Long operations MUST support progress indicators**
5. **All commands MUST be non-interactive by default** (use `--interactive` flag if needed)

---

## Standard JSON Response Schema

```json
{
  "success": true,
  "data": { /* command-specific payload */ },
  "error": null,
  "error_code": null,
  "timestamp": "2026-02-05T14:30:00Z",
  "duration_ms": 1234,
  "metadata": {
    "command": "forge status",
    "version": "4.0.0"
  }
}
```

### Error Response

```json
{
  "success": false,
  "data": null,
  "error": "Database connection failed: timeout after 30s",
  "error_code": "DB_CONNECTION_TIMEOUT",
  "timestamp": "2026-02-05T14:30:00Z",
  "duration_ms": 30000,
  "metadata": {
    "command": "forge status",
    "version": "4.0.0",
    "retryable": true
  }
}
```

---

## Standard Error Codes

| Code | Category | Meaning |
|------|----------|---------|
| `SUCCESS` | Success | Operation completed |
| `INVALID_INPUT` | Input | Bad arguments or options |
| `NOT_FOUND` | Resource | File/entity doesn't exist |
| `PERMISSION_DENIED` | Auth | Insufficient permissions |
| `TIMEOUT` | Network | Operation timed out |
| `RATE_LIMITED` | API | Rate limit exceeded |
| `SERVICE_UNAVAILABLE` | External | Dependency not available |
| `INTERNAL_ERROR` | System | Unexpected failure |

---

## CLI Framework Patterns

### Typer (Recommended for Python)

```python
import typer
import json
from datetime import datetime

app = typer.Typer()

@app.command()
def query(
    prompt: str,
    json_output: bool = typer.Option(False, "--json", help="Output as JSON")
):
    """Query the knowledge base."""
    start = datetime.utcnow()
    
    try:
        result = do_query(prompt)
        
        if json_output:
            print(json.dumps({
                "success": True,
                "data": result,
                "error": None,
                "error_code": None,
                "timestamp": datetime.utcnow().isoformat(),
                "duration_ms": (datetime.utcnow() - start).total_seconds() * 1000
            }))
        else:
            # Human-friendly output
            print(f"Result: {result}")
            
    except Exception as e:
        if json_output:
            print(json.dumps({
                "success": False,
                "data": None,
                "error": str(e),
                "error_code": "INTERNAL_ERROR",
                "timestamp": datetime.utcnow().isoformat()
            }))
            raise typer.Exit(1)
        else:
            raise
```

### Go/Cobra (For forge CLI)

```go
var jsonOutput bool

func init() {
    rootCmd.PersistentFlags().BoolVar(&jsonOutput, "json", false, "Output as JSON")
}

func outputResult(data interface{}, err error) {
    if jsonOutput {
        response := map[string]interface{}{
            "success":   err == nil,
            "data":      data,
            "error":     nil,
            "timestamp": time.Now().UTC().Format(time.RFC3339),
        }
        if err != nil {
            response["error"] = err.Error()
            response["error_code"] = getErrorCode(err)
        }
        json.NewEncoder(os.Stdout).Encode(response)
    } else {
        // Human-friendly output
        fmt.Printf("Result: %v\n", data)
    }
}
```

---

## Progress Indicators

For long-running operations, use stderr for progress (keeps stdout clean for JSON):

```python
import sys

def long_operation(json_output: bool):
    total = 100
    for i in range(total):
        if not json_output:
            # Progress on stderr, final result on stdout
            print(f"\rProcessing: {i+1}/{total}", file=sys.stderr, end="")
        process_item(i)
    
    if not json_output:
        print("", file=sys.stderr)  # Newline after progress
    
    return {"processed": total}
```

---

## FORGE CLI Inventory

| CLI | Project | Status | Framework |
|-----|---------|--------|-----------|
| `forge` | cmd/forge | ✅ Complete | Cobra (Go) |
| `forged` | cmd/forged | ✅ Complete | Cobra (Go) |
| `graphrag` | graphrag-starter | ✅ Complete | Typer |
| `ios-agent` | ios-agent-cli | ✅ Complete | Cobra |
| `code-atlas` | code-atlas | Needs CLI | Typer |
| `synapse` | synapse-graph-rag | Needs CLI | Typer |

---

## Agent Consumption Pattern

Agents should consume CLI output like this:

```python
import subprocess
import json

def run_cli_command(cmd: list[str]) -> dict:
    """Run CLI command and parse JSON output."""
    result = subprocess.run(
        cmd + ["--json"],
        capture_output=True,
        text=True
    )
    
    response = json.loads(result.stdout)
    
    if not response["success"]:
        raise Exception(f"{response['error_code']}: {response['error']}")
    
    return response["data"]

# Example usage
data = run_cli_command(["graphrag", "query", "What is the architecture?"])
```

---

## Validation

All CLIs should pass this test:

```bash
# Test JSON output
$CLI_COMMAND --json | jq '.success, .data, .timestamp'

# Test error handling
$CLI_COMMAND --invalid-option --json 2>&1 | jq '.success, .error_code'
```

---

*Standard maintained by FORGE orchestration team*
