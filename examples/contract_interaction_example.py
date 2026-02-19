#!/usr/bin/env python3
"""
Example: Using the Contract Helper Module

This script demonstrates how to use the ContractInteractor helper
to interact with the Fee Distribution contract.

Usage:
    python examples/contract_interaction_example.py
"""

import os
import sys
from pathlib import Path

# Add parent directory to path to import test modules
sys.path.insert(0, str(Path(__file__).parent.parent))

from tests.contract_helper import (
    ContractInteractor,
    MockContractInteractor,
    NetworkType,
    sol_to_lamports,
    lamports_to_sol,
)
from solders.pubkey import Pubkey



def example_basic_operations():
    """Example: Basic contract operations."""
    print("=" * 60)
    print("Example 1: Basic Contract Operations")
    print("=" * 60)
    
    # Create interactor (using mock for demo)
    interactor = MockContractInteractor()
    
    print(f"\nProgram ID: {interactor.program_id}")
    print(f"Network: {interactor.network}")
    
    # Example: Calculate fees
    payment_amount = sol_to_lamports(1.0)  # 1 SOL
    fee, recipient = interactor.calculate_fee(payment_amount)
    
    print(f"\nPayment amount: {lamports_to_sol(payment_amount)} SOL")
    print(f"Fee (20%): {lamports_to_sol(fee)} SOL")
    print(f"Recipient amount (80%): {lamports_to_sol(recipient)} SOL")


def example_fee_distribution():
    """Example: Fee distribution to token holders."""
    print("\n" + "=" * 60)
    print("Example 2: Fee Distribution Calculation")
    print("=" * 60)
    
    # Simulate token holders
    token_holders = {
        "Alice": 0.50,   # 50% of supply
        "Bob": 0.30,     # 30% of supply
        "Charlie": 0.20, # 20% of supply
    }
    
    # Total accumulated fees
    total_fees = sol_to_lamports(5.0)  # 5 SOL in fees
    
    print(f"\nTotal fees to distribute: {lamports_to_sol(total_fees)} SOL")
    print("\nToken holder distributions:")
    print("-" * 60)
    
    total_distributed = 0
    for holder, percentage in token_holders.items():
        distribution = int(total_fees * percentage)
        total_distributed += distribution
        print(f"{holder:15} ({percentage*100:>3.0f}%) → {lamports_to_sol(distribution):.6f} SOL")
    
    print("-" * 60)
    print(f"{'Total distributed':15} → {lamports_to_sol(total_distributed):.6f} SOL")
    print(f"Verification: {total_distributed == total_fees}")


def example_multiple_payments():
    """Example: Processing multiple rent_space payments."""
    print("\n" + "=" * 60)
    print("Example 3: Multiple Rent Space Payments")
    print("=" * 60)
    
    interactor = MockContractInteractor()
    
    # Simulate multiple payments
    payments = [
        ("Alice", 0.5),    # 0.5 SOL
        ("Bob", 1.0),      # 1.0 SOL
        ("Charlie", 2.5),  # 2.5 SOL
    ]
    
    print(f"\nProcessing {len(payments)} payments:")
    print("-" * 60)
    print(f"{'Payer':15} {'Amount':>12} {'Fee (20%)':>12} {'Recipient (80%)':>15}")
    print("-" * 60)
    
    total_fees = 0
    total_recipients = 0
    
    for payer, amount_sol in payments:
        amount_lamports = sol_to_lamports(amount_sol)
        fee, recipient = interactor.calculate_fee(amount_lamports)
        
        total_fees += fee
        total_recipients += recipient
        
        print(f"{payer:15} {lamports_to_sol(amount_lamports):>12.6f} {lamports_to_sol(fee):>12.6f} {lamports_to_sol(recipient):>15.6f}")
    
    print("-" * 60)
    print(f"{'TOTAL':15} {lamports_to_sol(total_fees + total_recipients):>12.6f} {lamports_to_sol(total_fees):>12.6f} {lamports_to_sol(total_recipients):>15.6f}")


def example_account_management():
    """Example: Account management."""
    print("\n" + "=" * 60)
    print("Example 4: Account Management")
    print("=" * 60)
    
    interactor = MockContractInteractor()
    
    # Create test account
    test_account = Pubkey.from_string("11111111111111111111111111111112")
    
    print(f"\nTest account: {test_account}")
    
    # Airdrop SOL
    airdrop_amount = 10.0
    tx_sig = interactor.airdrop(test_account, airdrop_amount)
    print(f"Airdrop: {airdrop_amount} SOL")
    print(f"Transaction signature: {tx_sig}")
    
    # Check balance
    balance = interactor.get_balance(test_account)
    print(f"Account balance: {lamports_to_sol(balance)} SOL")


def example_transaction_flow():
    """Example: Complete transaction flow."""
    print("\n" + "=" * 60)
    print("Example 5: Complete Transaction Flow")
    print("=" * 60)
    
    print("""
Transaction Flow for Rent Space Payment:

1. User initiates rent_space transaction with:
   - Recipient address
   - Expiration time
   - Payment amount

2. Smart contract executes:
   - Validates payment amount (must be > 0)
   - Calculates 20% fee
   - Transfers 80% to recipient
   - Records transaction with expiration

3. Fee accumulation:
   - 20% fees are accumulated in fee vault
   - Tracked per transaction
   - Ready for distribution

4. Fee distribution (when triggered):
   - Calculate proportional distribution to token holders
   - Distribute fees based on token holdings
   - Emit distribution event

5. Owner management:
   - Only owner can update owner address
   - New owner receives full control
   - Changes persist on-chain
    """)


def example_environment_setup():
    """Example: Environment setup and configuration."""
    print("\n" + "=" * 60)
    print("Example 6: Environment Configuration")
    print("=" * 60)
    
    program_id = os.getenv("PROGRAM_ID")
    network = os.getenv("SOLANA_NETWORK")
    rpc_url = os.getenv("SOLANA_RPC_URL")
    
    print(f"\nCurrent Configuration:")
    print(f"  PROGRAM_ID: {program_id or 'Not set'}")
    print(f"  SOLANA_NETWORK: {network or 'Not set'}")
    print(f"  SOLANA_RPC_URL: {rpc_url or 'Not set'}")
    
    print(f"\nTo deploy and configure:")
    print(f"  python deploy.py ~/.config/solana/id.json testnet")
    
    print(f"\nTo run integration tests:")
    print(f"  pytest tests/integration_tests.py -v")


def main():
    """Run all examples."""
    print("\n" + "=" * 60)
    print("Fee Distribution Contract - Interaction Examples")
    print("=" * 60)
    
    try:
        example_basic_operations()
        example_fee_distribution()
        example_multiple_payments()
        example_account_management()
        example_transaction_flow()
        example_environment_setup()
        
        print("\n" + "=" * 60)
        print("✓ All examples completed successfully!")
        print("=" * 60)
        
    except Exception as e:
        print(f"\n✗ Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
