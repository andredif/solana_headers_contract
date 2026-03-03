#!/usr/bin/env python3
"""
airdrop_governance_tokens.py — Mint governance tokens to a list of holder addresses.

For each recipient the script will:
  1. Derive the Associated Token Account (ATA) for the governance mint.
  2. Create the ATA on-chain if it doesn't already exist.
  3. Mint AMOUNT_PER_RECIPIENT tokens into that ATA.

This is useful for devnet / localnet testing to simulate multiple governance
holders who can later call ``claim_fees``.

──────────────────────────────────────────────────────────────────────────────
CONFIG  (edit the section below before running)
──────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

try:
    from solana.rpc.async_api import AsyncClient
    from solana.rpc.commitment import Confirmed
    from solana.rpc.types import TxOpts
    from solders.keypair import Keypair
    from solders.pubkey import Pubkey
    from spl.token.async_client import AsyncToken
    from spl.token.constants import TOKEN_PROGRAM_ID
    from spl.token.instructions import get_associated_token_address
except ImportError as exc:
    sys.exit(
        f"[ERROR] Missing dependency: {exc}\n"
        "Install with:  pip install solana solders spl-token"
    )

# ══════════════════════════════════════════════════════════════════════════════
#  ✏️  CONFIGURE THESE VALUES BEFORE RUNNING
# ══════════════════════════════════════════════════════════════════════════════

# Governance token mint created by initialize_state.py
MINT_ADDRESS = "HcVRxQMYM3WtkCmBMXyHyczNwrmEt5hSVn682yrd8ops"

# How many tokens to send to each address
AMOUNT_PER_RECIPIENT = 490_000

# Recipient wallet addresses (base58).  Add as many as you like.
RECIPIENTS: list[str] = [
    "EM5czUuM1H98PzHbzdiHaFzQ8eiGS7KW6TvxXCWqHwmf",
    "B7oMVgBaR9ZgWAkBNKPA4MCFDLkk7LE7EHxC71r5Ac6q",
    # "WalletAddress3333333333333333333333333333333",
]

# Path to the mint-authority keypair (same keypair used in initialize_state.py)
KEYPAIR_PATH = Path(__file__).parent / "id.json"

# Target network
NETWORK = "devnet"   # localnet | devnet | testnet | mainnet

# ══════════════════════════════════════════════════════════════════════════════

_NETWORK_URLS: dict[str, str] = {
    "localnet": "http://localhost:8899",
    "devnet":   "https://api.devnet.solana.com",
    "testnet":  "https://api.testnet.solana.com",
    "mainnet":  "https://api.mainnet-beta.solana.com",
}


def _load_keypair(path: Path) -> Keypair:
    if not path.exists():
        sys.exit(f"[ERROR] Keypair file not found: {path}")
    return Keypair.from_bytes(bytes(json.loads(path.read_text())))


def _rpc_url() -> str:
    url = _NETWORK_URLS.get(NETWORK)
    if url is None:
        if NETWORK.startswith("http"):
            return NETWORK
        sys.exit(f"[ERROR] Unknown network '{NETWORK}'.")
    return url


async def airdrop() -> None:
    if not RECIPIENTS:
        sys.exit(
            "[ERROR] RECIPIENTS list is empty.\n"
            "        Edit the RECIPIENTS variable at the top of this script."
        )

    rpc_url   = _rpc_url()
    authority = _load_keypair(KEYPAIR_PATH)
    mint_pk   = Pubkey.from_string(MINT_ADDRESS)

    print(f"\nGovernance token airdrop")
    print(f"  network    : {NETWORK}  ({rpc_url})")
    print(f"  mint       : {MINT_ADDRESS}")
    print(f"  authority  : {authority.pubkey()}")
    print(f"  recipients : {len(RECIPIENTS)}")
    print(f"  amount each: {AMOUNT_PER_RECIPIENT:,} tokens")
    print()

    async with AsyncClient(rpc_url, commitment=Confirmed) as client:
        token = AsyncToken(client, mint_pk, TOKEN_PROGRAM_ID, authority)

        # ── Print current supply ──────────────────────────────────────────────
        try:
            info = await token.get_mint_info()
            print(f"  current supply: {info.supply:,} tokens\n")
        except Exception as exc:
            sys.exit(
                f"[ERROR] Could not fetch mint info for {MINT_ADDRESS}.\n"
                f"        Make sure the mint exists on {NETWORK}.\n"
                f"        Detail: {exc}"
            )

        succeeded = 0
        failed    = 0

        for raw_addr in RECIPIENTS:
            raw_addr = raw_addr.strip()
            if not raw_addr:
                continue

            try:
                owner_pk = Pubkey.from_string(raw_addr)
            except Exception:
                print(f"  [SKIP] Invalid pubkey: '{raw_addr}'")
                failed += 1
                continue

            ata = get_associated_token_address(owner_pk, mint_pk)
            short = str(owner_pk)[:8] + "…"

            # ── Create ATA if it doesn't exist ────────────────────────────────
            acct_info = await client.get_account_info(ata)
            if acct_info.value is None:
                print(f"  [{short}] Creating ATA {str(ata)[:8]}…", end=" ", flush=True)
                try:
                    await token.create_associated_token_account(owner_pk)
                    print("created.")
                except Exception as exc:
                    print(f"FAILED: {exc}")
                    failed += 1
                    continue
            else:
                print(f"  [{short}] ATA {str(ata)[:8]}… already exists.")

            # ── Mint tokens ───────────────────────────────────────────────────
            print(f"  [{short}] Minting {AMOUNT_PER_RECIPIENT:,} tokens …", end=" ", flush=True)
            try:
                opts = TxOpts(skip_confirmation=False, preflight_commitment=Confirmed)
                await token.mint_to(
                    dest=ata,
                    mint_authority=authority,
                    amount=AMOUNT_PER_RECIPIENT,
                    opts=opts,
                )
                print("OK.")
                succeeded += 1
            except Exception as exc:
                print(f"FAILED: {exc}")
                failed += 1

        # ── Summary ───────────────────────────────────────────────────────────
        print(f"\n{'─'*50}")
        print(f"  ✓ succeeded : {succeeded}")
        if failed:
            print(f"  ✗ failed    : {failed}")
        total_minted = succeeded * AMOUNT_PER_RECIPIENT
        print(f"  total minted: {total_minted:,} tokens")

        try:
            info2 = await token.get_mint_info()
            print(f"  new supply  : {info2.supply:,} tokens")
        except Exception:
            pass

        print()


if __name__ == "__main__":
    asyncio.run(airdrop())
