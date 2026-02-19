"""
Contract Interaction Helper Module

Provides utilities for interacting with the Fee Distribution contract on Solana.
"""

import os
from typing import Optional, Dict, Any, List, Tuple
from dataclasses import dataclass
from enum import Enum

from solders.pubkey import Pubkey
from solders.keypair import Keypair
from solders.instruction import Instruction
from solana.rpc.api import Client
from solana.rpc.commitment import Confirmed
from solana.transaction import Transaction


class NetworkType(str, Enum):
    """Supported Solana networks."""
    DEVNET = "devnet"
    TESTNET = "testnet"
    MAINNET = "mainnet-beta"


@dataclass
class ContractAccount:
    """Represents a contract-related account."""
    address: Pubkey
    balance: int
    is_signer: bool = False
    is_writable: bool = False
    lamports: int = 0
    owner: Optional[Pubkey] = None
    data: bytes = b""


@dataclass
class TransactionResult:
    """Result of a contract transaction."""
    signature: str
    confirmed: bool
    error: Optional[str] = None
    logs: List[str] = None
    
    def __post_init__(self):
        if self.logs is None:
            self.logs = []


class ContractInteractor:
    """Helper class for interacting with the Fee Distribution contract."""
    
    def __init__(
        self,
        program_id: str = None,
        network: str = "testnet",
        keypair_path: str = None
    ):
        """
        Initialize contract interactor.
        
        Args:
            program_id: Deployed program ID (uses PROGRAM_ID env var if not provided)
            network: Solana network (devnet, testnet, mainnet-beta)
            keypair_path: Path to keypair JSON file (uses default Solana config if not provided)
        """
        self.program_id = Pubkey.from_string(
            program_id or os.getenv("PROGRAM_ID", "")
        )
        self.network = network
        self.rpc_url = self._get_rpc_url(network)
        self.client = Client(self.rpc_url, commitment=Confirmed)
        self.payer = self._load_keypair(keypair_path)
    
    def _get_rpc_url(self, network: str) -> str:
        """Get RPC URL for network."""
        rpc_urls = {
            NetworkType.DEVNET.value: "https://api.devnet.solana.com",
            NetworkType.TESTNET.value: "https://api.testnet.solana.com",
            NetworkType.MAINNET.value: "https://api.mainnet-beta.solana.com",
        }
        return os.getenv("SOLANA_RPC_URL", rpc_urls.get(network, ""))
    
    def _load_keypair(self, keypair_path: Optional[str]) -> Optional[Keypair]:
        """Load keypair from file or use default."""
        if keypair_path:
            try:
                keypair = Keypair.from_secret_key_file(keypair_path)
                return keypair
            except Exception as e:
                raise RuntimeError(f"Failed to load keypair: {e}")
        return None
    
    def get_balance(self, pubkey: Pubkey) -> int:
        """Get account balance in lamports."""
        try:
            response = self.client.get_balance(pubkey)
            return response.value
        except Exception as e:
            raise RuntimeError(f"Failed to get balance: {e}")
    
    def get_account_info(self, pubkey: Pubkey) -> Optional[ContractAccount]:
        """Get account information."""
        try:
            response = self.client.get_account_info(pubkey)
            if response.value is None:
                return None
            
            account_info = response.value
            return ContractAccount(
                address=pubkey,
                balance=account_info.lamports,
                lamports=account_info.lamports,
                owner=account_info.owner,
                data=bytes(account_info.data),
            )
        except Exception as e:
            raise RuntimeError(f"Failed to get account info: {e}")
    
    def airdrop(self, pubkey: Pubkey, amount_sol: float) -> str:
        """Request airdrop of SOL (testnet/devnet only)."""
        try:
            amount_lamports = int(amount_sol * 1e9)
            response = self.client.request_airdrop(pubkey, amount_lamports)
            return response.value
        except Exception as e:
            raise RuntimeError(f"Airdrop failed: {e}")
    
    def wait_for_confirmation(
        self,
        signature: str,
        max_retries: int = 30,
        timeout_seconds: int = 1
    ) -> bool:
        """Wait for transaction confirmation."""
        import time
        
        for i in range(max_retries):
            try:
                response = self.client.get_signature_statuses([signature])
                if response.value[0] is not None:
                    status = response.value[0]
                    if status.is_confirmed:
                        return True
            except Exception:
                pass
            time.sleep(timeout_seconds)
        
        return False
    
    def get_transaction_logs(self, signature: str) -> List[str]:
        """Get transaction logs."""
        try:
            response = self.client.get_transaction(signature, encoding="json")
            if response.value and response.value.transaction.meta:
                return response.value.transaction.meta.log_messages or []
            return []
        except Exception as e:
            raise RuntimeError(f"Failed to get transaction logs: {e}")
    
    def create_initialize_instruction(
        self,
        owner: Pubkey,
        governance_token_mint: Pubkey,
        payer: Pubkey
    ) -> Instruction:
        """Create instruction for contract initialization."""
        # Placeholder - actual implementation would encode the instruction
        # based on the contract's IDL
        raise NotImplementedError(
            "Use Anchor client or your own instruction encoder"
        )
    
    def create_rent_space_instruction(
        self,
        recipient: Pubkey,
        expiration_time: int,
        payment_amount: int,
        payer: Pubkey,
        payer_token_account: Pubkey,
        recipient_token_account: Pubkey
    ) -> Instruction:
        """Create instruction for rent_space function."""
        raise NotImplementedError(
            "Use Anchor client or your own instruction encoder"
        )
    
    def create_distribute_fees_instruction(
        self,
        fee_amount: int,
        payer: Pubkey,
        payer_token_account: Pubkey,
        fee_vault: Pubkey
    ) -> Instruction:
        """Create instruction for fee distribution."""
        raise NotImplementedError(
            "Use Anchor client or your own instruction encoder"
        )
    
    def create_update_owner_instruction(
        self,
        new_owner: Pubkey,
        current_owner: Pubkey
    ) -> Instruction:
        """Create instruction for updating owner."""
        raise NotImplementedError(
            "Use Anchor client or your own instruction encoder"
        )
    
    def calculate_fee(self, amount: int) -> Tuple[int, int]:
        """
        Calculate fee and recipient amount.
        
        Args:
            amount: Total payment amount in lamports
            
        Returns:
            Tuple of (fee_amount, recipient_amount)
        """
        fee = (amount * 20) // 100
        recipient = amount - fee
        return fee, recipient
    
    def parse_rent_space_event(self, logs: List[str]) -> Optional[Dict[str, Any]]:
        """Parse RentSpaceEvent from transaction logs."""
        # Placeholder - actual implementation would parse Anchor program logs
        for log in logs:
            if "RentSpaceEvent" in log:
                return {"raw_log": log}
        return None
    
    def parse_distribution_event(self, logs: List[str]) -> Optional[Dict[str, Any]]:
        """Parse DistributionEvent from transaction logs."""
        # Placeholder - actual implementation would parse Anchor program logs
        for log in logs:
            if "DistributionEvent" in log:
                return {"raw_log": log}
        return None


