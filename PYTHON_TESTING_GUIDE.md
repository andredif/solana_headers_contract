# Python Test Environment Setup Guide

This guide explains how to use the Python test environment for the Solana Fee Distribution smart contract.

## Prerequisites

- Python 3.8 or higher
- Linux, macOS, or Windows (with WSL)

## Quick Start

### 1. Initial Setup

Run the automated setup script to create the virtual environment and install dependencies:

```bash
cd /home/andrea/Desktop/solana_contract
bash setup_env.sh
```

This will:
- Create a Python virtual environment in `venv/`
- Install all required dependencies from `requirements.txt`
- Prepare the environment for testing

### 2. Activate the Virtual Environment

After the initial setup, activate the environment for subsequent sessions:

```bash
source venv/bin/activate
```

On Windows (with WSL):
```bash
source venv/Scripts/activate
```

To deactivate the environment:
```bash
deactivate
```

## Running Tests

### Run All Tests

```bash
pytest tests/ -v
```

### Run Specific Test Class

```bash
pytest tests/test_contract.py::TestRentSpacePayments -v
```

### Run Specific Test

```bash
pytest tests/test_contract.py::TestRentSpacePayments::test_calculate_fee_distribution -v
```

### Run with Coverage

```bash
pip install coverage
coverage run -m pytest tests/ -v
coverage report
coverage html  # Generate HTML report
```

## Test Structure

The test suite is organized into the following test classes:

### TestContractInitialization
Tests for contract initialization functionality:
- `test_initialize_contract_success` - Valid initialization with owner and governance token mint
- `test_initialize_requires_owner` - Owner validation

### TestRentSpacePayments
Tests for rent space payment processing:
- `test_rent_space_payment_amount_validation` - Payment amount validation
- `test_calculate_fee_distribution` - 20% fee calculation
- `test_multiple_payment_scenarios` - Various payment amounts
- `test_rent_space_transaction_data` - Transaction data capture
- `test_arithmetic_overflow_protection` - Overflow safety

### TestFeeDistribution
Tests for fee distribution to token holders:
- `test_distribute_fees_to_token_holders` - Proportional distribution
- `test_zero_token_holder_distribution` - Zero-amount rejection
- `test_accumulated_fees_tracking` - Fee accumulation tracking

### TestOwnerManagement
Tests for owner management:
- `test_update_owner_requires_authorization` - Authorization checks
- `test_owner_address_validation` - Address validation

### TestErrorHandling
Tests for error handling:
- `test_invalid_payment_amount_rejected` - Invalid amount rejection
- `test_unauthorized_operations_blocked` - Unauthorized operation blocking

## Using Fixtures

Pytest fixtures are available in `tests/conftest.py`:

- `owner_address()` - Test owner address
- `governance_token_mint()` - Test governance token mint
- `test_recipient()` - Test recipient address
- `test_payer()` - Test payer address
- `contract_config()` - Contract configuration
- `payment_data()` - Test payment data

Example usage in a test:

```python
def test_with_fixtures(owner_address, governance_token_mint):
    assert owner_address != governance_token_mint
```

## Environment Configuration

Create a `.env` file in the project root for configuration:

```bash
cp .env.example .env
```

Then edit `.env` with your configuration:
- `SOLANA_NETWORK`: Network to test against (devnet, testnet, mainnet)
- `PROGRAM_ID`: Your deployed program ID
- `CONTRACT_OWNER`: Owner wallet address
- `GOVERNANCE_TOKEN_MINT`: Governance token mint address
- `TEST_PAYER_KEYPAIR`: Path to test keypair JSON

Load environment variables in your tests:
```python
import os
from dotenv import load_dotenv

load_dotenv()
program_id = os.getenv('PROGRAM_ID')
```

## Dependencies

The following Python packages are installed:

- **solders** (0.20.0+) - Low-level Solana bindings
- **solana** (0.32.0+) - High-level Solana Python SDK
- **pytest** (7.4.3+) - Testing framework
- **pytest-asyncio** (0.21.1+) - Async test support
- **python-dotenv** (1.0.0+) - Environment variable loading

## Integration Testing with On-Chain Contract

To test against the actual deployed contract:

1. Deploy the contract to devnet:
   ```bash
   cd /home/andrea/Desktop/solana_contract
   anchor deploy --provider.cluster devnet
   ```

2. Update `.env` with the program ID from deployment

3. Create integration tests using the Solana Python SDK to interact with deployed contract

4. Run integration tests:
   ```bash
   pytest tests/integration/ -v
   ```

## Troubleshooting

### Virtual Environment Issues
```bash
# Recreate virtual environment
rm -rf venv
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### Import Errors
Ensure virtual environment is activated:
```bash
which python  # Should show path to venv/bin/python
```

### Test Failures
Run with verbose output:
```bash
pytest tests/ -vv --tb=long
```

## Next Steps

1. Run the initial test suite to verify setup
2. Create integration tests for contract interactions
3. Test against devnet deployment
4. Validate fee distribution logic with real token holders

## Additional Resources

- [Solana Python SDK Documentation](https://github.com/kevinheavey/solders)
- [Pytest Documentation](https://docs.pytest.org/)
- [Solana Documentation](https://docs.solana.com/)
