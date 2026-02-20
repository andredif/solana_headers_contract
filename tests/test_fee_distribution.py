"""
Test suite for the fee_distribution Solana program.
Requires:
    pip install anchorpy pytest solana solders
Run with:
    pytest test_fee_distribution.py -v
"""

import pytest
import asyncio
import struct
from typing import Tuple

from anchorpy import Program, Provider, Wallet, Context, Idl
from anchorpy.pytest_plugin import workspace_fixture
from solana.rpc.async_api import AsyncClient
from solana.rpc.commitment import Confirmed
from solana.rpc.types import TxOpts
from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solders.system_program import ID as SYS_PROGRAM_ID
from spl.token.async_client import AsyncToken
from spl.token.constants import TOKEN_PROGRAM_ID
from spl.token.instructions import (
    create_associated_token_account,
    get_associated_token_address,
)

# ── Constants ────────────────────────────────────────────────────────────────

PRECISION = 1_000_000_000_000
SUPPLY    = 1_000_000
PAYMENT   = 1_000
EXPECTED_FEE       = 200   # 20%
EXPECTED_RECIPIENT = 800   # 80%

# ── Fixtures ─────────────────────────────────────────────────────────────────

workspace = workspace_fixture(".", build_cmd="anchor build")


@pytest.fixture(scope="module")
def event_loop():
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="module")
async def program(workspace) -> Program:
    return workspace["fee_distribution"]


@pytest.fixture(scope="module")
async def provider(program: Program) -> Provider:
    return program.provider


@pytest.fixture(scope="module")
async def payer(provider: Provider) -> Keypair:
    return provider.wallet.payer


@pytest.fixture(scope="module")
async def recipient() -> Keypair:
    return Keypair()


@pytest.fixture(scope="module")
async def holder() -> Keypair:
    return Keypair()


@pytest.fixture(scope="module")
async def mint(provider: Provider, payer: Keypair) -> Pubkey:
    """Create the governance token mint."""
    client = provider.connection
    token = await AsyncToken.create_mint(
        conn=client,
        payer=payer,
        mint_authority=payer.pubkey(),
        decimals=0,
        program_id=TOKEN_PROGRAM_ID,
    )
    return token.pubkey


@pytest.fixture(scope="module")
async def pdas(program: Program, mint: Pubkey) -> dict:
    """Derive all PDAs used throughout the tests."""
    contract_pda, contract_bump = Pubkey.find_program_address(
        [b"contract"],
        program.program_id,
    )
    fee_vault_pda, fee_vault_bump = Pubkey.find_program_address(
        [b"fee_vault", bytes(contract_pda)],
        program.program_id,
    )
    return {
        "contract":       contract_pda,
        "contract_bump":  contract_bump,
        "fee_vault":      fee_vault_pda,
        "fee_vault_bump": fee_vault_bump,
    }


@pytest.fixture(scope="module")
async def token_accounts(
    provider: Provider,
    payer: Keypair,
    recipient: Keypair,
    holder: Keypair,
    mint: Pubkey,
) -> dict:
    """
    Airdrop SOL to recipient/holder and create ATA for payer,
    recipient, and holder. Mint SUPPLY tokens to payer and holder.
    """
    client = provider.connection

    # Airdrop to recipient and holder so they can pay tx fees
    for kp in [recipient, holder]:
        sig = await client.request_airdrop(kp.pubkey(), 2_000_000_000)
        await client.confirm_transaction(sig.value, commitment=Confirmed)

    token = AsyncToken(client, mint, TOKEN_PROGRAM_ID, payer)

    payer_ata     = await token.create_associated_token_account(payer.pubkey())
    recipient_ata = await token.create_associated_token_account(recipient.pubkey())
    holder_ata    = await token.create_associated_token_account(holder.pubkey())

    # Mint tokens
    await token.mint_to(payer_ata,     payer, SUPPLY)
    await token.mint_to(holder_ata,    payer, SUPPLY)

    return {
        "payer":     payer_ata,
        "recipient": recipient_ata,
        "holder":    holder_ata,
    }


# ── Helpers ───────────────────────────────────────────────────────────────────

