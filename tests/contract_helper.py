"""
Contract Interaction Helper Module

Provides utilities for interacting with the Fee Distribution contract on Solana.
"""

import os
import struct
from typing import Optional, Dict, Any, List, Tuple
from dataclasses import dataclass
from enum import Enum

from solders.pubkey import Pubkey
from solders.keypair import Keypair
from solders.instruction import Instruction, AccountMeta
from solders.sysvar import RENT, CLOCK
from solana.rpc.api import Client
from solana.rpc.commitment import Confirmed

try:
    from construct import Struct, Bytes, Int8ul, Int64sl, Int64ul, Int128ul
    HAS_CONSTRUCT = True
except ImportError:
    HAS_CONSTRUCT = False


class NetworkType(str, Enum):
    """Supported Solana networks."""
    DEVNET = "devnet"
    TESTNET = "testnet"
    MAINNET = "mainnet-beta"
    LOCALNET = "localnet"


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
    
    # Anchor instruction discriminators (first 8 bytes of sighash)
    DISCRIMINATOR_INITIALIZE = b'\xaf\xaf\x6c\xa7\xb8\x49\xb0\x1e'
    DISCRIMINATOR_UPDATE_SUPPLY = b'\x5d\xf3\xec\x16\x06\x8f\x69\xe1'
    DISCRIMINATOR_RENT_SPACE = b'\x5a\x79\x67\x3e\x7a\x93\x51\xab'
    DISCRIMINATOR_INIT_HOLDER = b'\x2b\xd5\x8e\x8a\xdc\xb1\x2c\xca'
    DISCRIMINATOR_CLAIM_FEES = b'\xf5\x5c\x82\xc0\xef\xba\x8b\x77'
    DISCRIMINATOR_UPDATE_OWNER = b'\x9e\xa4\xfd\x75\x90\x7a\xdf\xf8'
    
    # Constants
    PRECISION = 1_000_000_000_000
    TOKEN_PROGRAM = "TokenkegQfeZyiNwAJsyFbPVwwj8tAd5kKamrHTjmJ5"
    SYSTEM_PROGRAM = "11111111111111111111111111111111"
    
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
            program_id or os.getenv("PROGRAM_ID", "CkM4AcKu7LSXikXaTvxwuTDBXmT2yrEZTvXbJ6Kdrox7")
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
            NetworkType.LOCALNET.value: "http://127.0.0.1:8899"
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
    
    # ======================== PDA Derivation ========================
    
    def get_contract_pda(self) -> Tuple[Pubkey, int]:
        """Derive contract state PDA.
        
        Returns:
            Tuple of (contract_pubkey, bump)
        """
        return Pubkey.find_program_address(
            seeds=[b"contract"],
            program_id=self.program_id
        )
    
    def get_fee_vault_pda(self, contract: Pubkey) -> Tuple[Pubkey, int]:
        """Derive fee vault token account PDA.
        
        Args:
            contract: Contract state PDA address
            
        Returns:
            Tuple of (fee_vault_pubkey, bump)
        """
        return Pubkey.find_program_address(
            seeds=[b"fee_vault", bytes(contract)],
            program_id=self.program_id
        )
    
    def get_fee_record_pda(
        self,
        payer: Pubkey,
        fee_record_count: int
    ) -> Tuple[Pubkey, int]:
        """Derive fee record PDA.
        
        Args:
            payer: Payer pubkey
            fee_record_count: Current fee record count
            
        Returns:
            Tuple of (fee_record_pubkey, bump)
        """
        return Pubkey.find_program_address(
            seeds=[
                b"fee_record",
                bytes(payer),
                fee_record_count.to_bytes(8, byteorder='little')
            ],
            program_id=self.program_id
        )
    
    def get_holder_state_pda(self, holder: Pubkey) -> Tuple[Pubkey, int]:
        """Derive holder state PDA.
        
        Args:
            holder: Holder pubkey
            
        Returns:
            Tuple of (holder_state_pubkey, bump)
        """
        return Pubkey.find_program_address(
            seeds=[b"holder_state", bytes(holder)],
            program_id=self.program_id
        )
    
    # ======================== Instruction Creation ========================
    
    def create_initialize_instruction(
        self,
        owner: Pubkey,
        governance_token_mint: Pubkey,
        payer: Pubkey,
        supply_snapshot: int = 1_000_000_000_000_000
    ) -> Instruction:
        """
        Create instruction for contract initialization.
        
        Args:
            owner: Owner pubkey
            governance_token_mint: Governance token mint
            payer: Transaction payer
            supply_snapshot: Supply snapshot at initialization time
        
        Returns:
            Instruction for initialize
        """
        contract_pubkey, _ = self.get_contract_pda()
        fee_vault_pubkey, _ = self.get_fee_vault_pda(contract_pubkey)
        
        # Encode instruction data: discriminator + owner + supply_snapshot
        instruction_data = self.DISCRIMINATOR_INITIALIZE
        instruction_data += bytes(owner)
        instruction_data += supply_snapshot.to_bytes(8, byteorder='little')
        
        # Build account metas in order
        accounts = [
            AccountMeta(pubkey=contract_pubkey, is_signer=False, is_writable=True),
            AccountMeta(pubkey=governance_token_mint, is_signer=False, is_writable=False),
            AccountMeta(pubkey=fee_vault_pubkey, is_signer=False, is_writable=True),
            AccountMeta(pubkey=payer, is_signer=True, is_writable=True),
            AccountMeta(pubkey=Pubkey.from_string(self.TOKEN_PROGRAM), is_signer=False, is_writable=False),
            AccountMeta(pubkey=Pubkey.from_string(self.SYSTEM_PROGRAM), is_signer=False, is_writable=False),
            AccountMeta(pubkey=RENT, is_signer=False, is_writable=False),
        ]
        
        return Instruction(
            program_id=self.program_id,
            accounts=accounts,
            data=instruction_data
        )
    
    def create_update_supply_snapshot_instruction(
        self,
        owner: Pubkey,
        governance_token_mint: Pubkey
    ) -> Instruction:
        """
        Create instruction to update supply snapshot (owner only).
        
        Args:
            owner: Owner signer pubkey
            governance_token_mint: Governance token mint
        
        Returns:
            Instruction for update_supply_snapshot
        """
        contract_pubkey, _ = self.get_contract_pda()
        
        instruction_data = self.DISCRIMINATOR_UPDATE_SUPPLY
        
        accounts = [
            AccountMeta(pubkey=contract_pubkey, is_signer=False, is_writable=True),
            AccountMeta(pubkey=governance_token_mint, is_signer=False, is_writable=False),
            AccountMeta(pubkey=owner, is_signer=True, is_writable=False),
        ]
        
        return Instruction(
            program_id=self.program_id,
            accounts=accounts,
            data=instruction_data
        )
    
    def create_rent_space_instruction(
        self,
        recipient: Pubkey,
        expiration_time: int,
        payment_amount: int,
        payer: Pubkey,
        payer_token_account: Pubkey,
        recipient_token_account: Pubkey,
        governance_token_mint: Pubkey,
        fee_record_count: int = 0
    ) -> Instruction:
        """
        Create instruction for rent_space function.
        
        Args:
            recipient: Recipient pubkey
            expiration_time: Unix timestamp for payment expiration
            payment_amount: Amount to transfer in token base units
            payer: Payer signer pubkey
            payer_token_account: Payer's token account
            recipient_token_account: Recipient's token account
            governance_token_mint: Governance token mint
            fee_record_count: Current fee record count (for seed derivation)
        
        Returns:
            Instruction for rent_space
        """
        contract_pubkey, _ = self.get_contract_pda()
        fee_vault_pubkey, _ = self.get_fee_vault_pda(contract_pubkey)
        fee_record_pubkey, _ = self.get_fee_record_pda(payer, fee_record_count)
        
        # Encode instruction data: discriminator + recipient + expiration_time + payment_amount
        instruction_data = self.DISCRIMINATOR_RENT_SPACE
        instruction_data += bytes(recipient)
        instruction_data += expiration_time.to_bytes(8, byteorder='little', signed=True)
        instruction_data += payment_amount.to_bytes(8, byteorder='little')
        
        accounts = [
            AccountMeta(pubkey=contract_pubkey, is_signer=False, is_writable=True),
            AccountMeta(pubkey=governance_token_mint, is_signer=False, is_writable=False),
            AccountMeta(pubkey=payer, is_signer=True, is_writable=True),
            AccountMeta(pubkey=payer_token_account, is_signer=False, is_writable=True),
            AccountMeta(pubkey=recipient_token_account, is_signer=False, is_writable=True),
            AccountMeta(pubkey=fee_vault_pubkey, is_signer=False, is_writable=True),
            AccountMeta(pubkey=fee_record_pubkey, is_signer=False, is_writable=True),
            AccountMeta(pubkey=Pubkey.from_string(self.TOKEN_PROGRAM), is_signer=False, is_writable=False),
            AccountMeta(pubkey=Pubkey.from_string(self.SYSTEM_PROGRAM), is_signer=False, is_writable=False),
        ]
        
        return Instruction(
            program_id=self.program_id,
            accounts=accounts,
            data=instruction_data
        )
    
    def create_init_holder_state_instruction(
        self,
        holder: Pubkey
    ) -> Instruction:
        """
        Create instruction to initialize holder state.
        
        Must be called once per holder before they can claim fees.
        
        Args:
            holder: Holder signer pubkey
        
        Returns:
            Instruction for init_holder_state
        """
        contract_pubkey, _ = self.get_contract_pda()
        holder_state_pubkey, _ = self.get_holder_state_pda(holder)
        
        instruction_data = self.DISCRIMINATOR_INIT_HOLDER
        
        accounts = [
            AccountMeta(pubkey=contract_pubkey, is_signer=False, is_writable=False),
            AccountMeta(pubkey=holder_state_pubkey, is_signer=False, is_writable=True),
            AccountMeta(pubkey=holder, is_signer=True, is_writable=True),
            AccountMeta(pubkey=Pubkey.from_string(self.SYSTEM_PROGRAM), is_signer=False, is_writable=False),
        ]
        
        return Instruction(
            program_id=self.program_id,
            accounts=accounts,
            data=instruction_data
        )
    
    def create_claim_fees_instruction(
        self,
        holder: Pubkey,
        holder_token_account: Pubkey,
        governance_token_mint: Pubkey
    ) -> Instruction:
        """
        Create instruction to claim accumulated fees.
        
        Args:
            holder: Holder signer pubkey
            holder_token_account: Holder's token account to receive fees
            governance_token_mint: Governance token mint
        
        Returns:
            Instruction for claim_fees
        """
        contract_pubkey, _ = self.get_contract_pda()
        fee_vault_pubkey, _ = self.get_fee_vault_pda(contract_pubkey)
        holder_state_pubkey, _ = self.get_holder_state_pda(holder)
        
        instruction_data = self.DISCRIMINATOR_CLAIM_FEES
        
        accounts = [
            AccountMeta(pubkey=contract_pubkey, is_signer=False, is_writable=False),
            AccountMeta(pubkey=fee_vault_pubkey, is_signer=False, is_writable=True),
            AccountMeta(pubkey=holder_state_pubkey, is_signer=False, is_writable=True),
            AccountMeta(pubkey=holder_token_account, is_signer=False, is_writable=True),
            AccountMeta(pubkey=holder, is_signer=True, is_writable=False),
            AccountMeta(pubkey=Pubkey.from_string(self.TOKEN_PROGRAM), is_signer=False, is_writable=False),
        ]
        
        return Instruction(
            program_id=self.program_id,
            accounts=accounts,
            data=instruction_data
        )
    
    def create_update_owner_instruction(
        self,
        new_owner: Pubkey,
        current_owner: Pubkey
    ) -> Instruction:
        """
        Create instruction to update contract owner (owner only).
        
        Args:
            new_owner: New owner pubkey
            current_owner: Current owner signer pubkey
        
        Returns:
            Instruction for update_owner
        """
        contract_pubkey, _ = self.get_contract_pda()
        
        instruction_data = self.DISCRIMINATOR_UPDATE_OWNER
        instruction_data += bytes(new_owner)
        
        accounts = [
            AccountMeta(pubkey=contract_pubkey, is_signer=False, is_writable=True),
            AccountMeta(pubkey=current_owner, is_signer=True, is_writable=False),
        ]
        
        return Instruction(
            program_id=self.program_id,
            accounts=accounts,
            data=instruction_data
        )
    
    # ======================== Account Queries ========================
    
    def get_contract_state(self) -> Optional[Dict[str, Any]]:
        """
        Get current contract state from chain.
        
        Returns:
            Dictionary containing contract state fields or None if not found
        """
        try:
            contract_pubkey, _ = self.get_contract_pda()
            account = self.get_account_info(contract_pubkey)
            
            if not account or len(account.data) < 114:
                return None
            
            # Parse contract state: skip 8-byte discriminator
            data = account.data[8:]
            
            result = {
                'address': str(contract_pubkey),
                'owner': str(Pubkey(data[0:32])),
                'governance_token_mint': Pubkey(data[32:64]),
                'bump': data[64],
                'fee_vault_bump': data[65],
                'fees_per_token_accumulated': int.from_bytes(data[66:82], byteorder='little'),
                'total_fees_accumulated': int.from_bytes(data[82:90], byteorder='little'),
                'fee_record_count': int.from_bytes(data[90:98], byteorder='little'),
                'supply_snapshot': int.from_bytes(data[98:106], byteorder='little'),
            }
            return result
        except Exception as e:
            raise RuntimeError(f"Failed to get contract state: {e}")
    
    def get_holder_state(self, holder: Pubkey) -> Optional[Dict[str, Any]]:
        """
        Get holder state from chain.
        
        Args:
            holder: Holder pubkey
            
        Returns:
            Dictionary containing holder state fields or None if not found
        """
        try:
            holder_state_pubkey, _ = self.get_holder_state_pda(holder)
            account = self.get_account_info(holder_state_pubkey)
            
            if not account or len(account.data) < 56:
                return None
            
            # Parse holder state: skip 8-byte discriminator
            data = account.data[8:]
            
            result = {
                'address': str(holder_state_pubkey),
                'holder': str(Pubkey(data[0:32])),
                'fees_per_token_claimed': int.from_bytes(data[32:48], byteorder='little'),
                'total_claimed': int.from_bytes(data[48:56], byteorder='little'),
            }
            return result
        except Exception as e:
            raise RuntimeError(f"Failed to get holder state: {e}")
    
    def get_fee_record(
        self,
        payer: Pubkey,
        index: int
    ) -> Optional[Dict[str, Any]]:
        """
        Get fee record from chain.
        
        Args:
            payer: Payer pubkey
            index: Fee record index
            
        Returns:
            Dictionary containing fee record fields or None if not found
        """
        try:
            fee_record_pubkey, _ = self.get_fee_record_pda(payer, index)
            account = self.get_account_info(fee_record_pubkey)
            
            if not account or len(account.data) < 144:
                return None
            
            # Parse fee record: skip 8-byte discriminator
            data = account.data[8:]
            
            result = {
                'address': str(fee_record_pubkey),
                'contract': str(Pubkey(data[0:32])),
                'payer': str(Pubkey(data[32:64])),
                'recipient': str(Pubkey(data[64:96])),
                'expiration_time': int.from_bytes(data[96:104], byteorder='little', signed=True),
                'total_amount': int.from_bytes(data[104:112], byteorder='little'),
                'fee_amount': int.from_bytes(data[112:120], byteorder='little'),
                'timestamp': int.from_bytes(data[120:128], byteorder='little', signed=True),
                'index': int.from_bytes(data[128:136], byteorder='little'),
            }
            return result
        except Exception as e:
            raise RuntimeError(f"Failed to get fee record: {e}")
    
    # ======================== Utility Methods ========================
    
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
    
    def calculate_fee(self, amount: int) -> Tuple[int, int]:
        """
        Calculate 20% fee and 80% recipient amount.
        
        Args:
            amount: Total payment amount
            
        Returns:
            Tuple of (fee_amount, recipient_amount)
        """
        fee = (amount * 20) // 100
        recipient = amount - fee
        return fee, recipient
    
    def calculate_claimable_fees(
        self,
        token_balance: int,
        fees_per_token_accumulated: int,
        fees_per_token_claimed: int
    ) -> int:
        """
        Calculate claimable fees based on token balance and accumulated fees.
        
        Formula: claimable = (token_balance * delta) / PRECISION
        where delta = fees_per_token_accumulated - fees_per_token_claimed
        
        Args:
            token_balance: Current token balance
            fees_per_token_accumulated: Contract accumulated fees per token
            fees_per_token_claimed: Already claimed fees per token
        
        Returns:
            Claimable fee amount
        """
        delta = fees_per_token_accumulated - fees_per_token_claimed
        if delta <= 0:
            return 0
        
        claimable = (token_balance * delta) // self.PRECISION
        return claimable
    
    # ======================== Event Parsing ========================
    
    def parse_rent_space_event(self, logs: List[str]) -> Optional[Dict[str, Any]]:
        """Parse RentSpaceEvent from transaction logs."""
        for log in logs:
            if "RentSpaceEvent" in log or "rent_space" in log:
                return {"raw_log": log}
        return None
    
    def parse_claim_event(self, logs: List[str]) -> Optional[Dict[str, Any]]:
        """Parse ClaimEvent from transaction logs."""
        for log in logs:
            if "ClaimEvent" in log or "claim_fees" in log:
                return {"raw_log": log}
        return None
    
    def parse_supply_snapshot_event(self, logs: List[str]) -> Optional[Dict[str, Any]]:
        """Parse SupplySnapshotUpdated event from transaction logs."""
        for log in logs:
            if "SupplySnapshotUpdated" in log:
                return {"raw_log": log}
        return None



class MockContractInteractor(ContractInteractor):
    """Mock contract interactor for testing without network calls."""
    
    def __init__(self):
        """Initialize mock interactor."""
        self.program_id = Pubkey.from_string("CkM4AcKu7LSXikXaTvxwuTDBXmT2yrEZTvXbJ6Kdrox7")
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
