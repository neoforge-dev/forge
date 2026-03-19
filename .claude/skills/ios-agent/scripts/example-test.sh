#!/bin/bash
# Example iOS Agent Test Script
# Demonstrates basic ios-agent workflow

set -e

# Configuration
BUNDLE_ID="com.example.app"
OUTPUT_DIR="./test-results"
DEVICE_NAME="iPhone 15"

# Colors for output
GREEN='\033[0;32m'
BLUE='\033[0;34m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -e "${BLUE}iOS Agent Test Script${NC}"
echo "================================"

# Create output directory
mkdir -p "$OUTPUT_DIR"

# Step 1: Discover devices
echo -e "\n${BLUE}[1/7] Discovering devices...${NC}"
DEVICES=$(ios-agent devices)
echo "$DEVICES" | jq .

# Get first booted device or boot one
DEVICE_ID=$(echo "$DEVICES" | jq -r '.result.devices[] | select(.state=="Booted") | .id' | head -1)

if [ -z "$DEVICE_ID" ]; then
    echo -e "${BLUE}No booted device found. Booting $DEVICE_NAME...${NC}"
    BOOT_RESULT=$(ios-agent simulator boot --name "$DEVICE_NAME")
    echo "$BOOT_RESULT" | jq .

    DEVICE_ID=$(echo "$BOOT_RESULT" | jq -r '.result.device_id')

    # Wait for boot
    echo "Waiting for simulator to boot..."
    sleep 5
fi

echo -e "${GREEN}Using device: $DEVICE_ID${NC}"

# Step 2: Launch app
echo -e "\n${BLUE}[2/7] Launching app...${NC}"
LAUNCH_RESULT=$(ios-agent app launch --device "$DEVICE_ID" --bundle "$BUNDLE_ID" --wait-for-ready)
SUCCESS=$(echo "$LAUNCH_RESULT" | jq -r '.success')

if [ "$SUCCESS" != "true" ]; then
    echo -e "${RED}App launch failed!${NC}"
    echo "$LAUNCH_RESULT" | jq '.error'
    exit 1
fi

echo -e "${GREEN}App launched successfully${NC}"

# Step 3: Wait for app to initialize
echo -e "\n${BLUE}[3/7] Waiting for app initialization...${NC}"
sleep 2

# Step 4: Take initial screenshot
echo -e "\n${BLUE}[4/7] Capturing initial state...${NC}"
ios-agent screenshot --device "$DEVICE_ID" --output "$OUTPUT_DIR/01_initial.png"
echo -e "${GREEN}Screenshot saved: $OUTPUT_DIR/01_initial.png${NC}"

# Step 5: Perform UI interactions
echo -e "\n${BLUE}[5/7] Performing UI interactions...${NC}"

# Example: Tap on login button (adjust coordinates for your app)
echo "  Tapping at (185, 400)..."
ios-agent io tap --device "$DEVICE_ID" --x 185 --y 400
sleep 1

# Take screenshot after tap
ios-agent screenshot --device "$DEVICE_ID" --output "$OUTPUT_DIR/02_after_tap.png"
echo -e "${GREEN}Screenshot saved: $OUTPUT_DIR/02_after_tap.png${NC}"

# Example: Type text into focused field
echo "  Typing text..."
ios-agent io text --device "$DEVICE_ID" --text "test@example.com"
sleep 1

# Take screenshot after text input
ios-agent screenshot --device "$DEVICE_ID" --output "$OUTPUT_DIR/03_after_text.png"
echo -e "${GREEN}Screenshot saved: $OUTPUT_DIR/03_after_text.png${NC}"

# Step 6: Capture final state
echo -e "\n${BLUE}[6/7] Capturing final state...${NC}"
ios-agent screenshot --device "$DEVICE_ID" --output "$OUTPUT_DIR/04_final.png"
echo -e "${GREEN}Screenshot saved: $OUTPUT_DIR/04_final.png${NC}"

# Step 7: Cleanup
echo -e "\n${BLUE}[7/7] Cleaning up...${NC}"
TERMINATE_RESULT=$(ios-agent app terminate --device "$DEVICE_ID" --bundle "$BUNDLE_ID")
echo "$TERMINATE_RESULT" | jq .

# Summary
echo -e "\n${GREEN}================================${NC}"
echo -e "${GREEN}Test completed successfully!${NC}"
echo -e "${GREEN}Results saved to: $OUTPUT_DIR${NC}"
echo -e "${GREEN}================================${NC}"

# List screenshots
echo -e "\nScreenshots captured:"
ls -lh "$OUTPUT_DIR"/*.png
