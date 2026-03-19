---
name: performance-profiler
description: Profile Python and JavaScript code performance, identify bottlenecks, and suggest optimizations. Supports cProfile, py-spy, memory_profiler, database query analysis, API latency profiling, and flamegraph generation.
trigger: user-invoked
---

# Performance Profiler

Comprehensive profiling tool for FORGE projects to identify performance bottlenecks and optimize code execution across Python, JavaScript, databases, and APIs.

## When to Use

- Code is running slower than expected and bottlenecks are unknown
- Need to optimize database queries or API response times
- Memory usage is growing unexpectedly
- Preparing for production scaling
- Refactoring legacy code for performance

## Quick Start

```bash
# Profile a Python script
/profile python app/main.py --flamegraph

# Profile API endpoint latency
/profile api --endpoint /api/v1/users --requests 100

# Analyze slow database queries
/profile db --slow-queries --threshold 100

# Memory profiling for a Python module
/profile memory app/services.py --line-by-line
```

---

## Supported Profile Types

| Type | Tool | Best For | Output |
|------|------|----------|--------|
| **python** | cProfile, py-spy | CPU bottlenecks, function call frequency | Stats, flamegraph |
| **memory** | memory_profiler, tracemalloc | Memory leaks, high RAM usage | Line-by-line report |
| **api** | curl, wrk, hey | Endpoint latency, throughput | Latency percentiles |
| **db** | PostgreSQL pg_stat_statements | Slow queries, N+1 patterns | Query analysis |
| **js** | 0x, Chrome DevTools | Node.js/browser performance | Flamegraph, trace |

---

## Commands

### 1. Python Profiling

Profile Python scripts and applications to identify CPU bottlenecks.

```bash
# Basic cProfile (built-in, no install needed)
/profile python <script.py> [args...]

# High-resolution sampling with py-spy (requires install)
/profile python <script.py> --pyspy --flamegraph

# Profile specific function
/profile python app/main.py --function process_data --duration 30

# Export to callgrind for KCachegrind
/profile python app/main.py --output callgrind.out
```

**Requirements:**
- cProfile: Built into Python (no install)
- py-spy: `pip install py-spy` or `cargo install py-spy`
- flamegraph: `pip install flameprof` or use built-in converter

**Example Output:**
```
Function                              Calls   Time (ms)   %Total
----------------------------------  -------  ----------  -------
services.process_batch                  150      2450.3     45.2
database.get_user_by_id               1,200      1890.5     34.8
utils.transform_data                    300       450.2      8.3
```

---

### 2. Memory Profiling

Identify memory leaks and high memory consumption in Python code.

```bash
# Line-by-line memory usage
/profile memory <module.py> --line-by-line

# Monitor specific function over time
/profile memory app/services.py --function heavy_processing --interval 0.1

# Peak memory snapshot with tracemalloc
/profile memory --snapshot --top 20

# Compare memory before/after operation
/profile memory --diff --before baseline.snapshot --after current.snapshot
```

**Requirements:**
- memory_profiler: `pip install memory_profiler`
- tracemalloc: Built into Python 3.4+

**Example Output:**
```
Line #    Mem usage    Increment   Line Contents
===============================================
    45   45.2 MiB   45.2 MiB   def process_large_dataset():
    46   78.5 MiB   33.3 MiB       data = load_csv('large_file.csv')
    47   81.2 MiB    2.7 MiB       processed = [transform(row) for row in data]
    48   52.1 MiB  -29.1 MiB       return processed[:100]  # Memory spike!
```

---

### 3. API Latency Profiling

Measure API endpoint performance under load.

```bash
# Basic latency check
/profile api --endpoint /api/v1/users --method GET

# Load test with concurrent requests
/profile api --endpoint /api/v1/users --requests 1000 --concurrency 50

# Authenticated endpoint
/profile api --endpoint /api/v1/protected --header "Authorization: Bearer $TOKEN"

# Full latency distribution
/profile api --endpoint /api/v1/users --latency-percentiles

# Compare before/after optimization
/profile api --endpoint /api/v1/users --baseline baseline.json --compare
```

**Tools Used:**
- Built-in: curl + time command
- Advanced: wrk (recommended), hey, or Apache Bench

**Example Output:**
```
Endpoint: GET /api/v1/users
Requests: 1000 (50 concurrent)

Latency Distribution:
  50%:    45ms
  75%:    62ms
  90%:   110ms
  95%:   185ms
  99%:   420ms

Throughput: 892 req/sec
Errors: 0 (0%)
```

---

### 4. Database Query Profiling

Analyze PostgreSQL slow queries and identify N+1 patterns.

```bash
# Show slow queries (requires pg_stat_statements)
/profile db --slow-queries --threshold 100

# Analyze specific table queries
/profile db --table users --analyze

# Detect N+1 query patterns
/profile db --detect-n-plus-1 --time-window 5m

# Query execution plan analysis
/profile db --explain "SELECT * FROM users WHERE email = 'test@example.com'"

# Index usage report
/profile db --index-usage --missing-only
```

**Requirements:**
- PostgreSQL with `pg_stat_statements` extension
- Database connection string in `.env` or `--db-url`

