#!/bin/bash
# Vision-Guided iOS Agent Test
# Uses Claude vision API to analyze screenshots and drive testing decisions

set -e

BUNDLE_ID="${1:-com.example.app}"
DEVICE_NAME="iPhone 15"
OUTPUT_DIR="./test-results-vision"
MAX_STEPS=10

GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${BLUE}Vision-Guided iOS Agent Test${NC}"
echo "================================"
echo "Bundle ID: $BUNDLE_ID"

mkdir -p "$OUTPUT_DIR"

# Get device
DEVICE_ID=$(ios-agent devices | jq -r '.result.devices[] | select(.state=="Booted") | .id' | head -1)

if [ -z "$DEVICE_ID" ]; then
    echo -e "${BLUE}Booting simulator...${NC}"
    BOOT_RESULT=$(ios-agent simulator boot --name "$DEVICE_NAME")
    DEVICE_ID=$(echo "$BOOT_RESULT" | jq -r '.result.device_id')
    sleep 5
fi

echo -e "${GREEN}Device: $DEVICE_ID${NC}"

# Launch app
echo -e "\n${BLUE}Launching app...${NC}"
ios-agent app launch --device "$DEVICE_ID" --bundle "$BUNDLE_ID" --wait-for-ready
sleep 2

# Agent loop: observe -> analyze -> act
for step in $(seq 1 $MAX_STEPS); do
    echo -e "\n${YELLOW}[Step $step/$MAX_STEPS] Capturing state...${NC}"

    # Take screenshot
    SCREENSHOT="$OUTPUT_DIR/step_${step}.png"
    ios-agent screenshot --device "$DEVICE_ID" --output "$SCREENSHOT"
    echo "Screenshot: $SCREENSHOT"

    # In real implementation, analyze screenshot with Claude vision API
    # This would use the Claude API to understand UI state and decide next action
    #
    # Example pseudo-code:
    # ANALYSIS=$(claude analyze-screenshot "$SCREENSHOT" --prompt \
    #   "Identify UI elements: buttons, text fields, current screen. \
    #    What action should be taken next to complete the test flow?")
    #
    # ACTION=$(echo "$ANALYSIS" | jq -r '.next_action')
    # TARGET_X=$(echo "$ANALYSIS" | jq -r '.coordinates.x')
    # TARGET_Y=$(echo "$ANALYSIS" | jq -r '.coordinates.y')

    # For demo purposes, we'll use a simple state machine
    case $step in
        1)
            echo "  Vision analysis: Login screen detected"
            echo "  Action: Tap login button"
            ios-agent io tap --device "$DEVICE_ID" --x 185 --y 400
            ;;
        2)
            echo "  Vision analysis: Email field focused"
            echo "  Action: Enter email"
            ios-agent io text --device "$DEVICE_ID" --text "test@example.com"
            ;;
        3)
            echo "  Vision analysis: Need to move to password field"
            echo "  Action: Tap password field"
            ios-agent io tap --device "$DEVICE_ID" --x 185 --y 500
            ;;
        4)
            echo "  Vision analysis: Password field focused"
            echo "  Action: Enter password"
            ios-agent io text --device "$DEVICE_ID" --text "password123"
            ;;
        5)
            echo "  Vision analysis: Submit button visible"
            echo "  Action: Tap submit"
            ios-agent io tap --device "$DEVICE_ID" --x 185 --y 600
            ;;
        6)
            echo "  Vision analysis: Home screen loaded"
            echo "  Result: Test complete!"
            break
            ;;
        *)
            echo "  Vision analysis: Continuing..."
            ;;
    esac

    sleep 1
done

# Final screenshot
echo -e "\n${BLUE}Capturing final state...${NC}"
ios-agent screenshot --device "$DEVICE_ID" --output "$OUTPUT_DIR/final.png"

# Cleanup
echo -e "\n${BLUE}Terminating app...${NC}"
ios-agent app terminate --device "$DEVICE_ID" --bundle "$BUNDLE_ID"

echo -e "\n${GREEN}================================${NC}"
echo -e "${GREEN}Vision-guided test complete!${NC}"
echo -e "${GREEN}Screenshots: $OUTPUT_DIR${NC}"
echo -e "${GREEN}================================${NC}"

# Show captured screenshots
ls -lh "$OUTPUT_DIR"/*.png

echo -e "\n${YELLOW}Note: This demo uses a simple state machine.${NC}"
echo -e "${YELLOW}Real implementation would use Claude vision API to analyze screenshots.${NC}"
