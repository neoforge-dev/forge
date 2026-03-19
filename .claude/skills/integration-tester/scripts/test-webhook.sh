#!/usr/bin/env bash
#
# Webhook Integration Test Script
# Usage: test-webhook.sh [options]
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
SERVER_PID=""

log() {
    if [ "$VERBOSE" = "true" ]; then
        echo "$@"
    fi
}

usage() {
    cat <<EOF
Webhook Integration Test

Usage: test-webhook.sh [options]

Options:
  --url URL            Webhook endpoint URL to test
  --port PORT          Port for webhook listener (default: 8080)
  --secret SECRET      Webhook signing secret
  --provider NAME      Provider type (stripe|github|shopify|custom)
  --send-test          Send test webhook
  --payload JSON       Custom payload for test
  --wait SECONDS       Seconds to wait for webhook (default: 30)
  --help               Show this help

Modes:
  1. Listener mode (--port): Start webhook listener and wait for events
  2. Sender mode (--url --send-test): Send test webhook to endpoint

Examples:
  # Start listener
  test-webhook.sh --port 8080 --secret whsec_xxx --provider stripe
  
  # Send test webhook
  test-webhook.sh --url https://api.example.com/webhooks --secret whsec_xxx --send-test

EOF
}

# Parse arguments
parse_args() {
    WEBHOOK_URL=""
    WEBHOOK_PORT=""
    WEBHOOK_SECRET="${WEBHOOK_SECRET:-}"
    PROVIDER="custom"
    SEND_TEST=false
    CUSTOM_PAYLOAD='{"event":"test","timestamp":"'$(date -u +%Y-%m-%dT%H:%M:%SZ)'"}'
    WAIT_TIME=30
    
    while [[ $# -gt 0 ]]; do
        case $1 in
            --url)
                WEBHOOK_URL="$2"
                shift 2
                ;;
            --port)
                WEBHOOK_PORT="$2"
                shift 2
                ;;
            --secret)
                WEBHOOK_SECRET="$2"
                shift 2
                ;;
            --provider)
                PROVIDER="$2"
                shift 2
                ;;
            --send-test)
                SEND_TEST=true
                shift
                ;;
            --payload)
                CUSTOM_PAYLOAD="$2"
                shift 2
                ;;
            --wait)
                WAIT_TIME="$2"
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
}

# Generate Stripe signature
generate_stripe_signature() {
    local payload="$1"
    local secret="$2"
    local timestamp
    timestamp=$(date +%s)
    
    local signed_payload="${timestamp}.${payload}"
    local signature
    signature=$(echo -n "$signed_payload" | openssl dgst -sha256 -hmac "$secret" | cut -d' ' -f2)
    
    echo "t=${timestamp},v1=${signature}"
}

# Generate GitHub signature
generate_github_signature() {
    local payload="$1"
    local secret="$2"
    
    local signature
    signature=$(echo -n "$payload" | openssl dgst -sha256 -hmac "$secret" | cut -d' ' -f2)
    
    echo "sha256=${signature}"
}

# Send test webhook
send_test_webhook() {
    echo "Sending test webhook to $WEBHOOK_URL..."
    
    local headers=()
    local payload="$CUSTOM_PAYLOAD"
    
    # Add provider-specific headers
    case "$PROVIDER" in
        stripe)
            if [ -n "$WEBHOOK_SECRET" ]; then
                local signature
                signature=$(generate_stripe_signature "$payload" "$WEBHOOK_SECRET")
                headers+=(-H "Stripe-Signature: $signature")
            fi
            headers+=(-H "Content-Type: application/json")
            ;;
        github)
            if [ -n "$WEBHOOK_SECRET" ]; then
                local signature
                signature=$(generate_github_signature "$payload" "$WEBHOOK_SECRET")
                headers+=(-H "X-Hub-Signature-256: $signature")
            fi
            headers+=(-H "Content-Type: application/json")
            headers+=(-H "X-GitHub-Event: test")
            ;;
        *)
            headers+=(-H "Content-Type: application/json")
            if [ -n "$WEBHOOK_SECRET" ]; then
                headers+=(-H "X-Webhook-Secret: $WEBHOOK_SECRET")
            fi
            ;;
    esac
    
    local response http_code
    response=$(curl -s -w "\nHTTP_CODE:%{http_code}" \
        "${headers[@]}" \
        -d "$payload" \
        "$WEBHOOK_URL" 2>&1)
    
    http_code=$(echo "$response" | grep "HTTP_CODE:" | cut -d: -f2)
    
    if [ "$http_code" = "200" ] || [ "$http_code" = "201" ] || [ "$http_code" = "202" ]; then
        echo -e "${GREEN}✅ Webhook sent successfully${NC}"
        echo "   Response: $http_code"
        return 0
    else
        echo -e "${RED}❌ Webhook failed${NC}"
        echo "   Response: $http_code"
        echo "   Body: $(echo "$response" | sed '/HTTP_CODE:/d')"
        ((FAILED_TESTS++))
        return 1
    fi
}

