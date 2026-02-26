"""
Test suite for the fee_distribution Solana program.

Fee model:
  80% → renter (immediate)
  15% → fee vault, claimable by governance holders after finalize_rent
   5% → fee vault, claimable by contract owner after finalize_rent
  revert_rent / revoke_by_oracle send vault funds back to the loaner.

Requires:
    pip install anchorpy pytest pytest-asyncio solana solders spl-token
Run with:
    pytest tests/test_fee_distribution.py -v
"""

import asyncio
import struct
import time
import pytest

from anchorpy import Program, Provider, Context
from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solders.system_program import ID as SYS_PROGRAM_ID
from spl.token.async_client import AsyncToken
from spl.token.constants import TOKEN_PROGRAM_ID
from solana.rpc.commitment import Confirmed

# ── Constants ─────────────────────────────────────────────────────────────────

PRECISION = 1_000_000_000_000
SUPPLY    = 1_000_000
PAYMENT   = 1_000

# 80 / 15 / 5 split on PAYMENT = 1000
EXPECTED_RECIPIENT   = 800   # 80 % — sent to renter immediately
EXPECTED_GOV_FEE     = 150   # 15 % — locked in vault for governance holders
EXPECTED_OWNER_FEE   = 50    #  5 % — locked in vault for contract owner
EXPECTED_VAULT_TOTAL = 200   # 20 % — total locked (gov + owner)

# After finalising one rent with governance_fee = 150 and SUPPLY = 1_000_000:
#   delta     = (150 * PRECISION) / SUPPLY        = 150_000_000
#   claimable = (SUPPLY * delta)  / PRECISION     = 150
EXPECTED_HOLDER_CLAIM = 150
EXPECTED_OWNER_CLAIM  = 50

# ── Fixtures ──────────────────────────────────────────────────────────────────

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
    """Wallet that pays for rents (the loaner)."""
    return provider.wallet.payer


@pytest.fixture(scope="module")
async def oracle() -> Keypair:
    """Backend monitoring keypair — calls revoke_by_oracle."""
    return Keypair()


@pytest.fixture(scope="module")
async def recipient() -> Keypair:
    """The renter — receives 80 % immediately."""
    return Keypair()


@pytest.fixture(scope="module")
async def holder() -> Keypair:
    """Governance token holder — claims the 15 % after finalization."""
    return Keypair()


@pytest.fixture(scope="module")
async def mint(provider: Provider, payer: Keypair) -> Pubkey:
    """Create the governance token mint."""
    token = await AsyncToken.create_mint(
        conn=provider.connection,
        payer=payer,
        mint_authority=payer.pubkey(),
        decimals=0,
        program_id=TOKEN_PROGRAM_ID,
    )
    return token.pubkey


@pytest.fixture(scope="module")
async def pdas(program: Program) -> dict:
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
    oracle: Keypair,
    mint: Pubkey,
) -> dict:
    """
    Airdrop SOL to secondary wallets, create ATAs, mint governance tokens.
    payer ATA also serves as the owner ATA (payer IS owner in tests).
    """
    client = provider.connection

    for kp in [recipient, holder, oracle]:
        sig = await client.request_airdrop(kp.pubkey(), 2_000_000_000)
        await client.confirm_transaction(sig.value, commitment=Confirmed)

    token      = AsyncToken(client, mint, TOKEN_PROGRAM_ID, payer)
    payer_ata  = await token.create_associated_token_account(payer.pubkey())
    rec_ata    = await token.create_associated_token_account(recipient.pubkey())
    holder_ata = await token.create_associated_token_account(holder.pubkey())

    await token.mint_to(payer_ata,  payer, SUPPLY)
    await token.mint_to(holder_ata, payer, SUPPLY)

    return {
        "payer":     payer_ata,
        "recipient": rec_ata,
        "holder":    holder_ata,
        "owner":     payer_ata,   # payer == owner in these tests
    }


# ── Helpers ───────────────────────────────────────────────────────────────────


async def get_token_balance(provider: Provider, ata: Pubkey) -> int:
    resp = await provider.connection.get_token_account_balance(ata)
    return int(resp.value.amount)


