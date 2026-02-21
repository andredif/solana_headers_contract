# Contract Full Test Script

A comprehensive Python test suite for the Solana Fee Distribution contract that tests all contract functionalities using the `ContractInteractor` class.

## Features

The `contract_full_test.py` script performs a full test of all contract operations:

1. **Contract Initialization** - Tests creating an initialize instruction
2. **Get Contract State** - Tests PDA derivation for contract state
3. **Update Supply Snapshot** - Tests owner-only supply snapshot updates
4. **Initialize Holder State** - Tests holder state initialization
5. **Get Holder State** - Tests holder state PDA derivation
6. **Create Rent Space Instruction** - Tests payment instruction creation
7. **Fee Calculation** - Tests 20/80 fee split calculation
8. **Multiple Rent Space Payments** - Tests batch payment instruction creation
9. **Create Claim Fees Instruction** - Tests fee claiming instruction creation
10. **Update Owner** - Tests ownership transfer instruction creation
11. **PDA Derivation** - Tests all PDA derivations (contract, fee vault, holder state, fee record)
12. **Claimable Fees Calculation** - Tests claimable fees calculation

## Installation

Ensure you have the required dependencies installed:

```bash
pip install solders solana
```

## Usage

### Option 1: Using Command-Line Arguments

```bash
python contract_full_test.py \
    --network localnet \
    --program-id CkM4AcKu7LSXikXaTvxwuTDBXmT2yrEZTvXbJ6Kdrox7 \
    --token-address 9xDeTuFZiTd98A926MhgnTzHrSmNJjVB86uS595EEuxF
```

### Option 2: Using Configuration File

Create a `config.json` file with your settings:

```json
{
  "network": "localnet",
  "program_id": "CkM4AcKu7LSXikXaTvxwuTDBXmT2yrEZTvXbJ6Kdrox7",
  "governance_token_address": "9xDeTuFZiTd98A926MhgnTzHrSmNJjVB86uS595EEuxF",
  "keypairs": {
    "owner": [1, 2, 3, ...],
    "payer": [1, 2, 3, ...],
    "recipient": [1, 2, 3, ...],
    "holder": [1, 2, 3, ...]
  }
}
```

Then run:

```bash
python contract_full_test.py --config config.json
```

### Option 3: Using Keypair File

```bash
python contract_full_test.py \
    --network localnet \
    --program-id <PROGRAM_ID> \
    --token-address <TOKEN_ADDRESS> \
    --keys keypairs.json
```

## Configuration File Format

The configuration JSON file should contain:

| Field | Type | Description |
|-------|------|-------------|
| `network` | string | Solana network: `localnet`, `devnet`, `testnet`, or `mainnet-beta` |
| `program_id` | string | The contract program's public key |
| `governance_token_address` | string | Governance token mint address |
| `keypairs` | object | Keypairs for different roles (owner, payer, recipient, holder) |

### Keypair Format

Keypairs should be provided as arrays of bytes (0-255). You can generate them:

```python
from solders.keypair import Keypair
import json

keypair = Keypair()
keypair_array = list(bytes(keypair))
print(json.dumps(keypair_array))
```

## Test Output

The script produces detailed output showing:

- Test name and status (✓ PASS, ✗ FAIL, ⊘ SKIP)
- Descriptive message for each test
- Relevant details (amounts, addresses, counts, etc.)
- Error messages if tests fail
- Final summary with pass rate

Example output:

```
======================================================================
SOLANA FEE DISTRIBUTION CONTRACT - FULL TEST SUITE
======================================================================

[TEST 1] Contract Initialization
----------------------------------------------------------------------
✓ PASS: Initialize Contract
    Successfully created initialize instruction
    • program_id: CkM4AcKu7LSXikXaTvxwuTDBXmT2yrEZTvXbJ6Kdrox7
    • num_accounts: 7
    • data_length: 72

...

======================================================================
TEST SUMMARY
======================================================================

Total Tests: 12
  ✓ Passed:  12
  ✗ Failed:  0
  ⊘ Skipped: 0

Success Rate: 100.0%
======================================================================
```

## Exit Codes

- `0` - All tests passed
- `1` - One or more tests failed or configuration error

## Advanced Usage

### Testing with Custom Network

```bash
python contract_full_test.py \
    --network devnet \
    --program-id <DEVNET_PROGRAM_ID> \
    --token-address <DEVNET_TOKEN_ADDRESS> \
    --keys devnet_keys.json
```

### Generating Test Configuration

Create test keypairs and configuration:

```python
#!/usr/bin/env python3
import json
from solders.keypair import Keypair

config = {
    "network": "localnet",
    "program_id": "CkM4AcKu7LSXikXaTvxwuTDBXmT2yrEZTvXbJ6Kdrox7",
    "governance_token_address": "9xDeTuFZiTd98A926MhgnTzHrSmNJjVB86uS595EEuxF",
    "keypairs": {
        "owner": list(bytes(Keypair())),
        "payer": list(bytes(Keypair())),
        "recipient": list(bytes(Keypair())),
        "holder": list(bytes(Keypair())),
    }
}

with open("config.json", "w") as f:
    json.dump(config, f, indent=2)
```

## Test Constants

The script uses the following test constants:

```python
PAYMENT_AMOUNT = 500_000_000  # 0.5 SOL in lamports
HOLDER_TOKEN_BALANCE = 1_000_000  # 1M tokens
INITIAL_SUPPLY = 1_000_000_000_000  # 1T tokens
```

These can be modified in the `ContractFullTest` class if needed.

## Troubleshooting

### Error: "Missing 'owner' keypair"

Ensure all four required keypairs are provided: `owner`, `payer`, `recipient`, `holder`

### Error: "Invalid Pubkey"

Check that addresses are valid Solana public keys (44 characters in base58 format)

### Error: "Network connection failed"

Verify that the RPC endpoint for your network is accessible:
- Localnet: `http://127.0.0.1:8899`
- Devnet: `https://api.devnet.solana.com`
- Testnet: `https://api.testnet.solana.com`
- Mainnet: `https://api.mainnet-beta.solana.com`

## Integration with CI/CD

The script can be integrated into CI/CD pipelines:

```bash
#!/bin/bash
set -e

# Run tests
cd /path/to/contract
python contract_full_test.py --config test_config.json

echo "All contract tests passed!"
```

## See Also

- [ContractInteractor Documentation](tests/contract_helper.py)
- [Example Usage](examples/contract_interaction_example.py)
- [Integration Tests](tests/test_fee_distribution.py)

## License

Same as the main contract repository
