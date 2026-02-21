#!/usr/bin/env python3
"""
Full Contract Test Suite

This script performs a comprehensive test of all contract functionalities
using the ContractInteractor class. It tests:

1. Contract initialization
2. Supply snapshot updates
3. Rent space payments
4. Holder state initialization
5. Fee claiming
6. Owner updates

Usage:
    python contract_full_test.py --config config.json
    python contract_full_test.py --network localnet --program-id <ID> --keys keys.json
    
Configuration file format (JSON):
{
    "network": "localnet",
    "program_id": "CkM4AcKu7LSXikXaTvxwuTDBXmT2yrEZTvXbJ6Kdrox7",
    "governance_token_address": "9xDeTuFZiTd98A926MhgnTzHrSmNJjVB86uS595EEuxF",
    "keypairs": {
        "owner": [1,2,3,...],  # Solana keypair array
        "payer": [1,2,3,...],
        "recipient": [1,2,3,...],
        "holder": [1,2,3,...]
    }
}
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Dict, Any, Optional
from dataclasses import dataclass
from enum import Enum

from solders.keypair import Keypair
from solders.pubkey import Pubkey

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent))

from tests.contract_helper import (
    ContractInteractor,
    sol_to_lamports,
    lamports_to_sol,
)


class TestStatus(Enum):
    """Test status indicators."""
    PASS = "✓ PASS"
    FAIL = "✗ FAIL"
    SKIP = "⊘ SKIP"
    INFO = "ℹ INFO"


@dataclass
class TestResult:
    """Result of a single test."""
    test_name: str
    status: TestStatus
    message: str
    error: Optional[str] = None
    details: Optional[Dict[str, Any]] = None


class ContractFullTest:
    """Full test suite for the fee distribution contract."""
    
    # Test configuration
    PAYMENT_AMOUNT = sol_to_lamports(0.5)
    HOLDER_TOKEN_BALANCE = 1_000_000  # 1M tokens
    INITIAL_SUPPLY = 1_000_000_000_000  # 1T tokens
    
    def __init__(
        self,
        interactor: ContractInteractor,
        keypairs: Dict[str, Keypair],
        governance_token_mint: Pubkey,
    ):
        """
        Initialize test suite.
        
        Args:
            interactor: ContractInteractor instance
            keypairs: Dictionary mapping role names to Keypair objects
            governance_token_mint: Governance token mint address
        """
        self.interactor = interactor
        self.keypairs = keypairs
        self.governance_token_mint = governance_token_mint
        self.results: list[TestResult] = []
        
    def run_all_tests(self) -> int:
        """
        Run all tests and return exit code.
        
        Returns:
            0 if all tests passed, 1 otherwise
        """
        print("\n" + "="*70)
        print("SOLANA FEE DISTRIBUTION CONTRACT - FULL TEST SUITE")
        print("="*70)
        
        print("\n[TEST 1] Contract Initialization")
        print("-" * 70)
        self._test_initialize()
        
        print("\n[TEST 2] Get Contract State")
        print("-" * 70)
        self._test_get_contract_state()
        
        print("\n[TEST 3] Update Supply Snapshot")
        print("-" * 70)
        self._test_update_supply_snapshot()
        
        print("\n[TEST 4] Initialize Holder State")
        print("-" * 70)
        self._test_init_holder_state()
        
        print("\n[TEST 5] Get Holder State")
        print("-" * 70)
        self._test_get_holder_state()
        
        print("\n[TEST 6] Create Rent Space Instruction")
        print("-" * 70)
        self._test_create_rent_space_instruction()
        
        print("\n[TEST 7] Fee Calculation")
        print("-" * 70)
        self._test_fee_calculation()
        
        print("\n[TEST 8] Multiple Rent Space Payments")
        print("-" * 70)
        self._test_multiple_payments()
        
        print("\n[TEST 9] Create Claim Fees Instruction")
        print("-" * 70)
        self._test_create_claim_fees_instruction()
        
        print("\n[TEST 10] Update Owner")
        print("-" * 70)
        self._test_update_owner()
        
        print("\n[TEST 11] PDA Derivation")
        print("-" * 70)
        self._test_pda_derivation()
        
        print("\n[TEST 12] Claimable Fees Calculation")
        print("-" * 70)
        self._test_claimable_fees_calculation()
        
        # Print summary
        self._print_summary()
        
        # Return exit code based on results
        failed = sum(1 for r in self.results if r.status == TestStatus.FAIL)
        return 1 if failed > 0 else 0
    
    # ======================== Individual Tests ========================
    
    def _test_initialize(self):
        """Test contract initialization instruction creation."""
        try:
            owner = self.keypairs["owner"].pubkey()
            payer = self.keypairs["payer"].pubkey()
            
            instruction = self.interactor.create_initialize_instruction(
                owner=owner,
                governance_token_mint=self.governance_token_mint,
                payer=payer,
                supply_snapshot=self.INITIAL_SUPPLY
            )
            
            assert instruction is not None, "Instruction is None"
            assert instruction.program_id == self.interactor.program_id, "Wrong program ID"
            assert len(instruction.accounts) == 7, f"Expected 7 accounts, got {len(instruction.accounts)}"
            assert len(instruction.data) > 0, "Instruction data is empty"
            
            self._add_result(
                "Initialize Contract",
                TestStatus.PASS,
                "Successfully created initialize instruction",
                details={
                    "program_id": str(instruction.program_id),
                    "num_accounts": len(instruction.accounts),
                    "data_length": len(instruction.data),
                }
            )
        except Exception as e:
            self._add_result(
                "Initialize Contract",
                TestStatus.FAIL,
                f"Failed to create initialize instruction: {str(e)}",
                error=str(e)
            )
    
    def _test_get_contract_state(self):
        """Test getting contract state."""
        try:
            # This would fail if contract doesn't exist, which is expected in test
            # We're testing that the method can be called and returns proper structure
            contract_pda, bump = self.interactor.get_contract_pda()
            
            assert contract_pda is not None, "Contract PDA is None"
            assert isinstance(contract_pda, Pubkey), "Contract PDA should be Pubkey"
            assert bump > 0, "Bump should be positive"
            
            self._add_result(
                "Get Contract State (PDA)",
                TestStatus.PASS,
                "Successfully derived contract PDA",
                details={
                    "contract_pda": str(contract_pda),
                    "bump": bump,
                }
            )
        except Exception as e:
            self._add_result(
                "Get Contract State (PDA)",
                TestStatus.FAIL,
                f"Failed to get contract state: {str(e)}",
                error=str(e)
            )
    
    def _test_update_supply_snapshot(self):
        """Test update supply snapshot instruction creation."""
        try:
            owner = self.keypairs["owner"].pubkey()
            
            instruction = self.interactor.create_update_supply_snapshot_instruction(
                owner=owner,
                governance_token_mint=self.governance_token_mint
            )
            
            assert instruction is not None, "Instruction is None"
            assert len(instruction.accounts) == 3, f"Expected 3 accounts, got {len(instruction.accounts)}"
            assert len(instruction.data) > 0, "Instruction data is empty"
            
            self._add_result(
                "Update Supply Snapshot",
                TestStatus.PASS,
                "Successfully created update supply snapshot instruction",
                details={
                    "num_accounts": len(instruction.accounts),
                    "data_length": len(instruction.data),
                }
            )
        except Exception as e:
            self._add_result(
                "Update Supply Snapshot",
                TestStatus.FAIL,
                f"Failed to create update supply snapshot instruction: {str(e)}",
                error=str(e)
            )
    
    def _test_init_holder_state(self):
        """Test initialize holder state instruction creation."""
        try:
            holder = self.keypairs["holder"].pubkey()
            
            instruction = self.interactor.create_init_holder_state_instruction(holder=holder)
            
            assert instruction is not None, "Instruction is None"
            assert len(instruction.accounts) == 4, f"Expected 4 accounts, got {len(instruction.accounts)}"
            assert len(instruction.data) > 0, "Instruction data is empty"
            
            self._add_result(
                "Initialize Holder State",
                TestStatus.PASS,
                "Successfully created init holder state instruction",
                details={
                    "holder": str(holder),
                    "num_accounts": len(instruction.accounts),
                }
            )
        except Exception as e:
            self._add_result(
                "Initialize Holder State",
                TestStatus.FAIL,
                f"Failed to create init holder state instruction: {str(e)}",
                error=str(e)
            )
    
    def _test_get_holder_state(self):
        """Test getting holder state PDA."""
        try:
            holder = self.keypairs["holder"].pubkey()
            holder_state_pda, bump = self.interactor.get_holder_state_pda(holder)
            
            assert holder_state_pda is not None, "Holder state PDA is None"
            assert isinstance(holder_state_pda, Pubkey), "Holder state PDA should be Pubkey"
            assert bump > 0, "Bump should be positive"
            
            self._add_result(
                "Get Holder State (PDA)",
                TestStatus.PASS,
                "Successfully derived holder state PDA",
                details={
                    "holder": str(holder),
                    "holder_state_pda": str(holder_state_pda),
                    "bump": bump,
                }
            )
        except Exception as e:
            self._add_result(
                "Get Holder State (PDA)",
                TestStatus.FAIL,
                f"Failed to get holder state: {str(e)}",
                error=str(e)
            )
    
    def _test_create_rent_space_instruction(self):
        """Test rent space instruction creation."""
        try:
            payer = self.keypairs["payer"].pubkey()
            recipient = self.keypairs["recipient"].pubkey()
            payer_token_account = Keypair().pubkey()
            recipient_token_account = Keypair().pubkey()
            
            instruction = self.interactor.create_rent_space_instruction(
                recipient=recipient,
                expiration_time=9999999999,
                payment_amount=self.PAYMENT_AMOUNT,
                payer=payer,
                payer_token_account=payer_token_account,
                recipient_token_account=recipient_token_account,
                governance_token_mint=self.governance_token_mint,
                fee_record_count=0
            )
            
            assert instruction is not None, "Instruction is None"
            assert len(instruction.accounts) == 9, f"Expected 9 accounts, got {len(instruction.accounts)}"
            assert len(instruction.data) > 0, "Instruction data is empty"
            
            self._add_result(
                "Create Rent Space Instruction",
                TestStatus.PASS,
                "Successfully created rent space instruction",
                details={
                    "payment_amount": lamports_to_sol(self.PAYMENT_AMOUNT),
                    "num_accounts": len(instruction.accounts),
                    "data_length": len(instruction.data),
                }
            )
        except Exception as e:
            self._add_result(
                "Create Rent Space Instruction",
                TestStatus.FAIL,
                f"Failed to create rent space instruction: {str(e)}",
                error=str(e)
            )
    
    def _test_fee_calculation(self):
        """Test fee calculation method."""
        try:
            # Test with various amounts
            test_amounts = [
                self.PAYMENT_AMOUNT,
                sol_to_lamports(1.0),
                sol_to_lamports(10.0),
            ]
            
            for amount in test_amounts:
                fee, recipient = self.interactor.calculate_fee(amount)
                
                # Verify 20/80 split
                expected_fee = (amount * 20) // 100
                expected_recipient = amount - expected_fee
                
                assert fee == expected_fee, f"Fee mismatch: {fee} != {expected_fee}"
                assert recipient == expected_recipient, f"Recipient mismatch: {recipient} != {expected_recipient}"
                assert fee + recipient == amount, "Fee + recipient != total amount"
            
            fee, recipient = self.interactor.calculate_fee(self.PAYMENT_AMOUNT)
            self._add_result(
                "Fee Calculation",
                TestStatus.PASS,
                "Successfully calculated 20/80 fee split",
                details={
                    "total_amount": lamports_to_sol(self.PAYMENT_AMOUNT),
                    "fee_20_percent": lamports_to_sol(fee),
                    "recipient_80_percent": lamports_to_sol(recipient),
                }
            )
        except Exception as e:
            self._add_result(
                "Fee Calculation",
                TestStatus.FAIL,
                f"Fee calculation failed: {str(e)}",
                error=str(e)
            )
    
    def _test_multiple_payments(self):
        """Test creating multiple payment instructions."""
        try:
            payer = self.keypairs["payer"].pubkey()
            recipient = self.keypairs["recipient"].pubkey()
            payer_token_account = Keypair().pubkey()
            recipient_token_account = Keypair().pubkey()
            
            num_payments = 5
            instructions = []
            
            for i in range(num_payments):
                instruction = self.interactor.create_rent_space_instruction(
                    recipient=recipient,
                    expiration_time=9999999999,
                    payment_amount=self.PAYMENT_AMOUNT,
                    payer=payer,
                    payer_token_account=payer_token_account,
                    recipient_token_account=recipient_token_account,
                    governance_token_mint=self.governance_token_mint,
                    fee_record_count=i
                )
                instructions.append(instruction)
            
            assert len(instructions) == num_payments, f"Expected {num_payments} instructions"
            
            total_amount = lamports_to_sol(self.PAYMENT_AMOUNT * num_payments)
            self._add_result(
                "Multiple Payments",
                TestStatus.PASS,
                f"Successfully created {num_payments} payment instructions",
                details={
                    "num_payments": num_payments,
                    "amount_per_payment": lamports_to_sol(self.PAYMENT_AMOUNT),
                    "total_amount": total_amount,
                }
            )
        except Exception as e:
            self._add_result(
                "Multiple Payments",
                TestStatus.FAIL,
                f"Failed to create multiple payments: {str(e)}",
                error=str(e)
            )
    
    def _test_create_claim_fees_instruction(self):
        """Test claim fees instruction creation."""
        try:
            holder = self.keypairs["holder"].pubkey()
            holder_token_account = Keypair().pubkey()
            
            instruction = self.interactor.create_claim_fees_instruction(
                holder=holder,
                holder_token_account=holder_token_account,
                governance_token_mint=self.governance_token_mint
            )
            
            assert instruction is not None, "Instruction is None"
            assert len(instruction.accounts) == 6, f"Expected 6 accounts, got {len(instruction.accounts)}"
            assert len(instruction.data) > 0, "Instruction data is empty"
            
            self._add_result(
                "Create Claim Fees Instruction",
                TestStatus.PASS,
                "Successfully created claim fees instruction",
                details={
                    "holder": str(holder),
                    "num_accounts": len(instruction.accounts),
                }
            )
        except Exception as e:
            self._add_result(
                "Create Claim Fees Instruction",
                TestStatus.FAIL,
                f"Failed to create claim fees instruction: {str(e)}",
                error=str(e)
            )
    
    def _test_update_owner(self):
        """Test update owner instruction creation."""
        try:
            current_owner = self.keypairs["owner"].pubkey()
            new_owner = Keypair().pubkey()
            
            instruction = self.interactor.create_update_owner_instruction(
                new_owner=new_owner,
                current_owner=current_owner
            )
            
            assert instruction is not None, "Instruction is None"
            assert len(instruction.accounts) == 2, f"Expected 2 accounts, got {len(instruction.accounts)}"
            assert len(instruction.data) > 0, "Instruction data is empty"
            
            self._add_result(
                "Update Owner",
                TestStatus.PASS,
                "Successfully created update owner instruction",
                details={
                    "current_owner": str(current_owner),
                    "new_owner": str(new_owner),
                    "num_accounts": len(instruction.accounts),
                }
            )
        except Exception as e:
            self._add_result(
                "Update Owner",
                TestStatus.FAIL,
                f"Failed to create update owner instruction: {str(e)}",
                error=str(e)
            )
    
    def _test_pda_derivation(self):
        """Test all PDA derivations."""
        try:
            # Test contract PDA
            contract_pda, contract_bump = self.interactor.get_contract_pda()
            assert contract_pda is not None, "Contract PDA is None"
            
            # Test fee vault PDA
            fee_vault_pda, fee_vault_bump = self.interactor.get_fee_vault_pda(contract_pda)
            assert fee_vault_pda is not None, "Fee vault PDA is None"
            
            # Test holder state PDA
            holder = self.keypairs["holder"].pubkey()
            holder_state_pda, holder_bump = self.interactor.get_holder_state_pda(holder)
            assert holder_state_pda is not None, "Holder state PDA is None"
            
            # Test fee record PDA
            payer = self.keypairs["payer"].pubkey()
            fee_record_pda, fee_record_bump = self.interactor.get_fee_record_pda(payer, 0)
            assert fee_record_pda is not None, "Fee record PDA is None"
            
            # Verify PDAs are different
            pdas = [contract_pda, fee_vault_pda, holder_state_pda, fee_record_pda]
            assert len(set(str(p) for p in pdas)) == 4, "PDAs should be unique"
            
            self._add_result(
                "PDA Derivation",
                TestStatus.PASS,
                "Successfully derived all PDAs",
                details={
                    "contract_pda": str(contract_pda),
                    "fee_vault_pda": str(fee_vault_pda),
                    "holder_state_pda": str(holder_state_pda),
                    "fee_record_pda": str(fee_record_pda),
                }
            )
        except Exception as e:
            self._add_result(
                "PDA Derivation",
                TestStatus.FAIL,
                f"PDA derivation failed: {str(e)}",
                error=str(e)
            )
    
    def _test_claimable_fees_calculation(self):
        """Test claimable fees calculation."""
        try:
            token_balance = self.HOLDER_TOKEN_BALANCE
            fees_per_token_accumulated = 500_000_000_000  # 500 fees per token
            fees_per_token_claimed = 100_000_000_000      # 100 fees per token already claimed
            
            claimable = self.interactor.calculate_claimable_fees(
                token_balance=token_balance,
                fees_per_token_accumulated=fees_per_token_accumulated,
                fees_per_token_claimed=fees_per_token_claimed
            )
            
            # Verify calculation
            expected_claimable = (
                token_balance * 
                (fees_per_token_accumulated - fees_per_token_claimed)
            ) // self.interactor.PRECISION
            
            assert claimable == expected_claimable, f"Claimable mismatch: {claimable} != {expected_claimable}"
            assert claimable > 0, "Claimable should be positive"
            
            self._add_result(
                "Claimable Fees Calculation",
                TestStatus.PASS,
                "Successfully calculated claimable fees",
                details={
                    "token_balance": token_balance,
                    "fees_per_token_accumulated": fees_per_token_accumulated,
                    "fees_per_token_claimed": fees_per_token_claimed,
                    "claimable_fees": claimable,
                }
            )
        except Exception as e:
            self._add_result(
                "Claimable Fees Calculation",
                TestStatus.FAIL,
                f"Claimable fees calculation failed: {str(e)}",
                error=str(e)
            )
    
    # ======================== Utility Methods ========================
    
    def _add_result(
        self,
        test_name: str,
        status: TestStatus,
        message: str,
        error: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None
    ):
        """Add test result and print it."""
        result = TestResult(
            test_name=test_name,
            status=status,
            message=message,
            error=error,
            details=details
        )
        self.results.append(result)
        
        # Print result
        print(f"{status.value}: {test_name}")
        print(f"    {message}")
        
        if details:
            for key, value in details.items():
                print(f"    • {key}: {value}")
        
        if error:
            print(f"    Error: {error}")
    
    def _print_summary(self):
        """Print test summary."""
        print("\n" + "="*70)
        print("TEST SUMMARY")
        print("="*70)
        
        passed = sum(1 for r in self.results if r.status == TestStatus.PASS)
        failed = sum(1 for r in self.results if r.status == TestStatus.FAIL)
        skipped = sum(1 for r in self.results if r.status == TestStatus.SKIP)
        total = len(self.results)
        
        print(f"\nTotal Tests: {total}")
        print(f"  ✓ Passed:  {passed}")
        print(f"  ✗ Failed:  {failed}")
        print(f"  ⊘ Skipped: {skipped}")
        
        if failed > 0:
            print("\nFailed Tests:")
            for result in self.results:
                if result.status == TestStatus.FAIL:
                    print(f"  • {result.test_name}: {result.message}")
        
        success_rate = (passed / total * 100) if total > 0 else 0
        print(f"\nSuccess Rate: {success_rate:.1f}%")
        print("="*70 + "\n")


def load_config(config_path: str) -> Dict[str, Any]:
    """Load configuration from JSON file."""
    with open(config_path, "r") as f:
        return json.load(f)


def load_keypairs(keypair_data: Dict[str, list]) -> Dict[str, Keypair]:
    """Load keypairs from configuration."""
    keypairs = {}
    for role, key_array in keypair_data.items():
        keypairs[role] = Keypair.from_bytes(bytes(key_array))
    return keypairs


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Full test suite for Solana fee distribution contract"
    )
    parser.add_argument(
        "--config",
        type=str,
        help="Path to configuration JSON file"
    )
    parser.add_argument(
        "--network",
        type=str,
        default="localnet",
        help="Solana network (devnet, testnet, mainnet-beta, localnet)"
    )
    parser.add_argument(
        "--program-id",
        type=str,
        help="Contract program ID"
    )
    parser.add_argument(
        "--token-address",
        type=str,
        help="Governance token mint address"
    )
    parser.add_argument(
        "--keys",
        type=str,
        help="Path to test keypairs JSON file"
    )
    
    args = parser.parse_args()
    
    try:
        # Load configuration
        if args.config:
            config = load_config(args.config)
            network = config.get("network", "localnet")
            program_id = config.get("program_id")
            token_address = config.get("governance_token_address")
            keypair_data = config.get("keypairs", {})
        else:
            network = args.network
            program_id = args.program_id
            token_address = args.token_address
            
            # Load keypairs from file if provided
            keypair_data = {}
            if args.keys:
                with open(args.keys, "r") as f:
                    keypair_data = json.load(f)
            else:
                # Generate test keypairs
                print("Warning: Using generated test keypairs")
                keypair_data = {
                    "owner": list(bytes(Keypair())),
                    "payer": list(bytes(Keypair())),
                    "recipient": list(bytes(Keypair())),
                    "holder": list(bytes(Keypair())),
                }
        
        # Validate required parameters
        if not program_id:
            print("Error: program_id is required", file=sys.stderr)
            return 1
        
        if not token_address:
            print("Error: governance_token_address is required", file=sys.stderr)
            return 1
        
        # Create interactor and keypairs
        interactor = ContractInteractor(
            program_id=program_id,
            network=network
        )
        
        keypairs = load_keypairs(keypair_data)
        
        # Verify we have all required keypairs
        required_roles = ["owner", "payer", "recipient", "holder"]
        for role in required_roles:
            if role not in keypairs:
                print(f"Error: Missing '{role}' keypair", file=sys.stderr)
                return 1
        
        governance_token_mint = Pubkey.from_string(token_address)
        
        # Run tests
        tester = ContractFullTest(
            interactor=interactor,
            keypairs=keypairs,
            governance_token_mint=governance_token_mint
        )
        
        return tester.run_all_tests()
        
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
