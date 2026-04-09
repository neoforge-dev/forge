#!/bin/bash
# ios-green-loop.sh — Loop until all iOS tests pass and previews build
# Usage: bin/ios-green-loop.sh [project-path] [scheme]
# Example: bin/ios-green-loop.sh ios/calm-connect-ios Scheduler
#
# Runs in a loop:
#   1. Shutdown stale simulators
#   2. Boot fresh simulator
#   3. Build for testing
#   4. Run tests (serial, no clones)
#   5. If green → report + exit
#   6. If failures → log them, wait, retry

set -euo pipefail

PROJECT_DIR="${1:-ios/calm-connect-ios}"
SCHEME="${2:-Scheduler}"
SIM_NAME="iPhone 17 Pro"
MAX_RETRIES=10
RETRY_DELAY=30
LOG_DIR="/tmp/ios-green-loop"
FORGE_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

mkdir -p "$LOG_DIR"

cd "$FORGE_ROOT/$PROJECT_DIR"

echo "=== iOS Green Loop ==="
echo "Project: $PROJECT_DIR"
echo "Scheme: $SCHEME"
echo "Simulator: $SIM_NAME"
echo "Max retries: $MAX_RETRIES"
echo ""

for attempt in $(seq 1 $MAX_RETRIES); do
    echo "--- Attempt $attempt/$MAX_RETRIES ($(date '+%H:%M:%S')) ---"
    LOG_FILE="$LOG_DIR/attempt-$attempt.log"

    # 1. Clean simulator state
    echo "[1/4] Resetting simulator..."
    xcrun simctl shutdown all 2>/dev/null || true
    sleep 2
    xcrun simctl boot "$SIM_NAME" 2>/dev/null || true
    sleep 8

    # 2. Build for testing
    echo "[2/4] Building..."
    if ! xcodebuild build-for-testing \
        -project *.xcodeproj -scheme "$SCHEME" \
        -destination "platform=iOS Simulator,name=$SIM_NAME" \
        CODE_SIGNING_ALLOWED=NO \
        2>&1 | xcbeautify --quieter; then
        echo "BUILD FAILED — fixing needed"
        echo "BUILD_FAILED" > "$LOG_FILE"
        sleep "$RETRY_DELAY"
        continue
    fi

    # 3. Run tests (serial to avoid clone crashes)
    echo "[3/4] Running tests..."
    xcodebuild test-without-building \
        -project *.xcodeproj -scheme "$SCHEME" \
        -destination "platform=iOS Simulator,name=$SIM_NAME" \
        -only-testing:SchedulerTests \
        -disable-concurrent-testing \
        CODE_SIGNING_ALLOWED=NO \
        2>&1 > "$LOG_FILE"
    EXIT_CODE=$?

    # 4. Analyze results
    PASSED=$(grep -c "passed" "$LOG_FILE" 2>/dev/null || echo 0)
    FAILED=$(grep -c "' failed " "$LOG_FILE" 2>/dev/null || echo 0)

    echo "[4/4] Results: $PASSED passed, $FAILED failed (exit: $EXIT_CODE)"

    if [ "$EXIT_CODE" -eq 0 ] && [ "$FAILED" -eq 0 ]; then
        echo ""
        echo "=== ALL TESTS GREEN ==="
        echo "Passed: $PASSED"
        echo "Failed: $FAILED"
        echo "Attempt: $attempt/$MAX_RETRIES"
        echo "Log: $LOG_FILE"

        # Write result for forge monitoring
        cat > "$FORGE_ROOT/.forge/heartbeat/results/ios-green-loop-result.md" <<EOF
# iOS Green Loop Result

**Status:** GREEN
**Date:** $(date '+%Y-%m-%d %H:%M:%S')
**Project:** $PROJECT_DIR
**Scheme:** $SCHEME
**Tests Passed:** $PASSED
**Tests Failed:** $FAILED
**Attempts:** $attempt
EOF
        exit 0
    fi

    echo "Failures detected — listing:"
    grep "' failed " "$LOG_FILE" 2>/dev/null | head -20
    echo ""

    if [ "$attempt" -lt "$MAX_RETRIES" ]; then
        echo "Retrying in ${RETRY_DELAY}s..."
        sleep "$RETRY_DELAY"
    fi
done

echo "=== MAX RETRIES REACHED ==="
echo "Tests still failing after $MAX_RETRIES attempts."
echo "Review: $LOG_DIR/"
exit 1