async def get_token_balance(provider: Provider, token_account: Pubkey) -> int:
    resp = await provider.connection.get_token_account_balance(token_account)
    return int(resp.value.amount)


def fee_record_pda(program: Program, payer_pubkey: Pubkey, count: int) -> Pubkey:
    count_bytes = struct.pack("<Q", count)  # u64 little-endian
    pda, _ = Pubkey.find_program_address(
        [b"fee_record", bytes(payer_pubkey), count_bytes],
        program.program_id,
    )
    return pda


def holder_state_pda(program: Program, holder_pubkey: Pubkey) -> Pubkey:
    pda, _ = Pubkey.find_program_address(
        [b"holder_state", bytes(holder_pubkey)],
        program.program_id,
    )
    return pda


# ── Tests ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_initialize(program: Program, payer: Keypair, mint: Pubkey, pdas: dict):
    """Contract initializes with correct owner, mint, and supply snapshot."""
    await program.rpc["initialize"](
        payer.pubkey(),
        SUPPLY,
        ctx=Context(
            accounts={
                "contract":              pdas["contract"],
                "governance_token_mint": mint,
                "fee_vault":             pdas["fee_vault"],
                "signer":                payer.pubkey(),
                "token_program":         TOKEN_PROGRAM_ID,
                "system_program":        SYS_PROGRAM_ID,
            },
            signers=[payer],
        ),
    )

    state = await program.account["ContractState"].fetch(pdas["contract"])
    assert state.owner                      == payer.pubkey()
    assert state.governance_token_mint      == mint
    assert state.supply_snapshot            == SUPPLY
    assert state.fees_per_token_accumulated == 0
    assert state.fee_record_count           == 0


@pytest.mark.asyncio
async def test_init_holder_state(
    program: Program,
    holder: Keypair,
    pdas: dict,
):
    """Holder state initializes with zero claims and current accumulator baseline."""
    hs_pda = holder_state_pda(program, holder.pubkey())

    await program.rpc["init_holder_state"](
        ctx=Context(
            accounts={
                "contract":    pdas["contract"],
                "holder_state": hs_pda,
                "holder":       holder.pubkey(),
                "system_program": SYS_PROGRAM_ID,
            },
            signers=[holder],
        ),
    )

    state = await program.account["HolderState"].fetch(hs_pda)
    assert state.holder        == holder.pubkey()
    assert state.total_claimed == 0


@pytest.mark.asyncio
async def test_rent_space_correct_split(
    program: Program,
    provider: Provider,
    payer: Keypair,
    recipient: Keypair,
    mint: Pubkey,
    pdas: dict,
    token_accounts: dict,
):
    """rent_space transfers exactly 80% to recipient and 20% to fee vault."""
    import time

    contract_state = await program.account["ContractState"].fetch(pdas["contract"])
    count          = contract_state.fee_record_count
    fr_pda         = fee_record_pda(program, payer.pubkey(), count)

    recipient_before = await get_token_balance(provider, token_accounts["recipient"])
    vault_before     = await get_token_balance(provider, pdas["fee_vault"])

    expiration = int(time.time()) + 3600

    await program.rpc["rent_space"](
        recipient.pubkey(),
        expiration,
        PAYMENT,
        ctx=Context(
            accounts={
                "contract":               pdas["contract"],
                "governance_token_mint":  mint,
                "payer":                  payer.pubkey(),
                "payer_token_account":    token_accounts["payer"],
                "recipient_token_account": token_accounts["recipient"],
                "fee_vault":              pdas["fee_vault"],
                "fee_record":             fr_pda,
                "token_program":          TOKEN_PROGRAM_ID,
                "system_program":         SYS_PROGRAM_ID,
            },
            signers=[payer],
        ),
    )

    recipient_after = await get_token_balance(provider, token_accounts["recipient"])
    vault_after     = await get_token_balance(provider, pdas["fee_vault"])

    assert recipient_after - recipient_before == EXPECTED_RECIPIENT
    assert vault_after     - vault_before     == EXPECTED_FEE


