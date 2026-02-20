#!/bin/bash
# setup_new_deploy.sh - Prepares a fresh deployment with new program keypair

set -e

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
KEYPAIR_PATH="$PROJECT_DIR/target/deploy/fee_distribution-keypair.json"

echo "🔑 Generating new program keypair..."
solana-keygen new --no-passphrase -so "$KEYPAIR_PATH" --force

# Extract the new program ID
NEW_PROGRAM_ID=$(solana address -k "$KEYPAIR_PATH")
echo "✅ New Program ID: $NEW_PROGRAM_ID"

echo ""
echo "📝 Updating lib.rs..."
# Update lib.rs declare_id!
sed -i "s/declare_id!(\"[^\"]*\");/declare_id!(\"$NEW_PROGRAM_ID\");/" "$PROJECT_DIR/programs/fee_distribution/src/lib.rs"

echo "📝 Updating Anchor.toml..."
# Update Anchor.toml testnet address
sed -i "s/fee_distribution = \"[^\"]*\"  # testnet/fee_distribution = \"$NEW_PROGRAM_ID\"  # testnet/" "$PROJECT_DIR/Anchor.toml"
# Also handle the case without the comment
sed -i "/\[programs\.testnet\]/,/^$/{s/fee_distribution = \"[^\"]*\"/fee_distribution = \"$NEW_PROGRAM_ID\"/}" "$PROJECT_DIR/Anchor.toml"

echo ""
echo "Building contract with new keypair..."
cargo-build-sbf --manifest-path "$PROJECT_DIR/programs/fee_distribution/Cargo.toml" > /dev/null 2>&1

echo ""
echo "✨ Setup complete! Ready to deploy."
echo ""
echo "Next step: Run deployment"
echo "  cd $PROJECT_DIR"
echo "  ANCHOR_WALLET=./id.json anchor deploy --program-name fee_distribution --provider.cluster testnet"
