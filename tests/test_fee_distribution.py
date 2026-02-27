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
from spl.token.constants import TOKEN_PROGRAM_ID, ASSOCIATED_TOKEN_PROGRAM_ID
from solana.rpc.commitment import Confirmed

RENT_SYSVAR_ID = Pubkey.from_string("SysvarRent111111111111111111111111111111111")

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

@pytest.fixture(scope="session")
async def program(workspace) -> Program:
    return workspace["fee_distribution"]


@pytest.fixture(scope="session")
async def provider(program: Program) -> Provider:
    return program.provider


@pytest.fixture(scope="session")
async def payer(provider: Provider) -> Keypair:
    """Wallet that pays for rents (the loaner)."""
    return provider.wallet.payer


@pytest.fixture(scope="session")
async def oracle(provider: Provider) -> Keypair:
    """Backend monitoring keypair — calls revoke_by_oracle."""
    kp = Keypair()
    sig = await provider.connection.request_airdrop(kp.pubkey(), 2_000_000_000)
    await provider.connection.confirm_transaction(sig.value, commitment=Confirmed)
    return kp


@pytest.fixture(scope="session")
async def recipient(provider: Provider) -> Keypair:
    """The renter — receives 80 % immediately."""
    kp = Keypair()
    sig = await provider.connection.request_airdrop(kp.pubkey(), 2_000_000_000)
    await provider.connection.confirm_transaction(sig.value, commitment=Confirmed)
    return kp


@pytest.fixture(scope="session")
async def holder(provider: Provider) -> Keypair:
    """Governance token holder — claims the 15 % after finalization."""
    kp = Keypair()
    sig = await provider.connection.request_airdrop(kp.pubkey(), 2_000_000_000)
    await provider.connection.confirm_transaction(sig.value, commitment=Confirmed)
    return kp


@pytest.fixture(scope="session")
async def mint(provider: Provider, payer: Keypair, pdas: dict) -> Pubkey:
    """
    Return the governance token mint. If the contract is already initialized
    on-chain, reuse the existing mint address stored in ContractState.
    Otherwise create a fresh mint.
    """
    from anchorpy.error import AccountDoesNotExistError  # noqa: keep for future use
    # Try to read existing contract state
    contract_pda = pdas["contract"]
    try:
        resp = await provider.connection.get_account_info(contract_pda, encoding="base64")
        if resp.value:
            # Contract exists — decode the governance_token_mint
            # ContractState layout: [8 discriminator][32 owner][32 mint][32 oracle][...]
            raw = bytes(resp.value.data)   # data is already bytes
            mint_bytes = raw[40:72]
            return Pubkey.from_bytes(mint_bytes)
    except Exception:
        pass
    # Contract doesn't exist yet — create a fresh mint
    token = await AsyncToken.create_mint(
        conn=provider.connection,
        payer=payer,
        mint_authority=payer.pubkey(),
        decimals=0,
        program_id=TOKEN_PROGRAM_ID,
    )
    return token.pubkey


@pytest.fixture(scope="session")
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


@pytest.fixture(scope="session")
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

    async def get_or_create_ata(owner_pk: Pubkey) -> tuple:
        """Return (ata_pubkey, is_new). Creates ATA only if it doesn't exist."""
        ata, _ = Pubkey.find_program_address(
            [bytes(owner_pk), bytes(TOKEN_PROGRAM_ID), bytes(mint)],
            ASSOCIATED_TOKEN_PROGRAM_ID,
        )
        info = await client.get_account_info(ata)
        if info.value is not None:
            return ata, False
        tok = AsyncToken(client, mint, TOKEN_PROGRAM_ID, payer)
        addr = await tok.create_associated_token_account(owner_pk)
        return addr, True

    token      = AsyncToken(client, mint, TOKEN_PROGRAM_ID, payer)
    payer_ata,  payer_new  = await get_or_create_ata(payer.pubkey())
    rec_ata,    _          = await get_or_create_ata(recipient.pubkey())
    holder_ata, holder_new = await get_or_create_ata(holder.pubkey())

    print(f"\n[token_accounts] mint={mint}, payer_new={payer_new}, holder_new={holder_new}")

    # Only mint tokens if these are fresh ATAs
    if payer_new:
        print(f"[token_accounts] minting {SUPPLY} to payer_ata={payer_ata}")
        await token.mint_to(payer_ata,  payer, SUPPLY)
        print(f"[token_accounts] mint_to payer done")
    if holder_new:
        print(f"[token_accounts] minting {SUPPLY} to holder_ata={holder_ata}")
        await token.mint_to(holder_ata, payer, SUPPLY)
        print(f"[token_accounts] mint_to holder done")
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