@pytest.mark.asyncio
async def test_accumulator_updated_correctly(program: Program, pdas: dict):
    """fees_per_token_accumulated equals (fee * PRECISION) / supply after one payment."""
    state         = await program.account["ContractState"].fetch(pdas["contract"])
    expected_delta = (EXPECTED_FEE * PRECISION) // SUPPLY
    assert state.fees_per_token_accumulated == expected_delta


@pytest.mark.asyncio
async def test_fee_record_count_incremented(program: Program, pdas: dict):
    """fee_record_count increments after each rent_space call."""
    state = await program.account["ContractState"].fetch(pdas["contract"])
    assert state.fee_record_count == 1


@pytest.mark.asyncio
async def test_claim_fees(
    program: Program,
    provider: Provider,
    holder: Keypair,
    pdas: dict,
    token_accounts: dict,
):
    """Holder can claim a positive proportional fee amount."""
    hs_pda = holder_state_pda(program, holder.pubkey())

    holder_before = await get_token_balance(provider, token_accounts["holder"])

    await program.rpc["claim_fees"](
        ctx=Context(
            accounts={
                "contract":           pdas["contract"],
                "fee_vault":          pdas["fee_vault"],
                "holder_state":       hs_pda,
                "holder_token_account": token_accounts["holder"],
                "holder":             holder.pubkey(),
                "token_program":      TOKEN_PROGRAM_ID,
            },
            signers=[holder],
        ),
    )

    holder_after = await get_token_balance(provider, token_accounts["holder"])
    claimed      = holder_after - holder_before
    assert claimed > 0, f"Expected positive claim, got {claimed}"
    print(f"\n  Holder claimed: {claimed} tokens")


@pytest.mark.asyncio
async def test_cannot_double_claim(
    program: Program,
    holder: Keypair,
    pdas: dict,
    token_accounts: dict,
):
    """Claiming again without new fees should raise NothingToClaim."""
    hs_pda = holder_state_pda(program, holder.pubkey())

    with pytest.raises(Exception, match="NothingToClaim"):
        await program.rpc["claim_fees"](
            ctx=Context(
                accounts={
                    "contract":             pdas["contract"],
                    "fee_vault":            pdas["fee_vault"],
                    "holder_state":         hs_pda,
                    "holder_token_account": token_accounts["holder"],
                    "holder":               holder.pubkey(),
                    "token_program":        TOKEN_PROGRAM_ID,
                },
                signers=[holder],
            ),
        )


@pytest.mark.asyncio
async def test_rent_space_expired(
    program: Program,
    payer: Keypair,
    recipient: Keypair,
    mint: Pubkey,
    pdas: dict,
    token_accounts: dict,
):
    """rent_space with a past expiration_time should raise PaymentExpired."""
    import time

    contract_state = await program.account["ContractState"].fetch(pdas["contract"])
    fr_pda         = fee_record_pda(program, payer.pubkey(), contract_state.fee_record_count)

    with pytest.raises(Exception, match="PaymentExpired"):
        await program.rpc["rent_space"](
            recipient.pubkey(),
            int(time.time()) - 100,  # already expired
            PAYMENT,
            ctx=Context(
                accounts={
                    "contract":               pdas["contract"],
                    "governance_token_mint":  mint,
                    "payer":                  payer.pubkey(),
                    "payer_token_account":    token_accounts["payer"],
                    "recipient_token_account": token_accounts["recipient"],
                    "fee_vault":              pdas["fee_vault"],
                    "fee_record":             fr_pda,
                    "token_program":          TOKEN_PROGRAM_ID,
                    "system_program":         SYS_PROGRAM_ID,
                },
                signers=[payer],
            ),
        )