**Example Output:**
```
Slow Queries (>100ms):
┌─────────────────────────────────────────────────────────────┬─────────┬──────────┬──────────┐
│ Query                                                       │ Calls   │ Avg (ms) │ Total    │
├─────────────────────────────────────────────────────────────┼─────────┼──────────┼──────────┤
│ SELECT * FROM orders WHERE user_id = $1 ORDER BY created_at │ 15,420  │    245.3 │ 3,782.1s │
│ SELECT * FROM products WHERE category_id IN (...)           │    892  │    189.2 │ 1,687.3s │
└─────────────────────────────────────────────────────────────┴─────────┴──────────┴──────────┘

Detected N+1 Pattern:
  Query: SELECT * FROM users WHERE id = $1
  Occurrences: 1,500 in 2.3s
  Likely source: app/services.py:45
```

---

### 5. JavaScript/Node.js Profiling

Profile Node.js applications and browser JavaScript.

```bash
# Profile Node.js script with 0x
/profile js server.js --flamegraph

# Chrome DevTools trace (browser)
/profile js --browser --url http://localhost:3000 --duration 30

# Specific function profiling
/profile js app.js --function handleRequest --samples 10000

# Memory heap snapshot
/profile js --heap-snapshot --output heap.heapsnapshot
```

**Requirements:**
- 0x: `npm install -g 0x`
- Chrome/Chromium (for browser profiling)

---

## Profiling Workflow

### Standard Performance Investigation

```bash
# Step 1: Identify the bottleneck type
/profile api --endpoint /slow/endpoint --requests 100
# -> High latency detected (800ms avg)

# Step 2: Profile the backend code
/profile python app/api/slow_endpoint.py --pyspy --flamegraph
# -> 60% time in database queries

# Step 3: Analyze database queries
/profile db --slow-queries --threshold 50
# -> Missing index on user_id + created_at

# Step 4: Verify fix
/profile api --endpoint /slow/endpoint --requests 100
# -> Latency reduced to 120ms avg
```

### Memory Leak Investigation

```bash
# Step 1: Take baseline snapshot
/profile memory --snapshot --output baseline.snapshot

# Step 2: Run workload
# ... exercise the suspected leaky code ...

# Step 3: Take comparison snapshot
/profile memory --snapshot --output after.snapshot

# Step 4: Compare
/profile memory --diff --before baseline.snapshot --after after.snapshot
# -> 45MB growth in cache dictionary

# Step 5: Line-by-line analysis
/profile memory app/cache.py --line-by-line --function get_cached
```

---

## Scripts Reference

| Script | Purpose |
|--------|---------|
| `scripts/profile_python.sh` | cProfile/py-spy wrapper with flamegraph support |
| `scripts/profile_memory.sh` | memory_profiler wrapper with line-by-line analysis |
| `scripts/profile_api.sh` | API latency testing with wrk/curl fallback |
| `scripts/profile_db.sh` | PostgreSQL slow query and index analysis |
| `scripts/profile_js.sh` | Node.js profiling with 0x |
| `scripts/generate_report.sh` | Combine all profiling outputs into HTML report |

---

## Output Formats

All profilers support multiple output formats:

```bash
# Text (default)
/profile python app.py --format text

# JSON for programmatic analysis
/profile python app.py --format json --output profile.json

# HTML report with visualizations
/profile python app.py --format html --output report.html

# Flamegraph SVG
/profile python app.py --flamegraph --output flamegraph.svg
```

---

## Prompts Reference

Analysis prompts in `prompts/` directory:

| Prompt | Use Case |
|--------|----------|
| `python_analysis.txt` | Analyze cProfile/py-spy output |
| `memory_analysis.txt` | Analyze memory_profiler output |
| `api_latency_analysis.txt` | Interpret latency percentiles |
| `db_query_analysis.txt` | Optimize slow PostgreSQL queries |
| `optimization_recommendations.txt` | Generate prioritized fix list |

Use prompts with AI analysis:
```bash
/profile python app.py --format json | kimi analyze --prompt prompts/python_analysis.txt
```

---

## Integration with FORGE Workflow

### During Development
```bash
# Before committing performance-critical code
make profile  # Runs quick API latency check

# CI/CD integration
/profile api --endpoint /health --fail-if-p99-above 200ms
```

### Production Troubleshooting
```bash
# Quick health check on production-like data
/profile db --slow-queries --threshold 500 --last-hour

# Memory leak detection
/profile memory --snapshot --interval 60 --duration 3600
```

---

## Requirements Summary

### Required (always available)
- Python 3.8+
- cProfile (built-in)
- tracemalloc (built-in)
- curl (system)

### Optional (install as needed)
- `pip install py-spy memory_profiler flameprof`
- `npm install -g 0x`
- `brew install wrk` or `apt-get install wrk`
- PostgreSQL with `pg_stat_statements`

---

## Best Practices

1. **Profile in production-like conditions** - Use realistic data volumes
2. **Measure before optimizing** - Don't guess where the bottleneck is
3. **Focus on hot paths** - 80% of time is usually spent in 20% of code
4. **Verify improvements** - Always re-profile after changes
5. **Monitor continuously** - Set up periodic profiling in CI/CD

---

## Related Skills

- `/test-coverage-analyzer` - Find untested performance-critical code
- `/dependency-auditor` - Check for slow/outdated dependencies
- `/auto-test-runner` - Run performance regression tests