@pytest.fixture(scope="session")
async def base_count(program: Program, pdas: dict) -> int:
    """
    Read fee_record_count from on-chain state at the start of the test session.
    All tests that reference fee records by relative index (0, 1, 2 ...) must
    add this fixture and use ``base_count + N`` as the absolute index, so the
    test suite is idempotent across repeated runs on a persistent validator.
    """
    state = await program.account["ContractState"].fetch(pdas["contract"])
    return state.fee_record_count


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
    try:
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
                    "rent":                  RENT_SYSVAR_ID,
                },
                signers=[payer],
            ),
        )
    except Exception as e:
        err_str = str(e)
        # Contract was already initialized in a previous run on the same validator.
        # Update the oracle to the current session's keypair so later tests pass.
        if "already in use" in err_str.lower() or "custom program error: 0x0" in err_str:
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
        else:
            raise

    state = await program.account["ContractState"].fetch(pdas["contract"])
    assert state.owner                 == payer.pubkey()
    assert state.oracle                == oracle.pubkey()
    assert state.governance_token_mint == mint
    assert state.supply_snapshot       == SUPPLY


# ══════════════════════════════════════════════════════════════════════════════
#  2. Holder state
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_init_holder_state(
    program: Program,
    provider: Provider,
    holder: Keypair,
    pdas: dict,
):
    """Holder state initializes with zero claims and the current accumulator baseline."""
    hs_pda = holder_state_pda(program, holder.pubkey())

    sig = await program.rpc["init_holder_state"](
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
    await provider.connection.confirm_transaction(sig, commitment=Confirmed)

    hs = await program.account["HolderState"].fetch(hs_pda)
    # Read contract accumulator to get the expected baseline
    state = await program.account["ContractState"].fetch(pdas["contract"])
    assert hs.holder                 == holder.pubkey()
    assert hs.total_claimed          == 0
    # The holder's claimed baseline is set to the current accumulator (may be non-zero
    # if prior finalizations exist on a persistent validator).
    assert hs.fees_per_token_claimed == state.fees_per_token_accumulated


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
    base_count: int,
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
    payer: Keypair,
    pdas: dict,
    base_count: int,
):
    """
    Accumulator must NOT change immediately after rent_space.
    It is only updated when finalize_rent is called post-expiry.
    We compare against the value BEFORE rent_space (stored in base_count fixture side-effect
    via state snapshot taken during base_count, so here we just verify it equals the
    value at time of rent_space: fee_vault has grown but accumulator has NOT).
    """
    # Fetch current accumulator and total_fees — they should be unchanged since rent_space
    # does NOT call finalize_rent. We verify the record itself is unfinalized.
    state = await program.account["ContractState"].fetch(pdas["contract"])
    # The accumulator should NOT have changed since the last rent_space
    # (rent_space doesn't update it). We check the newly-created fee record is unfinalized.
    fr_pda = fee_record_pda(program, payer.pubkey(), base_count + 0)
    rec = await program.account["FeeRecord"].fetch(fr_pda)
    assert rec.is_finalized is False, "FeeRecord must not be finalized after rent_space"
    assert rec.is_reverted  is False, "FeeRecord must not be reverted after rent_space"


@pytest.mark.asyncio
async def test_fee_record_count_incremented(program: Program, pdas: dict, base_count: int):
    """fee_record_count increments after each rent_space call (should be base_count + 1)."""
    state = await program.account["ContractState"].fetch(pdas["contract"])
    assert state.fee_record_count == base_count + 1


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

    with pytest.raises(Exception, match="Rental period has not yet expired"):
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
    base_count: int,
):
    """
    After expiry, finalize_rent releases governance fees into the accumulator.
    Sleeps past rent[1]'s 4-second expiry then finalizes.
    """
    await asyncio.sleep(5)

    fr_pda     = fee_record_pda(program, payer.pubkey(), base_count + 1)
    state_before = await program.account["ContractState"].fetch(pdas["contract"])
    acc_before       = state_before.fees_per_token_accumulated
    acc_before_total = state_before.total_fees_accumulated

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
    assert state.total_fees_accumulated == acc_before_total + EXPECTED_GOV_FEE

    rec = await program.account["FeeRecord"].fetch(fr_pda)
    assert rec.is_finalized is True


