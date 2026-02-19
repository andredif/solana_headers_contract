# Deployment and Testing Guide

This guide explains how to deploy the Solana Fee Distribution smart contract to testnet and run integration tests.

## Prerequisites

### Required Software
- Solana CLI (v1.18+) - [Install Guide](https://docs.solana.com/cli/install-solana-cli-tools)
- Anchor Framework (0.29.0+) - [Install Guide](https://www.anchor-lang.com/docs/installation)
- Rust (1.70+)
- Python 3.8+
- Git

### Accounts and Funding
1. **Create a keypair for deployment:**
   ```bash
   solana-keygen new --force
   # Default location: ~/.config/solana/id.json
   ```

2. **Get testnet SOL (required for deployment):**
   - Visit the [Solana Faucet](https://faucet.solana.com/)
   - Request testnet SOL (minimum 2 SOL recommended for deployment)
   - Verify with: `solana balance`

## Deployment Steps

### Step 1: Prepare Your Environment

```bash
cd /home/andrea/Desktop/solana_contract
source venv/bin/activate
```

### Step 2: Deploy the Contract

Run the deployment script with your keypair:

```bash
# Deploy to testnet (recommended for initial testing)
python deploy.py ~/.config/solana/id.json testnet

# Or deploy to devnet
python deploy.py ~/.config/solana/id.json devnet
```

The script will:
1. ✓ Load your keypair
2. ✓ Configure Solana CLI
3. ✓ Check your account balance
4. ✓ Build the Anchor program
5. ✓ Deploy to testnet
6. ✓ Extract and save the Program ID

**Expected Output:**
```
============================================================
Solana Fee Distribution Contract Deployment
============================================================
✓ Keypair loaded from ~/.config/solana/id.json
✓ Solana keypair configured
✓ RPC URL configured to testnet
✓ Account balance: X.XXXXXX SOL
✓ Program built successfully

📦 Building Anchor program...
✓ Program built successfully

🚀 Deploying to testnet...
✓ Deployment successful!
✓ Program ID: <YOUR_PROGRAM_ID>

✓ Configuration saved to .env

============================================================
✓ Deployment Complete!
============================================================
```

### Step 3: Verify Deployment

The Program ID will be automatically saved to `.env`. Verify the deployment:

```bash
# Check your program is on-chain
solana program show <PROGRAM_ID>

# View recent transaction
solana confirm <TRANSACTION_HASH>
```

## Integration Testing

### Step 1: Update Environment Configuration

The deployment script automatically updates `.env` with your Program ID. Verify the `.env` file:

```bash
cat .env
```

Should contain:
```
SOLANA_NETWORK=testnet
PROGRAM_ID=<your_deployed_program_id>
SOLANA_RPC_URL=https://api.testnet.solana.com
```

### Step 2: Run Integration Tests

Run the complete integration test suite:

```bash
# Activate virtual environment
source venv/bin/activate

# Run all integration tests
pytest tests/integration_tests.py -v

# Run specific test class
pytest tests/integration_tests.py::TestContractDeployment -v

# Run specific test
pytest tests/integration_tests.py::TestRentSpaceFunction::test_fee_calculation_accuracy -v

# Run with markers
pytest tests/integration_tests.py -m integration -v
```

### Available Test Suites

#### TestContractDeployment
- `test_program_id_configured` - Verify Program ID is set
- `test_rpc_endpoint_accessible` - Verify network connectivity
- `test_program_exists` - Verify program is on-chain

#### TestContractInitialization
- `test_initialize_contract_state` - Test contract initialization
- `test_contract_state_persistence` - Verify state persists

#### TestRentSpaceFunction
- `test_rent_space_payment_processing` - Test payment processing
- `test_fee_calculation_accuracy` - Test 20% fee calculation
- `test_rent_space_event_emission` - Verify events are emitted
- `test_insufficient_balance_rejection` - Test balance validation

#### TestFeeDistribution
- `test_distribute_fees_to_holders` - Test proportional distribution
- `test_distribute_fees_event` - Verify distribution events
- `test_zero_distribution_rejection` - Test zero-amount rejection

#### TestOwnerManagement
- `test_update_owner_authorization` - Test authorization
- `test_owner_change_persistence` - Verify state changes
- `test_new_owner_authorization` - Test new owner privileges

#### TestErrorHandling
- `test_invalid_recipient_rejection` - Test validation
- `test_overflow_protection` - Test overflow safety
- `test_missing_required_accounts` - Test account validation

## Test Development Workflow

### Creating a New Integration Test

Example test that interacts with the contract:

```python
@pytest.mark.integration
def test_my_feature(self, solana_client, program_id):
    """Test a specific feature on-chain."""
    # 1. Set up test data
    test_value = 1000000
    
    # 2. Create and send transaction
    # tx = create_transaction(program_id, test_value)
    # signature = solana_client.send_transaction(tx)
    
    # 3. Wait for confirmation
    # assert solana_client.wait_for_confirmation(signature)
    
    # 4. Verify on-chain state
    # account_info = solana_client.client.get_account_info(...)
    # assert account_info.value is not None
```

### Running Tests with Logging

```bash
# Verbose output
pytest tests/integration_tests.py -vv

# Show print statements
pytest tests/integration_tests.py -v -s

# With coverage
pytest tests/integration_tests.py -v --cov=tests
```

## Troubleshooting

### Deployment Issues

#### "Anchor CLI not found"
```bash
# Install Anchor
cargo install --git https://github.com/coral-xyz/anchor --tag v0.29.0 anchor-cli
```

#### "Solana CLI not found"
```bash
# Install Solana
sh -c "$(curl -sSfL https://release.solana.com/v1.18.0/install)"
```

#### "Insufficient balance"
Request more testnet SOL from the [Solana Faucet](https://faucet.solana.com/)

#### Build failed with "cargo not found"
Install Rust: https://rustup.rs/

### Test Issues

#### "PROGRAM_ID not set in .env"
Run the deployment script again to generate the configuration:
```bash
python deploy.py ~/.config/solana/id.json testnet
```

#### "RPC endpoint not accessible"
Check your internet connection and that the network is correct:
```bash
solana cluster-version
```

#### Tests timeout
- May indicate network issues
- Increase timeout or retry
- Check transaction status: `solana confirm <TX_HASH>`

## Network Comparison

| Feature | Devnet | Testnet | Mainnet |
|---------|--------|---------|---------|
| Cost | Free | Free | Real SOL |
| Stability | Unstable | Stable | Stable |
| Data Reset | Frequent | Rare | Never |
| RPC Limit | Generous | Moderate | Strict |
| Ideal For | Development | Testing | Production |

### Switching Networks

```bash
# Testnet (recommended for testing)
python deploy.py ~/.config/solana/id.json testnet

# Devnet (for rapid development)
python deploy.py ~/.config/solana/id.json devnet

# Check current configuration
solana config get
```

## Production Deployment

Before deploying to mainnet:

1. **Audit the contract:**
   ```bash
   # Review all contract code
   # Consider professional audit
   ```

2. **Test thoroughly:**
   ```bash
   # Run full test suite on testnet
   pytest tests/ -v
   ```

3. **Verify security:**
   - Check for arithmetic overflows ✓
   - Verify access controls ✓
   - Test error handling ✓
   - Check state persistence ✓

4. **Deploy to mainnet:**
   ```bash
   python deploy.py ~/.config/solana/id.json mainnet-beta
   ```

## Monitoring

### View Transaction History

```bash
# Show all transactions for your wallet
solana address
solana account <ADDRESS>

# View recent transactions
solana transaction-history
```

### Check Program Upgrades

```bash
# View program authority
solana program show <PROGRAM_ID>

# View program data
solana account <PROGRAM_DATA_ADDRESS>
```

## Next Steps

1. Deploy to testnet using the deployment script
2. Run integration tests against deployed contract
3. Verify all functions work correctly
4. Test with real token holders
5. Perform security audit
6. Deploy to mainnet when ready

## Resources

- [Solana Documentation](https://docs.solana.com/)
- [Anchor Documentation](https://www.anchor-lang.com/docs/)
- [Solana Python SDK](https://github.com/kevinheavey/solders)
- [Solana CLI Reference](https://docs.solana.com/cli/conventions)
