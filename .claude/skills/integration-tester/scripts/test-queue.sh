#!/usr/bin/env bash
#
# Message Queue Integration Test Script
# Usage: test-queue.sh [options]
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VERBOSE="${VERBOSE:-false}"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

FAILED_TESTS=0

log() {
    if [ "$VERBOSE" = "true" ]; then
        echo "$@"
    fi
}

usage() {
    cat <<EOF
Message Queue Integration Test

Usage: test-queue.sh [options]

Options:
  --broker URL         Broker URL (redis://host:port) (required)
  --backend URL        Result backend URL
  --celery             Test Celery integration
  --test-task NAME     Test specific Celery task
  --send-task NAME     Send task and wait for result
  --args JSON          Task arguments as JSON
  --expect-result VAL  Expected result value
  --timeout SEC        Task timeout (default: 30)
  --help               Show this help

Examples:
  test-queue.sh --broker redis://localhost:6379
  test-queue.sh --broker redis://localhost:6379 --celery
  test-queue.sh --broker redis://localhost:6379 --send-task tasks.add --args '[1, 2]' --expect-result 3

EOF
}

# Parse arguments
parse_args() {
    BROKER_URL=""
    BACKEND_URL=""
    TEST_CELERY=false
    TEST_TASK=""
    SEND_TASK=""
    TASK_ARGS="[]"
    EXPECT_RESULT=""
    TASK_TIMEOUT=30
    
    while [[ $# -gt 0 ]]; do
        case $1 in
            --broker)
                BROKER_URL="$2"
                shift 2
                ;;
            --backend)
                BACKEND_URL="$2"
                shift 2
                ;;
            --celery)
                TEST_CELERY=true
                shift
                ;;
            --test-task)
                TEST_TASK="$2"
                shift 2
                ;;
            --send-task)
                SEND_TASK="$2"
                shift 2
                ;;
            --args)
                TASK_ARGS="$2"
                shift 2
                ;;
            --expect-result)
                EXPECT_RESULT="$2"
                shift 2
                ;;
            --timeout)
                TASK_TIMEOUT="$2"
                shift 2
                ;;
            --verbose)
                VERBOSE="true"
                shift
                ;;
            --help|-h)
                usage
                exit 0
                ;;
            *)
                echo "Unknown option: $1"
                usage
                exit 1
                ;;
        esac
    done
    
    if [ -z "$BROKER_URL" ]; then
        echo "Error: --broker is required"
        usage
        exit 1
    fi
}

# Get broker type from URL
get_broker_type() {
    local url="$1"
    echo "$url" | cut -d: -f1
}

