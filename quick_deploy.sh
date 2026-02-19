#!/bin/bash

# Quick Deploy Helper Script
# Usage: ./quick_deploy.sh <keypair_path> [network]

set -e

# Colors for output
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# Check arguments
if [ $# -lt 1 ]; then
    echo -e "${RED}Usage: ./quick_deploy.sh <keypair_path> [network]${NC}"
    echo ""
    echo "Arguments:"
    echo "  keypair_path  Path to Solana keypair (required)"
    echo "  network       Network: devnet, testnet, mainnet-beta (default: testnet)"
    echo ""
    echo "Examples:"
    echo "  ./quick_deploy.sh ~/.config/solana/id.json"
    echo "  ./quick_deploy.sh ~/.config/solana/id.json testnet"
    echo "  ./quick_deploy.sh /path/to/keypair.json devnet"
    exit 1
fi

KEYPAIR_PATH="$1"
NETWORK="${2:-testnet}"

# Validate keypair path
if [ ! -f "$KEYPAIR_PATH" ]; then
    echo -e "${RED}✗ Error: Keypair file not found at $KEYPAIR_PATH${NC}"
    exit 1
fi

echo -e "${BLUE}============================================================${NC}"
echo -e "${BLUE}Solana Fee Distribution Contract - Quick Deploy${NC}"
echo -e "${BLUE}============================================================${NC}"

# Step 1: Activate virtual environment
echo -e "\n${YELLOW}Step 1: Activating Python environment...${NC}"
if [ -d "venv" ]; then
    source venv/bin/activate
    echo -e "${GREEN}✓ Virtual environment activated${NC}"
else
    echo -e "${RED}✗ Virtual environment not found${NC}"
    echo "Run: bash setup_env.sh"
    exit 1
fi

# Step 2: Deploy
echo -e "\n${YELLOW}Step 2: Deploying contract to $NETWORK...${NC}"
python deploy.py "$KEYPAIR_PATH" "$NETWORK"

# Step 3: Show test command
echo -e "\n${BLUE}============================================================${NC}"
echo -e "${GREEN}✓ Deployment Complete!${NC}"
echo -e "${BLUE}============================================================${NC}"
echo ""
echo -e "${YELLOW}Next: Run integration tests${NC}"
echo "  pytest tests/integration_tests.py -v"
echo ""
echo -e "${YELLOW}Or run examples:${NC}"
echo "  python examples/contract_interaction_example.py"
echo ""
echo -e "${BLUE}============================================================${NC}"