# Create webhook listener script
create_listener_script() {
    local script_file="/tmp/webhook_listener_$$.py"
    
    cat > "$script_file" << 'PYTHON_SCRIPT'
#!/usr/bin/env python3
import sys
import json
import hmac
import hashlib
from http.server import HTTPServer, BaseHTTPRequestHandler

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8080
SECRET = sys.argv[2] if len(sys.argv) > 2 else None
PROVIDER = sys.argv[3] if len(sys.argv) > 3 else "custom"

class WebhookHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass
    
    def do_POST(self):
        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length)
        
        # Verify signature if secret provided
        if SECRET:
            if not self.verify_signature(body):
                self.send_response(401)
                self.end_headers()
                print(json.dumps({"status": "error", "message": "Invalid signature"}))
                return
        
        self.send_response(200)
        self.end_headers()
        
        # Print received webhook
        try:
            payload = json.loads(body)
            print(json.dumps({"status": "received", "payload": payload}, indent=2))
        except:
            print(json.dumps({"status": "received", "body": body.decode()}, indent=2))
    
    def verify_signature(self, payload):
        if PROVIDER == "stripe":
            sig_header = self.headers.get('Stripe-Signature', '')
            return self.verify_stripe_signature(payload, sig_header)
        elif PROVIDER == "github":
            sig_header = self.headers.get('X-Hub-Signature-256', '')
            return self.verify_github_signature(payload, sig_header)
        return True
    
    def verify_stripe_signature(self, payload, sig_header):
        import time
        try:
            # Parse signature header
            parts = sig_header.split(',')
            timestamp = None
            signatures = []
            for part in parts:
                key, val = part.split('=')
                if key == 't':
                    timestamp = val
                elif key == 'v1':
                    signatures.append(val)
            
            if not timestamp or not signatures:
                return False
            
            # Check timestamp (allow 5 min drift)
            if abs(time.time() - int(timestamp)) > 300:
                return False
            
            # Verify signature
            signed_payload = f"{timestamp}.{payload.decode()}"
            expected = hmac.new(
                SECRET.encode(),
                signed_payload.encode(),
                hashlib.sha256
            ).hexdigest()
            
            return expected in signatures
        except:
            return False
    
    def verify_github_signature(self, payload, sig_header):
        try:
            if not sig_header.startswith('sha256='):
                return False
            signature = sig_header.split('=')[1]
            expected = hmac.new(
                SECRET.encode(),
                payload,
                hashlib.sha256
            ).hexdigest()
            return hmac.compare_digest(signature, expected)
        except:
            return False

if __name__ == '__main__':
    server = HTTPServer(('0.0.0.0', PORT), WebhookHandler)
    print(json.dumps({"status": "listening", "port": PORT, "provider": PROVIDER}))
    sys.stdout.flush()
    server.serve_forever()
PYTHON_SCRIPT

    echo "$script_file"
}

# Start webhook listener
start_listener() {
    if [ -z "$WEBHOOK_PORT" ]; then
        WEBHOOK_PORT=8080
    fi
    
    echo "Starting webhook listener on port $WEBHOOK_PORT..."
    
    local listener_script
    listener_script=$(create_listener_script)
    
    # Start listener in background
    python3 "$listener_script" "$WEBHOOK_PORT" "$WEBHOOK_SECRET" "$PROVIDER" &
    SERVER_PID=$!
    
    # Wait for listener to start
    sleep 1
    
    # Check if process is running
    if kill -0 $SERVER_PID 2>/dev/null; then
        echo -e "${GREEN}✅ Webhook listener started${NC}"
        echo "   Port: $WEBHOOK_PORT"
        echo "   Provider: $PROVIDER"
        echo "   URL: http://localhost:$WEBHOOK_PORT"
        echo ""
        echo "Waiting for webhooks... (Ctrl+C to stop)"
        
        # Wait for data or timeout
        local count=0
        while [ $count -lt "$WAIT_TIME" ]; do
            if ! kill -0 $SERVER_PID 2>/dev/null; then
                break
            fi
            sleep 1
            ((count++))
        done
        
        # Cleanup
        stop_listener
    else
        echo -e "${RED}❌ Failed to start listener${NC}"
        rm -f "$listener_script"
        exit 1
    fi
    
    rm -f "$listener_script"
}

# Stop webhook listener
stop_listener() {
    if [ -n "$SERVER_PID" ] && kill -0 "$SERVER_PID" 2>/dev/null; then
        kill "$SERVER_PID" 2>/dev/null || true
        wait "$SERVER_PID" 2>/dev/null || true
        echo ""
        echo "Listener stopped"
    fi
}

# Cleanup on exit
cleanup() {
    stop_listener
}

trap cleanup EXIT

# Main
main() {
    parse_args "$@"
    
    echo "Webhook Integration Test"
    echo "========================"
    
    # Validate arguments
    if [ -z "$WEBHOOK_URL" ] && [ -z "$WEBHOOK_PORT" ]; then
        echo "Error: Either --url or --port must be specified"
        usage
        exit 1
    fi
    
    if [ -n "$WEBHOOK_URL" ] && [ "$SEND_TEST" = true ]; then
        send_test_webhook
    elif [ -n "$WEBHOOK_PORT" ]; then
        start_listener
    else
        echo "Error: --send-test is required when using --url"
        usage
        exit 1
    fi
    
    if [ $FAILED_TESTS -gt 0 ]; then
        exit 1
    fi
}

main "$@"