# Test Redis connection
test_redis() {
    local url="$1"
    local name="${2:-Redis}"
    
    echo "Testing $name connection..."
    
    # Extract host and port
    local host port
    if [[ "$url" =~ redis://([^:]+):([0-9]+) ]]; then
        host="${BASH_REMATCH[1]}"
        port="${BASH_REMATCH[2]}"
    else
        host="localhost"
        port=6379
    fi
    
    # Test with redis-cli
    if command -v redis-cli &> /dev/null; then
        if redis-cli -h "$host" -p "$port" ping | grep -q "PONG"; then
            echo -e "${GREEN}✅ $name connection successful${NC}"
            
            # Get server info
            local redis_version
            redis_version=$(redis-cli -h "$host" -p "$port" INFO SERVER 2>/dev/null | grep redis_version | cut -d: -f2 | tr -d '\r')
            if [ -n "$redis_version" ]; then
                echo "   Version: $redis_version"
            fi
            
            return 0
        else
            echo -e "${RED}❌ $name connection failed${NC}"
            ((FAILED_TESTS++))
            return 1
        fi
    fi
    
    # Test with Python
    if command -v python3 &> /dev/null; then
        python3 << EOF
import redis
import sys

try:
    r = redis.from_url("$url")
    if r.ping():
        print(f"✅ $name connection successful (Python)")
        sys.exit(0)
except Exception as e:
    print(f"❌ $name connection failed: {e}")
    sys.exit(1)
EOF
        return $?
    fi
    
    echo -e "${YELLOW}⚠️  Neither redis-cli nor Python redis available${NC}"
    return 1
}

# Test pub/sub
test_pubsub() {
    echo ""
    echo "Testing pub/sub..."
    
    if command -v python3 &> /dev/null; then
        python3 << EOF
import redis
import time
import sys

try:
    r = redis.from_url("$BROKER_URL")
    
    # Subscribe
    pubsub = r.pubsub()
    pubsub.subscribe("test_channel")
    
    # Publish
    r.publish("test_channel", "test_message")
    
    # Wait for message
    for i in range(10):
        message = pubsub.get_message()
        if message and message['type'] == 'message':
            if message['data'] == b'test_message':
                print("✅ Pub/sub working")
                pubsub.close()
                sys.exit(0)
        time.sleep(0.1)
    
    pubsub.close()
    print("❌ Pub/sub test failed")
    sys.exit(1)
except Exception as e:
    print(f"❌ Pub/sub test failed: {e}")
    sys.exit(1)
EOF
        local result=$?
        if [ $result -ne 0 ]; then
            ((FAILED_TESTS++))
        fi
        return $result
    else
        echo -e "${YELLOW}⚠️  Python not available for pub/sub test${NC}"
    fi
}

# Test Celery
test_celery() {
    echo ""
    echo "Testing Celery integration..."
    
    if ! command -v python3 &> /dev/null; then
        echo -e "${YELLOW}⚠️  Python not available${NC}"
        return 1
    fi
    
    # Create temporary Celery app
    local app_file="/tmp/celery_test_app_$$.py"
    
    cat > "$app_file" << EOF
from celery import Celery
import sys

app = Celery('test', broker='$BROKER_URL')
if '$BACKEND_URL':
    app.conf.result_backend = '$BACKEND_URL'

@app.task
def test_echo(message):
    return message

@app.task
def test_add(a, b):
    return a + b

if __name__ == '__main__':
    # Test connection
    try:
        with app.connection() as conn:
            conn.connect()
            print("✅ Celery broker connection successful")
    except Exception as e:
        print(f"❌ Celery broker connection failed: {e}")
        sys.exit(1)
    
    # Test task registration
    print("✅ Celery app initialized")
    print(f"   Registered tasks: {len(app.tasks)}")
EOF

    python3 "$app_file"
    local result=$?
    rm -f "$app_file"
    
    if [ $result -ne 0 ]; then
        ((FAILED_TESTS++))
    fi
    
    return $result
}

# Send and verify task
send_and_verify_task() {
    echo ""
    echo "Sending task: $SEND_TASK"
    
    if ! command -v python3 &> /dev/null; then
        echo -e "${YELLOW}⚠️  Python not available${NC}"
        return 1
    fi
    
    local app_file="/tmp/celery_task_$$.py"
    
    cat > "$app_file" << EOF
from celery import Celery
import sys
import json

app = Celery('test', broker='$BROKER_URL')
if '$BACKEND_URL':
    app.conf.result_backend = '$BACKEND_URL'

try:
    # Import and register task
    import importlib
    task_module, task_name = '$SEND_TASK'.rsplit('.', 1)
    module = importlib.import_module(task_module)
    task = getattr(module, task_name)
    
    # Send task
    args = json.loads('$TASK_ARGS')
    result = task.delay(*args)
    
    print(f"Task sent: {result.id}")
    
    # Wait for result
    try:
        value = result.get(timeout=$TASK_TIMEOUT)
        print(f"Task completed: {value}")
        
        if '$EXPECT_RESULT':
            if str(value) == '$EXPECT_RESULT':
                print("✅ Result matches expected value")
                sys.exit(0)
            else:
                print(f"❌ Result mismatch: expected '$EXPECT_RESULT', got '{value}'")
                sys.exit(1)
        else:
            print("✅ Task completed successfully")
            sys.exit(0)
    except Exception as e:
        print(f"❌ Task failed: {e}")
        sys.exit(1)
        
except ImportError as e:
    print(f"❌ Could not import task: {e}")
    sys.exit(1)
except Exception as e:
    print(f"❌ Error: {e}")
    sys.exit(1)
EOF

    python3 "$app_file"
    local result=$?
    rm -f "$app_file"
    
    if [ $result -ne 0 ]; then
        ((FAILED_TESTS++))
    fi
    
    return $result
}

# Main
main() {
    parse_args "$@"
    
    echo "Message Queue Integration Test"
    echo "==============================="
    echo "Broker: $BROKER_URL"
    if [ -n "$BACKEND_URL" ]; then
        echo "Backend: $BACKEND_URL"
    fi
    echo ""
    
    local broker_type
    broker_type=$(get_broker_type "$BROKER_URL")
    
    case "$broker_type" in
        redis)
            test_redis "$BROKER_URL" "Broker"
            if [ -n "$BACKEND_URL" ]; then
                test_redis "$BACKEND_URL" "Backend"
            fi
            test_pubsub
            ;;
        amqp|amqps)
            echo "Testing AMQP broker..."
            echo -e "${YELLOW}⚠️  AMQP tests require pika (Python)${NC}"
            ;;
        *)
            echo "Unknown broker type: $broker_type"
            ((FAILED_TESTS++))
            ;;
    esac
    
    if [ "$TEST_CELERY" = true ]; then
        test_celery
    fi
    
    if [ -n "$SEND_TASK" ]; then
        send_and_verify_task
    fi
    
    echo ""
    if [ $FAILED_TESTS -eq 0 ]; then
        echo -e "${GREEN}All queue tests passed!${NC}"
    else
        echo -e "${RED}$FAILED_TESTS test(s) failed${NC}"
        exit 1
    fi
}

main "$@"