class MockContractInteractor(ContractInteractor):
    """Mock contract interactor for testing without network calls."""
    
    def __init__(self):
        """Initialize mock interactor."""
        self.program_id = Pubkey.from_string("11111111111111111111111111111111")
        self.network = "testnet"
        self.rpc_url = "mock://testnet"
        self.payer = None
        self.accounts: Dict[str, ContractAccount] = {}
        self.transactions: List[TransactionResult] = []
    
    def get_balance(self, pubkey: Pubkey) -> int:
        """Get mock account balance."""
        account = self.accounts.get(str(pubkey))
        return account.lamports if account else 0
    
    def get_account_info(self, pubkey: Pubkey) -> Optional[ContractAccount]:
        """Get mock account info."""
        return self.accounts.get(str(pubkey))
    
    def airdrop(self, pubkey: Pubkey, amount_sol: float) -> str:
        """Mock airdrop."""
        amount_lamports = int(amount_sol * 1e9)
        account = ContractAccount(
            address=pubkey,
            balance=amount_lamports,
            lamports=amount_lamports
        )
        self.accounts[str(pubkey)] = account
        return "mock_signature"
    
    def wait_for_confirmation(
        self,
        signature: str,
        max_retries: int = 1,
        timeout_seconds: int = 0
    ) -> bool:
        """Mock confirmation wait."""
        return True
    
    def calculate_fee(self, amount: int) -> Tuple[int, int]:
        """Calculate fee for amount."""
        return super().calculate_fee(amount)


# Utility functions

def validate_pubkey(pubkey_str: str) -> Pubkey:
    """Validate and return Pubkey."""
    try:
        return Pubkey.from_string(pubkey_str)
    except Exception as e:
        raise ValueError(f"Invalid Pubkey: {pubkey_str} - {e}")


def lamports_to_sol(lamports: int) -> float:
    """Convert lamports to SOL."""
    return lamports / 1e9


def sol_to_lamports(sol: float) -> int:
    """Convert SOL to lamports."""
    return int(sol * 1e9)
