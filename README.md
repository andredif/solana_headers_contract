# Solana Fee Distribution Smart Contract

A smart contract deployed on Solana mainnet that handles a fee distribution system with token governance integration.

## Overview

This smart contract manages a rent space payment system where:
- **20% of payments** are automatically distributed to governance token holders proportionally to their holdings
- **80% of payments** go to the specified recipient
- All transactions are tracked with recipient address, expiration time, and payment value

## Project Structure

```
solana_contract/
├── programs/
│   └── fee_distribution/          # Main smart contract program
│       ├── src/
│       │   └── lib.rs            # Contract implementation
│       └── Cargo.toml            # Rust dependencies
├── tests/                         # Test files
├── Cargo.toml                    # Workspace configuration
└── README.md                     # This file
```

## Contract Components

### Initialization
The contract is initialized with:
- **Contract Owner**: Address that can manage the contract
- **Governance Token Mint**: Token mint address used for fee distribution calculations

### Main Functions

#### `initialize(owner, governance_token_mint)`
Initializes the contract with the owner address and governance token mint.

#### `rent_space(recipient, expiration_time, payment_amount)`
Processes a rental space payment:
- Receives payment from a Solana address
- Automatically calculates and segregates 20% fee
- Transfers 80% to the recipient
- Records transaction details (recipient, expiration time, amount)
- Emits event for tracking

#### `distribute_fees(token_holder_amount)`
Distributes accumulated fees to governance token holders based on their proportional holdings.

#### `update_owner(new_owner)`
Allows the current owner to update the contract owner address.

## Building

Prerequisites:
- Rust 1.70+
- Solana CLI (v1.18+)
- Anchor CLI (0.29.0+)

```bash
# Build the program
anchor build

# Test the program
anchor test
```

## Deployment & Testing

### Quick Start

1. **Set up Python environment:**
   ```bash
   source venv/bin/activate
   ```

2. **Deploy to testnet:**
   ```bash
   python deploy.py ~/.config/solana/id.json testnet
   ```

3. **Run integration tests:**
   ```bash
   pytest tests/integration_tests.py -v
   ```

### Detailed Documentation

See [DEPLOYMENT_GUIDE.md](DEPLOYMENT_GUIDE.md) for comprehensive deployment and testing instructions.

### Supported Networks

- **Devnet** - Fast development network (SOL resets frequently)
- **Testnet** - Stable testing network (recommended)
- **Mainnet** - Production network (uses real SOL)

```bash
# Deploy to different networks
python deploy.py ~/.config/solana/id.json devnet      # Development
python deploy.py ~/.config/solana/id.json testnet     # Testing
python deploy.py ~/.config/solana/id.json mainnet-beta # Production
```

## Account Structure

### ContractState
Stores the main contract configuration:
- `owner`: Contract owner address
- `governance_token_mint`: Governance token mint address
- `bump`: PDA bump seed

### FeeRecord
Records each rent space transaction:
- `contract`: Contract address
- `payer`: Payment sender
- `recipient`: Payment recipient
- `expiration_time`: Expiration timestamp
- `total_amount`: Full payment amount
- `fee_amount`: 20% fee amount
- `timestamp`: Transaction timestamp

## Events

### RentSpaceEvent
Emitted when a rent space payment is processed.

### DistributionEvent
Emitted when fees are distributed to token holders.

## License

MIT
