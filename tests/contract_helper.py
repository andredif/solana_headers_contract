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
    
    # Anchor instruction discriminators — sha256("global:{name}")[:8]
    DISCRIMINATOR_INITIALIZE          = b'\xaf\xaf\x6d\x1f\x0d\x98\x9b\xed'
    DISCRIMINATOR_UPDATE_SUPPLY       = b'\x5c\x13\xad\x98\xcd\xf6\xeb\xd1'
    DISCRIMINATOR_RENT_SPACE          = b'\x99\xe2\xcc\x02\xbb\x12\x0e\xcd'
    DISCRIMINATOR_INIT_HOLDER         = b'\x9d\x8e\xa5\x7e\x99\xf8\xa1\x34'
    DISCRIMINATOR_CLAIM_FEES          = b'\x52\xfb\xe9\x9c\x0c\x34\xb8\xca'
    DISCRIMINATOR_UPDATE_OWNER        = b'\xa4\xbc\x7c\xfe\x84\x1a\xc6\xb2'
    DISCRIMINATOR_FINALIZE_RENT       = b'\x61\x3d\xf2\xad\x9d\x0d\xc2\xcf'
    DISCRIMINATOR_REVERT_RENT         = b'\xc2\x28\x71\xf8\x67\x2f\xa9\xcf'
    DISCRIMINATOR_CLAIM_OWNER_FEE     = b'\x24\x2c\xb7\xd0\x7a\x63\x22\x89'
    DISCRIMINATOR_REVOKE_BY_ORACLE    = b'\x63\x3b\x94\xf4\xec\x3d\x0c\x99'
    DISCRIMINATOR_UPDATE_ORACLE       = b'\x70\x29\xd1\x12\xf8\xe2\xfc\xbc'
    
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
        oracle: Pubkey,
        governance_token_mint: Pubkey,
        payer: Pubkey,
        supply_snapshot: int = 1_000_000_000_000_000
    ) -> Instruction:
        """
        Create instruction for contract initialization.

        Args:
            owner: Owner pubkey (kept offline, safe)
            oracle: Oracle pubkey (used by backend to call revoke_by_oracle)
            governance_token_mint: Governance token mint
            payer: Transaction payer
            supply_snapshot: Supply snapshot at initialization time

        Returns:
            Instruction for initialize
        """
        contract_pubkey, _ = self.get_contract_pda()
        fee_vault_pubkey, _ = self.get_fee_vault_pda(contract_pubkey)

        # discriminator + owner (32) + oracle (32) + supply_snapshot (8)
        instruction_data  = self.DISCRIMINATOR_INITIALIZE
        instruction_data += bytes(owner)
        instruction_data += bytes(oracle)
        instruction_data += supply_snapshot.to_bytes(8, byteorder='little')

        accounts = [
            AccountMeta(pubkey=contract_pubkey,       is_signer=False, is_writable=True),
            AccountMeta(pubkey=governance_token_mint, is_signer=False, is_writable=False),
            AccountMeta(pubkey=fee_vault_pubkey,      is_signer=False, is_writable=True),
            AccountMeta(pubkey=payer,                 is_signer=True,  is_writable=True),
            AccountMeta(pubkey=Pubkey.from_string(self.TOKEN_PROGRAM),  is_signer=False, is_writable=False),
            AccountMeta(pubkey=Pubkey.from_string(self.SYSTEM_PROGRAM), is_signer=False, is_writable=False),
            AccountMeta(pubkey=RENT,                  is_signer=False, is_writable=False),
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

        instruction_data  = self.DISCRIMINATOR_UPDATE_OWNER
        instruction_data += bytes(new_owner)

        accounts = [
            AccountMeta(pubkey=contract_pubkey, is_signer=False, is_writable=True),
            AccountMeta(pubkey=current_owner,   is_signer=True,  is_writable=False),
        ]

        return Instruction(
            program_id=self.program_id,
            accounts=accounts,
            data=instruction_data
        )

    def create_update_oracle_instruction(
        self,
        new_oracle: Pubkey,
        current_owner: Pubkey
    ) -> Instruction:
        """
        Create instruction to rotate the oracle keypair (owner only).

        Args:
            new_oracle: New oracle pubkey
            current_owner: Current owner signer pubkey

        Returns:
            Instruction for update_oracle
        """
        contract_pubkey, _ = self.get_contract_pda()

        instruction_data  = self.DISCRIMINATOR_UPDATE_ORACLE
        instruction_data += bytes(new_oracle)

        accounts = [
            AccountMeta(pubkey=contract_pubkey, is_signer=False, is_writable=True),
            AccountMeta(pubkey=current_owner,   is_signer=True,  is_writable=False),
        ]

        return Instruction(
            program_id=self.program_id,
            accounts=accounts,
            data=instruction_data
        )

    def create_finalize_rent_instruction(
        self,
        payer: Pubkey,
        fee_record_index: int,
        caller: Pubkey
    ) -> Instruction:
        """
        Create instruction to finalize a rent after expiry.
        Permissionless — anyone can be the caller.

        Args:
            payer: Original payer whose fee record we are finalizing
            fee_record_index: Index of the fee record
            caller: Transaction signer (any pubkey)

        Returns:
            Instruction for finalize_rent
        """
        contract_pubkey, _ = self.get_contract_pda()
        fee_vault_pubkey, _ = self.get_fee_vault_pda(contract_pubkey)
        fee_record_pubkey, _ = self.get_fee_record_pda(payer, fee_record_index)

        accounts = [
            AccountMeta(pubkey=contract_pubkey,   is_signer=False, is_writable=True),
            AccountMeta(pubkey=fee_vault_pubkey,  is_signer=False, is_writable=True),
            AccountMeta(pubkey=fee_record_pubkey, is_signer=False, is_writable=True),
            AccountMeta(pubkey=caller,            is_signer=True,  is_writable=False),
        ]

        return Instruction(
            program_id=self.program_id,
            accounts=accounts,
            data=self.DISCRIMINATOR_FINALIZE_RENT
        )

    def create_revert_rent_instruction(
        self,
        payer: Pubkey,
        fee_record_index: int,
        authority: Pubkey,
        payer_token_account: Pubkey
    ) -> Instruction:
        """
        Create instruction to revert a rent (loaner or owner only).
        Returns governance + owner fees to the loaner.

        Args:
            payer: Original payer (loaner) pubkey
            fee_record_index: Index of the fee record
            authority: Signer — must be payer or contract owner
            payer_token_account: Loaner's token account that receives the refund

        Returns:
            Instruction for revert_rent
        """
        contract_pubkey, _ = self.get_contract_pda()
        fee_vault_pubkey, _ = self.get_fee_vault_pda(contract_pubkey)
        fee_record_pubkey, _ = self.get_fee_record_pda(payer, fee_record_index)

        accounts = [
            AccountMeta(pubkey=contract_pubkey,      is_signer=False, is_writable=False),
            AccountMeta(pubkey=fee_vault_pubkey,     is_signer=False, is_writable=True),
            AccountMeta(pubkey=fee_record_pubkey,    is_signer=False, is_writable=True),
            AccountMeta(pubkey=authority,            is_signer=True,  is_writable=False),
            AccountMeta(pubkey=payer_token_account,  is_signer=False, is_writable=True),
            AccountMeta(pubkey=Pubkey.from_string(self.TOKEN_PROGRAM), is_signer=False, is_writable=False),
        ]

        return Instruction(
            program_id=self.program_id,
            accounts=accounts,
            data=self.DISCRIMINATOR_REVERT_RENT
        )

    def create_claim_owner_fee_instruction(
        self,
        payer: Pubkey,
        fee_record_index: int,
        owner: Pubkey,
        owner_token_account: Pubkey
    ) -> Instruction:
        """
        Create instruction for the contract owner to claim their 5 % fee
        from a finalized rental record.

        Args:
            payer: Original payer pubkey (used for fee record PDA derivation)
            fee_record_index: Index of the fee record
            owner: Contract owner signer
            owner_token_account: Owner's token account that receives the fee

        Returns:
            Instruction for claim_owner_fee
        """
        contract_pubkey, _ = self.get_contract_pda()
        fee_vault_pubkey, _ = self.get_fee_vault_pda(contract_pubkey)
        fee_record_pubkey, _ = self.get_fee_record_pda(payer, fee_record_index)

        accounts = [
            AccountMeta(pubkey=contract_pubkey,     is_signer=False, is_writable=False),
            AccountMeta(pubkey=fee_vault_pubkey,    is_signer=False, is_writable=True),
            AccountMeta(pubkey=fee_record_pubkey,   is_signer=False, is_writable=True),
            AccountMeta(pubkey=owner,               is_signer=True,  is_writable=False),
            AccountMeta(pubkey=owner_token_account, is_signer=False, is_writable=True),
            AccountMeta(pubkey=Pubkey.from_string(self.TOKEN_PROGRAM), is_signer=False, is_writable=False),
        ]

        return Instruction(
            program_id=self.program_id,
            accounts=accounts,
            data=self.DISCRIMINATOR_CLAIM_OWNER_FEE
        )

    def create_revoke_by_oracle_instruction(
        self,
        payer: Pubkey,
        fee_record_index: int,
        oracle: Pubkey,
        payer_token_account: Pubkey,
        reason: str
    ) -> Instruction:
        """
        Create instruction for the oracle to revoke a rent (violation detected).
        The full locked fee (governance + owner) is refunded to the loaner.

        Args:
            payer: Original payer (loaner) pubkey
            fee_record_index: Index of the fee record
            oracle: Oracle backend signer
            payer_token_account: Loaner's token account that receives the refund
            reason: Human-readable reason string logged on-chain (e.g. "header_image_changed")

        Returns:
            Instruction for revoke_by_oracle
        """
        contract_pubkey, _ = self.get_contract_pda()
        fee_vault_pubkey, _ = self.get_fee_vault_pda(contract_pubkey)
        fee_record_pubkey, _ = self.get_fee_record_pda(payer, fee_record_index)

        # Borsh-encode the reason string: u32 length prefix + UTF-8 bytes
        reason_bytes = reason.encode('utf-8')
        instruction_data  = self.DISCRIMINATOR_REVOKE_BY_ORACLE
        instruction_data += len(reason_bytes).to_bytes(4, byteorder='little')
        instruction_data += reason_bytes

        accounts = [
            AccountMeta(pubkey=contract_pubkey,     is_signer=False, is_writable=False),
            AccountMeta(pubkey=fee_vault_pubkey,    is_signer=False, is_writable=True),
            AccountMeta(pubkey=fee_record_pubkey,   is_signer=False, is_writable=True),
            AccountMeta(pubkey=oracle,              is_signer=True,  is_writable=False),
            AccountMeta(pubkey=payer_token_account, is_signer=False, is_writable=True),
            AccountMeta(pubkey=Pubkey.from_string(self.TOKEN_PROGRAM), is_signer=False, is_writable=False),
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

        ContractState borsh layout (after 8-byte discriminator):
          owner                    [0:32]
          governance_token_mint   [32:64]
          oracle                  [64:96]   ← new
          bump                    [96]
          fee_vault_bump          [97]
          fees_per_token_accumulated [98:114]  (u128)
          total_fees_accumulated  [114:122] (u64)
          fee_record_count        [122:130] (u64)
          supply_snapshot         [130:138] (u64)
        """
        try:
            contract_pubkey, _ = self.get_contract_pda()
            account = self.get_account_info(contract_pubkey)

            if not account or len(account.data) < 146:   # 8 discriminator + 138
                return None

            data = account.data[8:]
            return {
                'address':                    str(contract_pubkey),
                'owner':                      str(Pubkey(data[0:32])),
                'governance_token_mint':      str(Pubkey(data[32:64])),
                'oracle':                     str(Pubkey(data[64:96])),
                'bump':                       data[96],
                'fee_vault_bump':             data[97],
                'fees_per_token_accumulated': int.from_bytes(data[98:114],  byteorder='little'),
                'total_fees_accumulated':     int.from_bytes(data[114:122], byteorder='little'),
                'fee_record_count':           int.from_bytes(data[122:130], byteorder='little'),
                'supply_snapshot':            int.from_bytes(data[130:138], byteorder='little'),
            }
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

        FeeRecord borsh layout (after 8-byte discriminator):
          contract          [0:32]
          payer             [32:64]
          recipient         [64:96]
          expiration_time   [96:104]  (i64)
          total_amount      [104:112] (u64)
          governance_fee    [112:120] (u64)  — 15 %
          owner_fee         [120:128] (u64)  —  5 %
          timestamp         [128:136] (i64)
          index             [136:144] (u64)
          is_finalized      [144]     (bool)
          is_reverted       [145]     (bool)
          owner_fee_claimed [146]     (bool)
        """
        try:
            fee_record_pubkey, _ = self.get_fee_record_pda(payer, index)
            account = self.get_account_info(fee_record_pubkey)

            if not account or len(account.data) < 155:  # 8 + 147
                return None

            data = account.data[8:]
            return {
                'address':          str(fee_record_pubkey),
                'contract':         str(Pubkey(data[0:32])),
                'payer':            str(Pubkey(data[32:64])),
                'recipient':        str(Pubkey(data[64:96])),
                'expiration_time':  int.from_bytes(data[96:104],  byteorder='little', signed=True),
                'total_amount':     int.from_bytes(data[104:112], byteorder='little'),
                'governance_fee':   int.from_bytes(data[112:120], byteorder='little'),
                'owner_fee':        int.from_bytes(data[120:128], byteorder='little'),
                'timestamp':        int.from_bytes(data[128:136], byteorder='little', signed=True),
                'index':            int.from_bytes(data[136:144], byteorder='little'),
                'is_finalized':     bool(data[144]),
                'is_reverted':      bool(data[145]),
                'owner_fee_claimed': bool(data[146]),
            }
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
    
    def calculate_fees(self, amount: int) -> Tuple[int, int, int]:
        """
        Calculate the 80/15/5 fee split.

        Args:
            amount: Total payment amount

        Returns:
            Tuple of (recipient_amount, governance_fee, owner_fee)
        """
        governance_fee = (amount * 15) // 100
        owner_fee      = (amount *  5) // 100
        recipient      = amount - governance_fee - owner_fee
        return recipient, governance_fee, owner_fee

    def calculate_fee(self, amount: int) -> Tuple[int, int]:
        """
        Legacy helper: returns (total_vault_fee, recipient_amount).
        Prefer calculate_fees() for the full 3-way split.
        """
        _, gov, own = self.calculate_fees(amount)
        vault = gov + own
        return vault, amount - vault
    
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
