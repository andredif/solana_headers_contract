# Testing & Deployment Environment - Complete Guide

## Overview

This guide explains the complete workflow for testing and deploying the Solana Fee Distribution smart contract. The environment includes:

1. **Deployment Script** - Automated deployment to testnet/devnet/mainnet
2. **Integration Tests** - Tests that interact with deployed contracts on-chain
3. **Contract Helper Module** - Utilities for contract interaction
4. **Examples** - Practical examples of contract usage
5. **Python Environment** - Isolated virtual environment with all dependencies

## Project Structure

```
solana_contract/
├── deploy.py                           # Main deployment script
├── examples/
│   └── contract_interaction_example.py # Usage examples
├── tests/
│   ├── conftest.py                     # Pytest fixtures
│   ├── contract_helper.py              # Contract interaction helpers
│   ├── integration_tests.py            # On-chain integration tests
│   └── test_contract.py                # Unit tests
├── programs/
│   └── fee_distribution/
│       ├── src/lib.rs                  # Smart contract code
│       └── Cargo.toml
├── Cargo.toml                          # Workspace configuration
├── requirements.txt                    # Python dependencies
├── setup_env.sh                        # Environment setup script
├── DEPLOYMENT_GUIDE.md                 # Detailed deployment documentation
├── PYTHON_TESTING_GUIDE.md             # Python testing documentation
└── .env                                # Configuration (after deployment)
```

## Quick Start Workflow

### 1. Initial Setup

```bash
# Clone repository
cd /home/andrea/Desktop/solana_contract

# Activate Python environment
source venv/bin/activate

# Verify environment
pip list | grep solana
```

### 2. Deploy to Testnet

```bash
# Deploy with your keypair
python deploy.py ~/.config/solana/id.json testnet

# This will:
# ✓ Load your Solana keypair
# ✓ Configure Solana CLI
# ✓ Check your account balance
# ✓ Build the Anchor program
# ✓ Deploy to testnet
# ✓ Save Program ID to .env
```

**Expected output:**
```
✓ Deployment successful!
✓ Program ID: <YOUR_PROGRAM_ID>
✓ Configuration saved to .env
```

### 3. Run Integration Tests

```bash
# Run all integration tests
pytest tests/integration_tests.py -v

# Run specific test suite
pytest tests/integration_tests.py::TestRentSpaceFunction -v

# Run with markers
pytest tests/integration_tests.py -m integration -v
```

### 4. View Examples

```bash
# Run example scripts
python examples/contract_interaction_example.py
```

## Deployment Script (`deploy.py`)

The deployment script automates the entire deployment process.

### Usage

```bash
python deploy.py <keypair_path> [network]

Arguments:
  keypair_path  Path to Solana keypair JSON (required)
  network       Target network: devnet, testnet, mainnet-beta (default: testnet)

Examples:
  python deploy.py ~/.config/solana/id.json testnet
  python deploy.py ~/.config/solana/id.json devnet
  python deploy.py /path/to/keypair.json mainnet-beta
```

### What It Does

1. **Loads keypair** - Validates your Solana keypair
2. **Configures Solana CLI** - Sets up RPC endpoint and signer
3. **Checks balance** - Ensures sufficient funds for deployment
4. **Builds program** - Compiles the Anchor program
5. **Deploys** - Sends deployment transaction to network
6. **Saves config** - Writes Program ID to `.env`

### Requirements

- Solana CLI installed
- Anchor CLI installed
- Valid Solana keypair
- At least 2 SOL on testnet/devnet or equivalent on mainnet

## Integration Tests (`integration_tests.py`)

Comprehensive test suite that verifies contract behavior on-chain.

### Test Suites

#### TestContractDeployment
- Verify Program ID is configured
- Check RPC endpoint accessibility  
- Confirm program exists on-chain

#### TestRentSpaceFunction
- Test payment processing
- Verify 20% fee calculation
- Check event emission
- Validate balance requirements

#### TestFeeDistribution
- Test proportional distribution
- Verify distribution events
- Check zero-amount rejection

#### TestOwnerManagement
- Test authorization checks
- Verify state persistence
- Check new owner privileges

#### TestErrorHandling
- Test invalid inputs
- Verify overflow protection
- Check account validation

### Running Tests

```bash
# All tests
pytest tests/integration_tests.py -v

# Specific test class
pytest tests/integration_tests.py::TestRentSpaceFunction -v

# Specific test
pytest tests/integration_tests.py::TestRentSpaceFunction::test_fee_calculation_accuracy -v

# With coverage
pytest tests/integration_tests.py --cov=tests -v

# Verbose with output
pytest tests/integration_tests.py -vv -s
```

## Contract Helper Module (`contract_helper.py`)

Provides utilities for interacting with the deployed contract.

### Main Classes

#### ContractInteractor
Real contract interaction with on-chain calls.

```python
from tests.contract_helper import ContractInteractor

# Initialize
interactor = ContractInteractor(
    program_id="YOUR_PROGRAM_ID",
    network="testnet"
)

# Use helper methods
balance = interactor.get_balance(pubkey)
fee, recipient = interactor.calculate_fee(1000000)
logs = interactor.get_transaction_logs(signature)
```

#### MockContractInteractor
Mock implementation for testing without network calls.

```python
from tests.contract_helper import MockContractInteractor

# Initialize
mock = MockContractInteractor()

# Use same interface as ContractInteractor
fee, recipient = mock.calculate_fee(1000000)  # Works offline
```

### Utility Functions