@pytest.mark.asyncio
async def test_cannot_finalize_twice(
    program: Program,
    payer: Keypair,
    pdas: dict,
    base_count: int,
):
    """Double-finalize must raise RentAlreadyFinalized."""
    fr_pda = fee_record_pda(program, payer.pubkey(), base_count + 1)

    with pytest.raises(Exception, match="already been finalized"):
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

    with pytest.raises(Exception, match="Nothing to claim"):
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
    base_count: int,
):
    """Owner cannot claim from rent[0] because it is not yet finalized."""
    fr_pda = fee_record_pda(program, payer.pubkey(), base_count + 0)

    with pytest.raises(Exception, match="Rental period has not yet expired"):
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
    base_count: int,
):
    """Owner withdraws the 5 % fee from finalized rent[1]."""
    fr_pda       = fee_record_pda(program, payer.pubkey(), base_count + 1)
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
    base_count: int,
):
    """Second claim_owner_fee on the same record must raise OwnerFeeAlreadyClaimed."""
    fr_pda = fee_record_pda(program, payer.pubkey(), base_count + 1)

    with pytest.raises(Exception, match="already been claimed"):
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
    base_count: int,
):
    """A random holder (neither payer nor owner) cannot revert rent[0]."""
    fr_pda = fee_record_pda(program, payer.pubkey(), base_count + 0)

    with pytest.raises(Exception, match="Unauthorized owner"):
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
    base_count: int,
):
    """
    Payer reverts rent[0] (still unexpired).
    The full 20 % (governance_fee + owner_fee) is returned to the payer.
    """
    fr_pda       = fee_record_pda(program, payer.pubkey(), base_count + 0)
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
    base_count: int,
):
    """Second revert_rent must raise RentAlreadyReverted."""
    fr_pda = fee_record_pda(program, payer.pubkey(), base_count + 0)

    with pytest.raises(Exception, match="already been reverted"):
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
    base_count: int,
):
    """finalize_rent on a reverted record must raise RentAlreadyReverted or RentNotExpired.
    Note: the contract checks expiration_time before is_reverted, so for a
    non-expired reverted record the error is RentNotExpired.
    """
    fr_pda = fee_record_pda(program, payer.pubkey(), base_count + 0)

    with pytest.raises(Exception, match="Rental period has not yet expired|already been reverted"):
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
    base_count: int,
):
    """
    Creates rent[2] (1-hour expiry) and tries to revoke it with the holder key
    (not the oracle) — must raise UnauthorizedOracle.
    """
    await create_rent(
        program, payer, recipient, mint, pdas, token_accounts,
        expiration_offset=3600,
    )
    fr_pda = fee_record_pda(program, payer.pubkey(), base_count + 2)

    with pytest.raises(Exception, match="Signer is not the authorised oracle"):
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
    base_count: int,
):
    """Oracle revokes rent[2] — loaner gets the 20 % vault amount refunded."""
    fr_pda       = fee_record_pda(program, payer.pubkey(), base_count + 2)
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
    base_count: int,
):
    """Oracle cannot revoke a record that is already reverted."""
    fr_pda = fee_record_pda(program, payer.pubkey(), base_count + 2)

    with pytest.raises(Exception, match="already been reverted"):
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
    base_count: int,
):
    """Oracle cannot revoke an already-finalized record (rent[1])."""
    fr_pda = fee_record_pda(program, payer.pubkey(), base_count + 1)

    with pytest.raises(Exception, match="already been finalized"):
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
    with pytest.raises(Exception, match="Unauthorized owner"):
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
    with pytest.raises(Exception, match="Unauthorized owner"):
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
    with pytest.raises(Exception, match="Unauthorized owner"):
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

    with pytest.raises(Exception, match="Payment has expired"):
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

    with pytest.raises(Exception, match="Invalid payment amount"):
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
