#!/bin/bash
# Linux: run with `./start_linux.sh`
cd "$(dirname "$0")"
clear

echo "========================================"
echo "  Browser Privacy User Study"
echo "========================================"
echo ""

# Check Python
if ! command -v python3 &>/dev/null; then
    echo "[ERROR] Python3 is not installed."
    echo "        Install with: sudo apt install python3 python3-pip"
    echo ""
    read -p "Press any key to exit..."
    exit 1
fi

# Auto-install mitmproxy
if ! command -v mitmdump &>/dev/null; then
    echo "[SETUP] Installing mitmproxy... (first time only)"
    pip3 install --user mitmproxy --break-system-packages 2>/dev/null || pip3 install --user mitmproxy
    export PATH="$HOME/.local/bin:$PATH"
    echo ""
fi

# Ask participant ID
echo ""
read -p "Enter your participant ID (e.g. P001): " PID
PID=$(echo "$PID" | tr '[:lower:]' '[:upper:]')

if [[ ! "$PID" =~ ^P[0-9]{3}$ ]]; then
    echo "[ERROR] Expected format: P001 ~ P020"
    read -p "Press any key to exit..."
    exit 1
fi

echo ""
echo "Starting study..."
echo ""
python3 study_client.py --participant "$PID"

echo ""
read -p "Press any key to exit..."
