# Local Testing & Deployment Setup Guide

## Quick Deploy Setup

Instead of manually updating keypairs before each deployment, use the automated setup script:

```bash
./setup_new_deploy.sh
```

This script will:
- ✅ Generate a new program keypair
- ✅ Update `lib.rs` with the new `declare_id!`
- ✅ Update `Anchor.toml` with the new program address
- ✅ Build the contract with the new keypair
- ✅ Print the new Program ID

Then deploy:
```bash
ANCHOR_WALLET=./id.json anchor deploy --program-name fee_distribution --provider.cluster testnet
```

---

## Local Testing (No Testnet Required)

### Option 1: Test with Local Validator (Recommended)

This runs your contract against a local Solana validator without deploying to testnet.

**Step 1: Start the local validator** (in one terminal):
```bash
solana-test-validator
```

**Step 2: Build the contract** (in another terminal):
```bash
cargo-build-sbf --manifest-path programs/fee_distribution/Cargo.toml
```

**Step 3: Check the contract setup**:
```bash
python tests/local_test.py
```

This shows contract instructions, accounts, and events without deploying.

**Step 4: Run integration tests** (Anchor test framework):
```bash
anchor test --skip-deploy
```

This will:
- Use the local validator
- Deploy the contract locally
- Run TypeScript tests
- Clean up automatically

### Option 2: Using Anchor Test (TypeScript)

For full integration testing with the local validator:

```bash
anchor test
```

This:
- ✅ Starts a local validator automatically
- ✅ Builds the contract
- ✅ Deploys to local chain
- ✅ Runs TypeScript test suite
- ✅ Cleans up afterward

### Option 3: Python Integration Tests

Create a Python test file (e.g., `tests/local_integration_test.py`):

```python
from solders.keypair import Keypair
from solana.rpc.api import Client
from solders.transaction import Transaction
from solders.instructions import Instruction

# Configuration
client = Client("http://localhost:8899")
payer = Keypair()  # or load from file
program_id = Pubkey("CkM4AcKu7LSXikXaTvxwuTDBXmT2yrEZTvXbJ6Kdrox7")

# Example: Create contract state account and call initialize
def test_initialize():
    # 1. Create instruction bytes
    # 2. Build transaction
    # 3. Send to client
    pass

if __name__ == "__main__":
    test_initialize()
```

---

## Workflow Comparison

### Before (Manual):
```bash
solana-keygen new --no-passphrase -so target/deploy/fee_distribution-keypair.json --force
# Get address
NEW_ID=$(solana address -k target/deploy/fee_distribution-keypair.json)
# Manually edit lib.rs
# Manually edit Anchor.toml
cargo-build-sbf --manifest-path programs/fee_distribution/Cargo.toml
ANCHOR_WALLET=./id.json anchor deploy --program-name fee_distribution --provider.cluster testnet
```

### After (Automated):
```bash
./setup_new_deploy.sh
ANCHOR_WALLET=./id.json anchor deploy --program-name fee_distribution --provider.cluster testnet
```

---

## Testing Strategy

| Test Type | Command | Network | Time | Cost |
|-----------|---------|---------|------|------|
| **Local Unit** | `cargo test` | None | ~1s | Free |
| **Local Integration** | `anchor test` | Local validator | ~10s | Free |
| **Python Integration** | `python tests/local_test.py` | Local validator | ~5s | Free |
| **Testnet** | `anchor deploy` | Testnet | ~30s | ~0.002-0.01 SOL |

**Recommended Workflow:**
1. ✅ Make contract changes
2. ✅ Run `anchor test` (catches most issues)
3. ✅ If passing, run `./setup_new_deploy.sh`
4. ✅ Deploy to testnet
5. ✅ Full integration tests on testnet

---

## Troubleshooting

### "solana-test-validator is not running"
Start validator in another terminal:
```bash
solana-test-validator
```

### "Program binary not found"
Build the contract:
```bash
cargo-build-sbf --manifest-path programs/fee_distribution/Cargo.toml
```

### "DeclaredProgramIdMismatch" error
Run setup script to sync keypairs:
```bash
./setup_new_deploy.sh
```

### Port already in use
Default validator uses port 8899. If in use:
```bash
solana-test-validator --rpc-port 8900
# Then use http://localhost:8900 in tests
```