```python
from tests.contract_helper import (
    sol_to_lamports,      # Convert SOL to lamports
    lamports_to_sol,      # Convert lamports to SOL
    validate_pubkey,      # Validate Pubkey string
)

# Examples
lamports = sol_to_lamports(1.0)  # 1000000000
sol = lamports_to_sol(1000000)    # 0.001
pubkey = validate_pubkey("11111111111111111111111111111112")
```

## Examples (`contract_interaction_example.py`)

Practical examples demonstrating contract usage.

### Available Examples

1. **Basic Operations** - Fee calculation and contract initialization
2. **Fee Distribution** - Proportional distribution to token holders
3. **Multiple Payments** - Processing multiple rent_space payments
4. **Account Management** - Airdrop and balance checking
5. **Transaction Flow** - Complete end-to-end flow
6. **Environment Setup** - Configuration management

### Running Examples

```bash
python examples/contract_interaction_example.py
```

Output shows:
- Fee calculations with real numbers
- Proportional distribution examples
- Multiple payment processing
- Account operations
- Complete transaction flow
- Environment configuration guide

## Environment Configuration

After deployment, the `.env` file contains:

```bash
SOLANA_NETWORK=testnet
PROGRAM_ID=<deployed_program_id>
SOLANA_RPC_URL=https://api.testnet.solana.com
```

### Update Configuration

```bash
# Manually update .env
nano .env

# Or redeploy to update
python deploy.py ~/.config/solana/id.json testnet
```

## Troubleshooting

### Deployment Issues

**"Anchor CLI not found"**
```bash
cargo install --git https://github.com/coral-xyz/anchor --tag v0.29.0 anchor-cli
```

**"Insufficient balance"**
```bash
# Request testnet SOL from faucet
solana airdrop 2  # Or visit https://faucet.solana.com/
```

**Build failed**
```bash
# Clean and rebuild
rm -rf target/
anchor build
```

### Test Issues

**"PROGRAM_ID not set"**
```bash
python deploy.py ~/.config/solana/id.json testnet
```

**"RPC endpoint not accessible"**
```bash
# Check network
solana cluster-version

# Verify RPC URL
echo $SOLANA_RPC_URL
```

**Tests timeout**
- Increase timeout in test fixtures
- Check network connectivity
- Verify RPC endpoint availability

## Development Workflow

### For Local Testing

```bash
# 1. Run unit tests (no network needed)
pytest tests/test_contract.py -v

# 2. Run example script
python examples/contract_interaction_example.py

# 3. Deploy to testnet
python deploy.py ~/.config/solana/id.json testnet

# 4. Run integration tests
pytest tests/integration_tests.py -v
```

### For Production Deployment

```bash
# 1. Test thoroughly on testnet
pytest tests/ -v

# 2. Perform security review
# - Verify all arithmetic checks
# - Check authorization controls
# - Test edge cases

# 3. Deploy to mainnet
python deploy.py ~/.config/solana/id.json mainnet-beta

# 4. Monitor transactions
solana transaction-history
```

## Fee Distribution Details

### Fee Calculation

```
Payment: 1.0 SOL (1,000,000,000 lamports)
Fee (20%): 0.2 SOL (200,000,000 lamports)
Recipient (80%): 0.8 SOL (800,000,000 lamports)
```

### Proportional Distribution Example

```
Total Fees: 5.0 SOL

Token Holders:
  Alice (50% holding) → 2.5 SOL
  Bob (30% holding)  → 1.5 SOL
  Charlie (20% holding) → 1.0 SOL
  Total: 5.0 SOL
```

## Network Comparison

| Aspect | Devnet | Testnet | Mainnet |
|--------|--------|---------|---------|
| Cost | Free | Free | Real SOL |
| Stability | Low | High | Highest |
| Data Reset | Frequent | Rare | Never |
| Best For | Development | Testing | Production |
| RPC Limit | Generous | Moderate | Strict |

## Next Steps

1. ✓ Set up Python environment
2. ✓ Deploy to testnet
3. ✓ Run integration tests
4. ✓ Review test results
5. → Implement additional tests for edge cases
6. → Test with real token governance setup
7. → Perform security audit
8. → Deploy to mainnet

## Files Reference

### Key Files

- [deploy.py](deploy.py) - Deployment script
- [tests/integration_tests.py](tests/integration_tests.py) - Integration tests
- [tests/contract_helper.py](tests/contract_helper.py) - Helper utilities
- [examples/contract_interaction_example.py](examples/contract_interaction_example.py) - Examples
- [DEPLOYMENT_GUIDE.md](DEPLOYMENT_GUIDE.md) - Detailed deployment guide
- [PYTHON_TESTING_GUIDE.md](PYTHON_TESTING_GUIDE.md) - Python testing guide

### Documentation

- [README.md](README.md) - Project overview
- [DEPLOYMENT_GUIDE.md](DEPLOYMENT_GUIDE.md) - Deployment and testing
- [PYTHON_TESTING_GUIDE.md](PYTHON_TESTING_GUIDE.md) - Python environment guide

## Support

For issues or questions:

1. Check the [DEPLOYMENT_GUIDE.md](DEPLOYMENT_GUIDE.md) troubleshooting section
2. Review the [PYTHON_TESTING_GUIDE.md](PYTHON_TESTING_GUIDE.md)
3. Run examples: `python examples/contract_interaction_example.py`
4. Check test output: `pytest tests/integration_tests.py -vv`
5. Review Solana docs: https://docs.solana.com/
6. Review Anchor docs: https://www.anchor-lang.com/docs/