@pytest.mark.asyncio
async def test_rent_space_zero_payment(
    program: Program,
    payer: Keypair,
    recipient: Keypair,
    mint: Pubkey,
    pdas: dict,
    token_accounts: dict,
):
    """rent_space with payment_amount = 0 should raise InvalidPaymentAmount."""
    import time

    contract_state = await program.account["ContractState"].fetch(pdas["contract"])
    fr_pda         = fee_record_pda(program, payer.pubkey(), contract_state.fee_record_count)

    with pytest.raises(Exception, match="InvalidPaymentAmount"):
        await program.rpc["rent_space"](
            recipient.pubkey(),
            int(time.time()) + 3600,
            0,  # zero payment
            ctx=Context(
                accounts={
                    "contract":               pdas["contract"],
                    "governance_token_mint":  mint,
                    "payer":                  payer.pubkey(),
                    "payer_token_account":    token_accounts["payer"],
                    "recipient_token_account": token_accounts["recipient"],
                    "fee_vault":              pdas["fee_vault"],
                    "fee_record":             fr_pda,
                    "token_program":          TOKEN_PROGRAM_ID,
                    "system_program":         SYS_PROGRAM_ID,
                },
                signers=[payer],
            ),
        )


@pytest.mark.asyncio
async def test_update_supply_snapshot_unauthorized(
    program: Program,
    mint: Pubkey,
    pdas: dict,
):
    """Only the owner can call update_supply_snapshot."""
    attacker = Keypair()

    with pytest.raises(Exception, match="UnauthorizedOwner"):
        await program.rpc["update_supply_snapshot"](
            ctx=Context(
                accounts={
                    "contract":              pdas["contract"],
                    "governance_token_mint": mint,
                    "owner":                 attacker.pubkey(),
                },
                signers=[attacker],
            ),
        )


@pytest.mark.asyncio
async def test_update_owner_unauthorized(
    program: Program,
    pdas: dict,
):
    """Only the current owner can transfer ownership."""
    attacker = Keypair()

    with pytest.raises(Exception, match="UnauthorizedOwner"):
        await program.rpc["update_owner"](
            attacker.pubkey(),
            ctx=Context(
                accounts={
                    "contract": pdas["contract"],
                    "owner":    attacker.pubkey(),
                },
                signers=[attacker],
            ),
        )


@pytest.mark.asyncio
async def test_update_owner_authorized(
    program: Program,
    payer: Keypair,
    pdas: dict,
):
    """Owner can successfully transfer ownership and the state reflects the change."""
    new_owner = Keypair()

    await program.rpc["update_owner"](
        new_owner.pubkey(),
        ctx=Context(
            accounts={
                "contract": pdas["contract"],
                "owner":    payer.pubkey(),
            },
            signers=[payer],
        ),
    )

    state = await program.account["ContractState"].fetch(pdas["contract"])
    assert state.owner == new_owner.pubkey()

    # Transfer back so other tests aren't broken
    await program.rpc["update_owner"](
        payer.pubkey(),
        ctx=Context(
            accounts={
                "contract": pdas["contract"],
                "owner":    new_owner.pubkey(),
            },
            signers=[new_owner],
        ),
    )

    state = await program.account["ContractState"].fetch(pdas["contract"])
    assert state.owner == payer.pubkey()


@pytest.mark.asyncio
async def test_multiple_rent_space_accumulator(
    program: Program,
    provider: Provider,
    payer: Keypair,
    recipient: Keypair,
    mint: Pubkey,
    pdas: dict,
    token_accounts: dict,
):
    """Accumulator grows correctly across multiple rent_space calls."""
    import time

    state_before = await program.account["ContractState"].fetch(pdas["contract"])
    acc_before   = state_before.fees_per_token_accumulated
    count        = state_before.fee_record_count

    expiration = int(time.time()) + 3600

    await program.rpc["rent_space"](
        recipient.pubkey(),
        expiration,
        PAYMENT,
        ctx=Context(
            accounts={
                "contract":               pdas["contract"],
                "governance_token_mint":  mint,
                "payer":                  payer.pubkey(),
                "payer_token_account":    token_accounts["payer"],
                "recipient_token_account": token_accounts["recipient"],
                "fee_vault":              pdas["fee_vault"],
                "fee_record":             fee_record_pda(program, payer.pubkey(), count),
                "token_program":          TOKEN_PROGRAM_ID,
                "system_program":         SYS_PROGRAM_ID,
            },
            signers=[payer],
        ),
    )

    state_after = await program.account["ContractState"].fetch(pdas["contract"])
    expected_delta = (EXPECTED_FEE * PRECISION) // SUPPLY
    assert state_after.fees_per_token_accumulated == acc_before + expected_delta