def fee_record_pda(program: Program, payer_pubkey: Pubkey, index: int) -> Pubkey:
    pda, _ = Pubkey.find_program_address(
        [b"fee_record", bytes(payer_pubkey), struct.pack("<Q", index)],
        program.program_id,
    )
    return pda


def holder_state_pda(program: Program, holder_pubkey: Pubkey) -> Pubkey:
    pda, _ = Pubkey.find_program_address(
        [b"holder_state", bytes(holder_pubkey)],
        program.program_id,
    )
    return pda


async def create_rent(
    program: Program,
    payer: Keypair,
    recipient: Keypair,
    mint: Pubkey,
    pdas: dict,
    token_accounts: dict,
    expiration_offset: int,
) -> tuple:
    """Create a rent and return (fee_record_pda, record_index)."""
    state  = await program.account["ContractState"].fetch(pdas["contract"])
    index  = state.fee_record_count
    fr_pda = fee_record_pda(program, payer.pubkey(), index)

    await program.rpc["rent_space"](
        recipient.pubkey(),
        int(time.time()) + expiration_offset,
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
    return fr_pda, index


# ══════════════════════════════════════════════════════════════════════════════
#  1. Initialization
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_initialize(
    program: Program,
    payer: Keypair,
    oracle: Keypair,
    mint: Pubkey,
    pdas: dict,
):
    """Contract initializes with correct owner, oracle, mint, and zero accumulators."""
    await program.rpc["initialize"](
        payer.pubkey(),   # owner
        oracle.pubkey(),  # oracle
        SUPPLY,           # supply_snapshot
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
    assert state.oracle                     == oracle.pubkey()
    assert state.governance_token_mint      == mint
    assert state.supply_snapshot            == SUPPLY
    assert state.fees_per_token_accumulated == 0
    assert state.total_fees_accumulated     == 0
    assert state.fee_record_count           == 0


# ══════════════════════════════════════════════════════════════════════════════
#  2. Holder state
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_init_holder_state(
    program: Program,
    holder: Keypair,
    pdas: dict,
):
    """Holder state initializes with zero claims and the current accumulator baseline."""
    hs_pda = holder_state_pda(program, holder.pubkey())

    await program.rpc["init_holder_state"](
        ctx=Context(
            accounts={
                "contract":       pdas["contract"],
                "holder_state":   hs_pda,
                "holder":         holder.pubkey(),
                "system_program": SYS_PROGRAM_ID,
            },
            signers=[holder],
        ),
    )

    hs = await program.account["HolderState"].fetch(hs_pda)
    assert hs.holder                 == holder.pubkey()
    assert hs.total_claimed          == 0
    assert hs.fees_per_token_claimed == 0   # accumulator starts at 0


# ══════════════════════════════════════════════════════════════════════════════
#  3. rent_space — 80/15/5 split, vault locked immediately
# ══════════════════════════════════════════════════════════════════════════════


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
    """
    rent_space sends 80 % to the renter immediately and locks 20 %
    (15 % governance + 5 % owner) in the fee vault.
    Creates rent[0] with a 1-hour expiry.
    """
    recipient_before = await get_token_balance(provider, token_accounts["recipient"])
    vault_before     = await get_token_balance(provider, pdas["fee_vault"])

    fr_pda, _ = await create_rent(
        program, payer, recipient, mint, pdas, token_accounts,
        expiration_offset=3600,
    )

    recipient_after = await get_token_balance(provider, token_accounts["recipient"])
    vault_after     = await get_token_balance(provider, pdas["fee_vault"])

    assert recipient_after - recipient_before == EXPECTED_RECIPIENT,   "80 % not sent to recipient"
    assert vault_after     - vault_before     == EXPECTED_VAULT_TOTAL, "20 % not locked in vault"

    # Verify FeeRecord fields
    rec = await program.account["FeeRecord"].fetch(fr_pda)
    assert rec.governance_fee    == EXPECTED_GOV_FEE
    assert rec.owner_fee         == EXPECTED_OWNER_FEE
    assert rec.total_amount      == PAYMENT
    assert rec.is_finalized      is False
    assert rec.is_reverted       is False
    assert rec.owner_fee_claimed is False


@pytest.mark.asyncio
async def test_accumulator_not_updated_before_finalize(
    program: Program,
    pdas: dict,
):
    """
    Accumulator must stay at 0 immediately after rent_space.
    It is only updated when finalize_rent is called post-expiry.
    """
    state = await program.account["ContractState"].fetch(pdas["contract"])
    assert state.fees_per_token_accumulated == 0, \
        "Accumulator must NOT update during rent_space"
    assert state.total_fees_accumulated == 0


@pytest.mark.asyncio
async def test_fee_record_count_incremented(program: Program, pdas: dict):
    """fee_record_count increments after each rent_space call (should be 1)."""
    state = await program.account["ContractState"].fetch(pdas["contract"])
    assert state.fee_record_count == 1


# ══════════════════════════════════════════════════════════════════════════════
#  4. finalize_rent
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_finalize_before_expiry_fails(
    program: Program,
    payer: Keypair,
    recipient: Keypair,
    mint: Pubkey,
    pdas: dict,
    token_accounts: dict,
):
    """
    finalize_rent before expiry must raise RentNotExpired.
    Also creates rent[1] with a 4-second expiry so the next test can finalize it.
    """
    fr_pda, _ = await create_rent(
        program, payer, recipient, mint, pdas, token_accounts,
        expiration_offset=4,
    )

    with pytest.raises(Exception, match="RentNotExpired"):
        await program.rpc["finalize_rent"](
            ctx=Context(
                accounts={
                    "contract":   pdas["contract"],
                    "fee_vault":  pdas["fee_vault"],
                    "fee_record": fr_pda,
                    "caller":     payer.pubkey(),
                },
                signers=[payer],
            ),
        )


@pytest.mark.asyncio
async def test_finalize_rent_succeeds(
    program: Program,
    payer: Keypair,
    pdas: dict,
):
    """
    After expiry, finalize_rent releases governance fees into the accumulator.
    Sleeps past rent[1]'s 4-second expiry then finalizes.
    """
    await asyncio.sleep(5)

    fr_pda     = fee_record_pda(program, payer.pubkey(), 1)
    acc_before = (await program.account["ContractState"].fetch(pdas["contract"])).fees_per_token_accumulated

    await program.rpc["finalize_rent"](
        ctx=Context(
            accounts={
                "contract":   pdas["contract"],
                "fee_vault":  pdas["fee_vault"],
                "fee_record": fr_pda,
                "caller":     payer.pubkey(),
            },
            signers=[payer],
        ),
    )

    state = await program.account["ContractState"].fetch(pdas["contract"])
    expected_delta = (EXPECTED_GOV_FEE * PRECISION) // SUPPLY
    assert state.fees_per_token_accumulated == acc_before + expected_delta, \
        "Accumulator incorrect after finalize_rent"
    assert state.total_fees_accumulated == EXPECTED_GOV_FEE

    rec = await program.account["FeeRecord"].fetch(fr_pda)
    assert rec.is_finalized is True


@pytest.mark.asyncio
async def test_cannot_finalize_twice(
    program: Program,
    payer: Keypair,
    pdas: dict,
):
    """Double-finalize must raise RentAlreadyFinalized."""
    fr_pda = fee_record_pda(program, payer.pubkey(), 1)

    with pytest.raises(Exception, match="RentAlreadyFinalized"):
        await program.rpc["finalize_rent"](
            ctx=Context(
                accounts={
                    "contract":   pdas["contract"],
                    "fee_vault":  pdas["fee_vault"],
                    "fee_record": fr_pda,
                    "caller":     payer.pubkey(),
                },
                signers=[payer],
            ),
        )


# ══════════════════════════════════════════════════════════════════════════════
#  5. claim_fees (governance holders)
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_claim_fees_after_finalize(
    program: Program,
    provider: Provider,
    holder: Keypair,
    pdas: dict,
    token_accounts: dict,
):
    """
    Holder claims proportional share after finalize_rent.
    With SUPPLY = 1_000_000 tokens and governance_fee = 150:
      claimable = (1_000_000 * delta) / PRECISION = 150
    """
    hs_pda        = holder_state_pda(program, holder.pubkey())
    holder_before = await get_token_balance(provider, token_accounts["holder"])

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

    holder_after = await get_token_balance(provider, token_accounts["holder"])
    claimed      = holder_after - holder_before
    assert claimed == EXPECTED_HOLDER_CLAIM, f"Expected {EXPECTED_HOLDER_CLAIM}, got {claimed}"

    hs = await program.account["HolderState"].fetch(hs_pda)
    assert hs.total_claimed == EXPECTED_HOLDER_CLAIM


@pytest.mark.asyncio
async def test_cannot_double_claim_holder(
    program: Program,
    holder: Keypair,
    pdas: dict,
    token_accounts: dict,
):
    """Second claim without new fees must raise NothingToClaim."""
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


# ══════════════════════════════════════════════════════════════════════════════
#  6. claim_owner_fee
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_claim_owner_fee_before_finalize_fails(
    program: Program,
    payer: Keypair,
    pdas: dict,
    token_accounts: dict,
):
    """Owner cannot claim from rent[0] because it is not yet finalized."""
    fr_pda = fee_record_pda(program, payer.pubkey(), 0)

    with pytest.raises(Exception, match="RentNotExpired"):
        await program.rpc["claim_owner_fee"](
            ctx=Context(
                accounts={
                    "contract":            pdas["contract"],
                    "fee_vault":           pdas["fee_vault"],
                    "fee_record":          fr_pda,
                    "owner":               payer.pubkey(),
                    "owner_token_account": token_accounts["owner"],
                    "token_program":       TOKEN_PROGRAM_ID,
                },
                signers=[payer],
            ),
        )


@pytest.mark.asyncio
async def test_claim_owner_fee_success(
    program: Program,
    provider: Provider,
    payer: Keypair,
    pdas: dict,
    token_accounts: dict,
):
    """Owner withdraws the 5 % fee from finalized rent[1]."""
    fr_pda       = fee_record_pda(program, payer.pubkey(), 1)
    owner_before = await get_token_balance(provider, token_accounts["owner"])

    await program.rpc["claim_owner_fee"](
        ctx=Context(
            accounts={
                "contract":            pdas["contract"],
                "fee_vault":           pdas["fee_vault"],
                "fee_record":          fr_pda,
                "owner":               payer.pubkey(),
                "owner_token_account": token_accounts["owner"],
                "token_program":       TOKEN_PROGRAM_ID,
            },
            signers=[payer],
        ),
    )

    owner_after = await get_token_balance(provider, token_accounts["owner"])
    claimed     = owner_after - owner_before
    assert claimed == EXPECTED_OWNER_CLAIM, f"Expected {EXPECTED_OWNER_CLAIM}, got {claimed}"

    rec = await program.account["FeeRecord"].fetch(fr_pda)
    assert rec.owner_fee_claimed is True


@pytest.mark.asyncio
async def test_cannot_double_claim_owner_fee(
    program: Program,
    payer: Keypair,
    pdas: dict,
    token_accounts: dict,
):
    """Second claim_owner_fee on the same record must raise OwnerFeeAlreadyClaimed."""
    fr_pda = fee_record_pda(program, payer.pubkey(), 1)

    with pytest.raises(Exception, match="OwnerFeeAlreadyClaimed"):
        await program.rpc["claim_owner_fee"](
            ctx=Context(
                accounts={
                    "contract":            pdas["contract"],
                    "fee_vault":           pdas["fee_vault"],
                    "fee_record":          fr_pda,
                    "owner":               payer.pubkey(),
                    "owner_token_account": token_accounts["owner"],
                    "token_program":       TOKEN_PROGRAM_ID,
                },
                signers=[payer],
            ),
        )


# ══════════════════════════════════════════════════════════════════════════════
#  7. revert_rent
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_revert_rent_unauthorized(
    program: Program,
    holder: Keypair,
    payer: Keypair,
    pdas: dict,
    token_accounts: dict,
):
    """A random holder (neither payer nor owner) cannot revert rent[0]."""
    fr_pda = fee_record_pda(program, payer.pubkey(), 0)

    with pytest.raises(Exception, match="UnauthorizedOwner"):
        await program.rpc["revert_rent"](
            ctx=Context(
                accounts={
                    "contract":            pdas["contract"],
                    "fee_vault":           pdas["fee_vault"],
                    "fee_record":          fr_pda,
                    "authority":           holder.pubkey(),
                    "payer_token_account": token_accounts["payer"],
                    "token_program":       TOKEN_PROGRAM_ID,
                },
                signers=[holder],
            ),
        )


@pytest.mark.asyncio
async def test_revert_rent_by_payer(
    program: Program,
    provider: Provider,
    payer: Keypair,
    pdas: dict,
    token_accounts: dict,
):
    """
    Payer reverts rent[0] (still unexpired).
    The full 20 % (governance_fee + owner_fee) is returned to the payer.
    """
    fr_pda       = fee_record_pda(program, payer.pubkey(), 0)
    payer_before = await get_token_balance(provider, token_accounts["payer"])

    await program.rpc["revert_rent"](
        ctx=Context(
            accounts={
                "contract":            pdas["contract"],
                "fee_vault":           pdas["fee_vault"],
                "fee_record":          fr_pda,
                "authority":           payer.pubkey(),
                "payer_token_account": token_accounts["payer"],
                "token_program":       TOKEN_PROGRAM_ID,
            },
            signers=[payer],
        ),
    )

    payer_after = await get_token_balance(provider, token_accounts["payer"])
    refund      = payer_after - payer_before
    assert refund == EXPECTED_VAULT_TOTAL, \
        f"Expected refund of {EXPECTED_VAULT_TOTAL}, got {refund}"

    rec = await program.account["FeeRecord"].fetch(fr_pda)
    assert rec.is_reverted is True


@pytest.mark.asyncio
async def test_cannot_revert_twice(
    program: Program,
    payer: Keypair,
    pdas: dict,
    token_accounts: dict,
):
    """Second revert_rent must raise RentAlreadyReverted."""
    fr_pda = fee_record_pda(program, payer.pubkey(), 0)

    with pytest.raises(Exception, match="RentAlreadyReverted"):
        await program.rpc["revert_rent"](
            ctx=Context(
                accounts={
                    "contract":            pdas["contract"],
                    "fee_vault":           pdas["fee_vault"],
                    "fee_record":          fr_pda,
                    "authority":           payer.pubkey(),
                    "payer_token_account": token_accounts["payer"],
                    "token_program":       TOKEN_PROGRAM_ID,
                },
                signers=[payer],
            ),
        )


@pytest.mark.asyncio
async def test_cannot_finalize_reverted_rent(
    program: Program,
    payer: Keypair,
    pdas: dict,
):
    """finalize_rent on a reverted record must raise RentAlreadyReverted."""
    fr_pda = fee_record_pda(program, payer.pubkey(), 0)

    with pytest.raises(Exception, match="RentAlreadyReverted"):
        await program.rpc["finalize_rent"](
            ctx=Context(
                accounts={
                    "contract":   pdas["contract"],
                    "fee_vault":  pdas["fee_vault"],
                    "fee_record": fr_pda,
                    "caller":     payer.pubkey(),
                },
                signers=[payer],
            ),
        )


# ══════════════════════════════════════════════════════════════════════════════
#  8. revoke_by_oracle
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_revoke_by_oracle_wrong_key(
    program: Program,
    payer: Keypair,
    holder: Keypair,
    recipient: Keypair,
    mint: Pubkey,
    pdas: dict,
    token_accounts: dict,
):
    """
    Creates rent[2] (1-hour expiry) and tries to revoke it with the holder key
    (not the oracle) — must raise UnauthorizedOracle.
    """
    await create_rent(
        program, payer, recipient, mint, pdas, token_accounts,
        expiration_offset=3600,
    )
    fr_pda = fee_record_pda(program, payer.pubkey(), 2)

    with pytest.raises(Exception, match="UnauthorizedOracle"):
        await program.rpc["revoke_by_oracle"](
            "header_image_changed",
            ctx=Context(
                accounts={
                    "contract":            pdas["contract"],
                    "fee_vault":           pdas["fee_vault"],
                    "fee_record":          fr_pda,
                    "oracle":              holder.pubkey(),   # wrong key
                    "payer_token_account": token_accounts["payer"],
                    "token_program":       TOKEN_PROGRAM_ID,
                },
                signers=[holder],
            ),
        )


@pytest.mark.asyncio
async def test_revoke_by_oracle_success(
    program: Program,
    provider: Provider,
    payer: Keypair,
    oracle: Keypair,
    pdas: dict,
    token_accounts: dict,
):
    """Oracle revokes rent[2] — loaner gets the 20 % vault amount refunded."""
    fr_pda       = fee_record_pda(program, payer.pubkey(), 2)
    payer_before = await get_token_balance(provider, token_accounts["payer"])

    await program.rpc["revoke_by_oracle"](
        "header_image_changed",
        ctx=Context(
            accounts={
                "contract":            pdas["contract"],
                "fee_vault":           pdas["fee_vault"],
                "fee_record":          fr_pda,
                "oracle":              oracle.pubkey(),
                "payer_token_account": token_accounts["payer"],
                "token_program":       TOKEN_PROGRAM_ID,
            },
            signers=[oracle],
        ),
    )

    payer_after = await get_token_balance(provider, token_accounts["payer"])
    refund      = payer_after - payer_before
    assert refund == EXPECTED_VAULT_TOTAL, \
        f"Expected refund {EXPECTED_VAULT_TOTAL}, got {refund}"

    rec = await program.account["FeeRecord"].fetch(fr_pda)
    assert rec.is_reverted is True


@pytest.mark.asyncio
async def test_cannot_revoke_already_reverted(
    program: Program,
    payer: Keypair,
    oracle: Keypair,
    pdas: dict,
    token_accounts: dict,
):
    """Oracle cannot revoke a record that is already reverted."""
    fr_pda = fee_record_pda(program, payer.pubkey(), 2)

    with pytest.raises(Exception, match="RentAlreadyReverted"):
        await program.rpc["revoke_by_oracle"](
            "duplicate",
            ctx=Context(
                accounts={
                    "contract":            pdas["contract"],
                    "fee_vault":           pdas["fee_vault"],
                    "fee_record":          fr_pda,
                    "oracle":              oracle.pubkey(),
                    "payer_token_account": token_accounts["payer"],
                    "token_program":       TOKEN_PROGRAM_ID,
                },
                signers=[oracle],
            ),
        )


@pytest.mark.asyncio
async def test_oracle_cannot_revoke_finalized(
    program: Program,
    payer: Keypair,
    oracle: Keypair,
    pdas: dict,
    token_accounts: dict,
):
    """Oracle cannot revoke an already-finalized record (rent[1])."""
    fr_pda = fee_record_pda(program, payer.pubkey(), 1)

    with pytest.raises(Exception, match="RentAlreadyFinalized"):
        await program.rpc["revoke_by_oracle"](
            "too late",
            ctx=Context(
                accounts={
                    "contract":            pdas["contract"],
                    "fee_vault":           pdas["fee_vault"],
                    "fee_record":          fr_pda,
                    "oracle":              oracle.pubkey(),
                    "payer_token_account": token_accounts["payer"],
                    "token_program":       TOKEN_PROGRAM_ID,
                },
                signers=[oracle],
            ),
        )


# ══════════════════════════════════════════════════════════════════════════════
#  9. update_oracle
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_update_oracle_unauthorized(
    program: Program,
    holder: Keypair,
    pdas: dict,
):
    """Non-owner cannot rotate the oracle keypair."""
    with pytest.raises(Exception, match="UnauthorizedOwner"):
        await program.rpc["update_oracle"](
            Keypair().pubkey(),
            ctx=Context(
                accounts={
                    "contract": pdas["contract"],
                    "owner":    holder.pubkey(),
                },
                signers=[holder],
            ),
        )


@pytest.mark.asyncio
async def test_update_oracle_authorized(
    program: Program,
    payer: Keypair,
    oracle: Keypair,
    pdas: dict,
):
    """Owner can rotate the oracle keypair; state is updated and then restored."""
    new_oracle = Keypair()

    await program.rpc["update_oracle"](
        new_oracle.pubkey(),
        ctx=Context(
            accounts={
                "contract": pdas["contract"],
                "owner":    payer.pubkey(),
            },
            signers=[payer],
        ),
    )
    state = await program.account["ContractState"].fetch(pdas["contract"])
    assert state.oracle == new_oracle.pubkey()

    # Restore so remaining tests are not affected
    await program.rpc["update_oracle"](
        oracle.pubkey(),
        ctx=Context(
            accounts={
                "contract": pdas["contract"],
                "owner":    payer.pubkey(),
            },
            signers=[payer],
        ),
    )
    state = await program.account["ContractState"].fetch(pdas["contract"])
    assert state.oracle == oracle.pubkey()


# ══════════════════════════════════════════════════════════════════════════════
#  10. update_owner
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_update_owner_unauthorized(
    program: Program,
    holder: Keypair,
    pdas: dict,
):
    """Non-owner cannot transfer ownership."""
    with pytest.raises(Exception, match="UnauthorizedOwner"):
        await program.rpc["update_owner"](
            holder.pubkey(),
            ctx=Context(
                accounts={
                    "contract": pdas["contract"],
                    "owner":    holder.pubkey(),
                },
                signers=[holder],
            ),
        )


@pytest.mark.asyncio
async def test_update_owner_authorized(
    program: Program,
    payer: Keypair,
    pdas: dict,
):
    """Owner can transfer ownership; state is updated and then restored."""
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


# ══════════════════════════════════════════════════════════════════════════════
#  11. update_supply_snapshot
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_update_supply_snapshot_unauthorized(
    program: Program,
    mint: Pubkey,
    holder: Keypair,
    pdas: dict,
):
    """Non-owner cannot update the supply snapshot."""
    with pytest.raises(Exception, match="UnauthorizedOwner"):
        await program.rpc["update_supply_snapshot"](
            ctx=Context(
                accounts={
                    "contract":              pdas["contract"],
                    "governance_token_mint": mint,
                    "owner":                 holder.pubkey(),
                },
                signers=[holder],
            ),
        )


# ══════════════════════════════════════════════════════════════════════════════
#  12. rent_space error paths
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_rent_space_expired_timestamp(
    program: Program,
    payer: Keypair,
    recipient: Keypair,
    mint: Pubkey,
    pdas: dict,
    token_accounts: dict,
):
    """rent_space with a past expiration_time must raise PaymentExpired."""
    state  = await program.account["ContractState"].fetch(pdas["contract"])
    fr_pda = fee_record_pda(program, payer.pubkey(), state.fee_record_count)

    with pytest.raises(Exception, match="PaymentExpired"):
        await program.rpc["rent_space"](
            recipient.pubkey(),
            int(time.time()) - 60,
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
    """rent_space with payment_amount = 0 must raise InvalidPaymentAmount."""
    state  = await program.account["ContractState"].fetch(pdas["contract"])
    fr_pda = fee_record_pda(program, payer.pubkey(), state.fee_record_count)

    with pytest.raises(Exception, match="InvalidPaymentAmount"):
        await program.rpc["rent_space"](
            recipient.pubkey(),
            int(time.time()) + 3600,
            0,
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


# ══════════════════════════════════════════════════════════════════════════════
#  13. Accumulator grows correctly across multiple finalizations
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_accumulator_grows_across_multiple_finalizations(
    program: Program,
    payer: Keypair,
    recipient: Keypair,
    mint: Pubkey,
    pdas: dict,
    token_accounts: dict,
):
    """
    Create two more rents with short expiry, finalize both, and verify
    the accumulator grows additively by exactly 2 × delta.
    """
    state_before = await program.account["ContractState"].fetch(pdas["contract"])
    acc_before   = state_before.fees_per_token_accumulated

    fr_pda_a, _ = await create_rent(
        program, payer, recipient, mint, pdas, token_accounts,
        expiration_offset=3,
    )
    fr_pda_b, _ = await create_rent(
        program, payer, recipient, mint, pdas, token_accounts,
        expiration_offset=3,
    )

    await asyncio.sleep(4)

    for fr_pda in [fr_pda_a, fr_pda_b]:
        await program.rpc["finalize_rent"](
            ctx=Context(
                accounts={
                    "contract":   pdas["contract"],
                    "fee_vault":  pdas["fee_vault"],
                    "fee_record": fr_pda,
                    "caller":     payer.pubkey(),
                },
                signers=[payer],
            ),
        )

    state_after    = await program.account["ContractState"].fetch(pdas["contract"])
    expected_delta = 2 * ((EXPECTED_GOV_FEE * PRECISION) // SUPPLY)
    assert state_after.fees_per_token_accumulated == acc_before + expected_delta, \
        "Accumulator did not grow correctly across two finalizations"



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
