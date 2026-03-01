#!/usr/bin/env python3
"""
initialize_state.py — Deploy and initialize the fee_distribution Solana program.

Performs three steps in sequence (each can be skipped via flags):
  1. anchor build               — compile the Rust program
  2. solana program deploy      — upload the .so to the chosen network
  3. initialize RPC call        — set owner/oracle/mint/supply on the contract PDA

Usage:
    python initialize_state.py <keypair_path> [network] [options]

Arguments:
    keypair_path         Path to the Solana keypair JSON file.
                         Acts as the transaction fee payer, contract owner, and
                         the signer for the initialize instruction.
    network              localnet | devnet | testnet | mainnet  (default: localnet)

Options:
    --oracle <pubkey>        Oracle public key (default: same as the signer/owner)
    --mint <pubkey>          Existing governance token mint public key.
                             If omitted a new SPL mint is created on-chain.
    --supply-snapshot <n>    Token supply snapshot stored in the contract
                             (default: 1_000_000).
    --no-build               Skip `anchor build` (use the existing .so file).
    --no-deploy              Skip `solana program deploy` (program is already on-chain).

Examples:
    # Full flow on localnet with a fresh mint:
    python initialize_state.py id.json

    # Deploy + init on devnet, reusing an existing mint:
    python initialize_state.py id.json devnet --mint 9aBc...XYZ

    # Init only (already deployed) with a separate oracle key:
    python initialize_state.py id.json localnet --no-build --no-deploy \\
        --oracle 4DeF...RST

    # Devnet, skip build (artifact already compiled):
    python initialize_state.py id.json devnet --no-build
"""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
from pathlib import Path

# ── third-party (install via requirements.txt) ────────────────────────────────
try:
    from anchorpy import Idl, Program, Provider, Wallet, Context
    from solana.rpc.async_api import AsyncClient
    from solana.rpc.commitment import Confirmed
    from solders.keypair import Keypair
    from solders.pubkey import Pubkey
    from solders.system_program import ID as SYS_PROGRAM_ID
    from spl.token.async_client import AsyncToken
    from spl.token.constants import TOKEN_PROGRAM_ID
except ImportError as exc:
    sys.exit(
        f"[ERROR] Missing dependency: {exc}\n"
        "Install with:  pip install anchorpy solana solders spl-token"
    )

# ── Repository paths ──────────────────────────────────────────────────────────
_ROOT      = Path(__file__).parent
_DEPLOY_KP = _ROOT / "target" / "deploy" / "fee_distribution-keypair.json"
_SO_FILE   = _ROOT / "target" / "deploy" / "fee_distribution.so"
_IDL_FILE  = _ROOT / "target" / "idl" / "fee_distribution.json"

# ── Network presets ───────────────────────────────────────────────────────────
_NETWORK_PRESETS: dict[str, str] = {
    "localnet": "http://localhost:8899",
    "devnet":   "https://api.devnet.solana.com",
    "testnet":  "https://api.testnet.solana.com",
    "mainnet":  "https://api.mainnet-beta.solana.com",
}


# ══════════════════════════════════════════════════════════════════════════════
#  IDL v2 → v1 converter  (identical to the one used in tests/conftest.py)
# ══════════════════════════════════════════════════════════════════════════════

def _convert_idl_v2_to_v1(idl_v2: dict) -> dict:
    """Convert Anchor 0.30+ IDL (v2 / spec 0.1.0) to the legacy v1 format
    that anchorpy_core 0.2.x understands."""

    metadata      = idl_v2.get("metadata", {})
    types_by_name = {t["name"]: t for t in idl_v2.get("types", [])}
    account_names = {a["name"] for a in idl_v2.get("accounts", [])}
    event_names   = {e["name"] for e in idl_v2.get("events", [])}

    def _ty(ty):
        if isinstance(ty, str):
            return "publicKey" if ty == "pubkey" else ty
        if isinstance(ty, dict):
            if "defined" in ty:
                n = ty["defined"]
                return {"defined": n["name"] if isinstance(n, dict) else n}
            for key in ("vec", "option"):
                if key in ty:
                    return {key: _ty(ty[key])}
            if "array" in ty:
                return {"array": [_ty(ty["array"][0]), ty["array"][1]]}
        return ty

    def _fields(fields):
        return [{"name": f["name"], "type": _ty(f["type"])} for f in (fields or [])]

    def _event_fields(fields):
        return [
            {"name": f["name"], "type": _ty(f["type"]), "index": f.get("index", False)}
            for f in (fields or [])
        ]

    def _type_def(td):
        ty   = td.get("type", {})
        kind = ty.get("kind", "struct")
        if kind == "struct":
            return {
                "name": td["name"],
                "type": {"kind": "struct", "fields": _fields(ty.get("fields", []))},
            }
        if kind == "enum":
            return {
                "name": td["name"],
                "type": {"kind": "enum", "variants": ty.get("variants", [])},
            }
        return {"name": td["name"], "type": ty}

    def _acct_item(acc):
        if "accounts" in acc:
            return {"name": acc["name"], "accounts": [_acct_item(a) for a in acc["accounts"]]}
        r = {
            "name":     acc["name"],
            "isMut":    acc.get("writable", False),
            "isSigner": acc.get("signer",   False),
        }
        if acc.get("isOptional"):
            r["isOptional"] = True
        if "docs" in acc:
            r["docs"] = acc["docs"]
        return r

    def _instr(instr):
        return {
            "name":     instr["name"],
            **({"docs": instr["docs"]} if "docs" in instr else {}),
            "accounts": [_acct_item(a) for a in instr.get("accounts", [])],
            "args":     [
                {"name": a["name"], "type": _ty(a["type"])}
                for a in instr.get("args", [])
            ],
        }

    old_accounts = [
        _type_def(types_by_name[n])
        for acc in idl_v2.get("accounts", [])
        for n in (acc["name"],)
        if n in types_by_name
    ]

    old_types = [
        _type_def(t)
        for t in idl_v2.get("types", [])
        if t["name"] not in account_names and t["name"] not in event_names
    ]

    old_events = [
        {
            "name":   e["name"],
            "fields": _event_fields(
                types_by_name[e["name"]].get("type", {}).get("fields", [])
            ),
        }
        for e in idl_v2.get("events", [])
        if e["name"] in types_by_name
    ]

    result = {
        "version":      metadata.get("version", "0.0.0"),
        "name":         metadata.get("name",    "unknown"),
        "instructions": [_instr(i) for i in idl_v2.get("instructions", [])],
        "accounts":     old_accounts,
        "types":        old_types,
        "errors":       idl_v2.get("errors", []),
    }
    if old_events:
        result["events"] = old_events
    return result


# ══════════════════════════════════════════════════════════════════════════════
#  Helper utilities
# ══════════════════════════════════════════════════════════════════════════════

def _get_rpc_url(network: str) -> str:
    url = _NETWORK_PRESETS.get(network)
    if url is not None:
        return url
    if network.startswith("http"):
        return network  # raw URL accepted as-is
    sys.exit(
        f"[ERROR] Unknown network '{network}'.\n"
        f"        Valid options: {', '.join(_NETWORK_PRESETS)}"
    )


def _load_keypair(path: str | Path) -> Keypair:
    path = Path(path).expanduser()
    if not path.exists():
        sys.exit(f"[ERROR] Keypair file not found: {path}")
    data = json.loads(path.read_text())
    return Keypair.from_bytes(bytes(data))


def _step(msg: str) -> None:
    print(f"\n{'='*60}")
    print(f"  {msg}")
    print(f"{'='*60}")


# ══════════════════════════════════════════════════════════════════════════════
#  Step 1 — anchor build
# ══════════════════════════════════════════════════════════════════════════════

def do_build() -> None:
    _step("Step 1/3 — anchor build")
    result = subprocess.run(
        ["anchor", "build"],
        cwd=_ROOT,
        capture_output=False,   # stream output directly to terminal
        text=True,
        timeout=300,
    )
    if result.returncode != 0:
        sys.exit(f"[ERROR] anchor build failed (exit {result.returncode})")
    print("[OK] Build succeeded.")


# ══════════════════════════════════════════════════════════════════════════════
#  Step 2 — solana program deploy
# ══════════════════════════════════════════════════════════════════════════════

def do_deploy(rpc_url: str, wallet_path: str | Path) -> str:
    _step(f"Step 2/3 — solana program deploy → {rpc_url}")

    if not _SO_FILE.exists():
        sys.exit(
            f"[ERROR] Compiled program not found: {_SO_FILE}\n"
            "        Run anchor build first (or pass --no-build if already built)."
        )
    if not _DEPLOY_KP.exists():
        sys.exit(f"[ERROR] Deploy keypair not found: {_DEPLOY_KP}")

    result = subprocess.run(
        [
            "solana", "program", "deploy",
            str(_SO_FILE),
            "--program-id", str(_DEPLOY_KP),
            "--url",        rpc_url,
            "--keypair",    str(wallet_path),
        ],
        cwd=_ROOT,
        capture_output=False,
        text=True,
        timeout=180,
    )
    if result.returncode != 0:
        sys.exit(f"[ERROR] solana program deploy failed (exit {result.returncode})")

    # Read program ID from the deploy keypair
    data = json.loads(_DEPLOY_KP.read_text())
    program_id = str(Keypair.from_bytes(bytes(data)).pubkey())
    print(f"[OK] Program deployed.  Program ID: {program_id}")
    return program_id


# ══════════════════════════════════════════════════════════════════════════════
#  Step 3 — initialize the contract (async)
# ══════════════════════════════════════════════════════════════════════════════

async def do_initialize(
    rpc_url:         str,
    signer:          Keypair,
    oracle_pubkey:   Pubkey,
    mint_pubkey:     Pubkey | None,
    supply_snapshot: int,
    is_localnet:     bool,
) -> None:
    _step("Step 3/3 — initialize contract on-chain")

    # ── Load & convert IDL ────────────────────────────────────────────────────
    if not _IDL_FILE.exists():
        sys.exit(
            f"[ERROR] IDL file not found: {_IDL_FILE}\n"
            "        Run anchor build to generate it."
        )
    idl_v2 = json.loads(_IDL_FILE.read_text())
    idl_v1 = _convert_idl_v2_to_v1(idl_v2)
    idl    = Idl.from_json(json.dumps(idl_v1))

    # ── Program ID from deploy keypair ─────────────────────────────────────────
    if not _DEPLOY_KP.exists():
        sys.exit(f"[ERROR] Deploy keypair not found: {_DEPLOY_KP}")
    deploy_data = json.loads(_DEPLOY_KP.read_text())
    program_id  = Keypair.from_bytes(bytes(deploy_data)).pubkey()

    # ── Provider & Program ─────────────────────────────────────────────────────
    client   = AsyncClient(rpc_url, commitment=Confirmed)
    provider = Provider(client, Wallet(signer))
    program  = Program(idl, program_id, provider)

    try:
        # ── Airdrop on localnet ────────────────────────────────────────────────
        if is_localnet:
            print(f"[info] Airdropping 10 SOL to {signer.pubkey()} on localnet …")
            resp = await client.request_airdrop(signer.pubkey(), 10_000_000_000)
            await client.confirm_transaction(resp.value, commitment=Confirmed)
            print("[info] Airdrop confirmed.")
        else:
            bal = (await client.get_balance(signer.pubkey())).value
            print(f"[info] Signer balance: {bal / 1e9:.4f} SOL")
            if bal < 50_000_000:
                print(
                    "[WARN] Signer balance is very low (< 0.05 SOL).\n"
                    "       Fund the wallet before running on a live network."
                )

        # ── Create mint if not provided ────────────────────────────────────────
        if mint_pubkey is None:
            print("[info] No --mint provided — creating a new SPL governance token mint …")
            token = await AsyncToken.create_mint(
                conn=provider.connection,
                payer=signer,
                mint_authority=signer.pubkey(),
                decimals=0,
                program_id=TOKEN_PROGRAM_ID,
            )
            mint_pubkey = token.pubkey
            print(f"[info] New mint created: {mint_pubkey}")
        else:
            print(f"[info] Using existing mint: {mint_pubkey}")

        # ── Derive PDAs ────────────────────────────────────────────────────────
        contract_pda, _ = Pubkey.find_program_address([b"contract"], program_id)
        fee_vault_pda, _ = Pubkey.find_program_address(
            [b"fee_vault", bytes(contract_pda)], program_id
        )
        print(f"[info] contract PDA : {contract_pda}")
        print(f"[info] fee_vault PDA: {fee_vault_pda}")

        # ── Check if already initialized ──────────────────────────────────────
        try:
            existing = await program.account["ContractState"].fetch(contract_pda)
            print(
                f"\n[WARN] Contract is ALREADY initialized on {rpc_url}.\n"
                f"       owner  = {existing.owner}\n"
                f"       oracle = {existing.oracle}\n"
                f"       mint   = {existing.governance_token_mint}\n"
                "\n       Nothing was changed.  "
                "Pass --no-deploy and skip this script if re-initialization is not intended."
            )
            return
        except Exception:
            pass  # account doesn't exist yet → proceed

        # ── Call initialize ────────────────────────────────────────────────────
        print(
            f"\n[info] Calling initialize …\n"
            f"       owner           = {signer.pubkey()}\n"
            f"       oracle          = {oracle_pubkey}\n"
            f"       mint            = {mint_pubkey}\n"
            f"       supply_snapshot = {supply_snapshot}"
        )
        sig = await program.rpc["initialize"](
            signer.pubkey(),   # owner
            oracle_pubkey,     # oracle
            supply_snapshot,   # supply_snapshot (u64)
            ctx=Context(
                accounts={
                    "contract":              contract_pda,
                    "governance_token_mint": mint_pubkey,
                    "fee_vault":             fee_vault_pda,
                    "signer":                signer.pubkey(),
                    "system_program":        SYS_PROGRAM_ID,
                },
                signers=[signer],
            ),
        )
        await client.confirm_transaction(sig, commitment=Confirmed)
        print(f"[OK] initialize confirmed.  Tx: {sig}")

        # ── Verify on-chain state ──────────────────────────────────────────────
        state = await program.account["ContractState"].fetch(contract_pda)
        assert state.owner                 == signer.pubkey(),    "owner mismatch"
        assert state.oracle                == oracle_pubkey,      "oracle mismatch"
        assert state.governance_token_mint == mint_pubkey,        "mint mismatch"
        assert state.supply_snapshot       == supply_snapshot,    "supply_snapshot mismatch"
        print(
            "\n[OK] Contract state verified:\n"
            f"     owner           = {state.owner}\n"
            f"     oracle          = {state.oracle}\n"
            f"     mint            = {state.governance_token_mint}\n"
            f"     supply_snapshot = {state.supply_snapshot}\n"
            f"     fee_vault PDA   = {fee_vault_pda}\n"
            f"     program ID      = {program_id}"
        )

    finally:
        await client.close()


# ══════════════════════════════════════════════════════════════════════════════
#  CLI entry point
# ══════════════════════════════════════════════════════════════════════════════

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Deploy and initialize the fee_distribution Solana program.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "keypair",
        help="Path to the Solana keypair JSON file (payer / signer / owner).",
    )
    parser.add_argument(
        "network",
        nargs="?",
        default="localnet",
        help="Target network: localnet | devnet | testnet | mainnet  (default: localnet).",
    )
    parser.add_argument(
        "--oracle",
        metavar="PUBKEY",
        help="Oracle public key.  Defaults to the signer's key.",
    )
    parser.add_argument(
        "--mint",
        metavar="PUBKEY",
        help="Existing governance token mint.  A new mint is created if omitted.",
    )
    parser.add_argument(
        "--supply-snapshot",
        metavar="N",
        type=int,
        default=1_000_000,
        help="Token supply snapshot stored in the contract (default: 1_000_000).",
    )
    parser.add_argument(
        "--no-build",
        action="store_true",
        help="Skip `anchor build` and use the existing compiled artifact.",
    )
    parser.add_argument(
        "--no-deploy",
        action="store_true",
        help="Skip `solana program deploy` and use the already-deployed program.",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()

    rpc_url    = _get_rpc_url(args.network)
    signer     = _load_keypair(args.keypair)
    is_local   = args.network == "localnet" or "localhost" in rpc_url

    print(f"\nfee_distribution — deploy & initialize")
    print(f"  network  : {args.network}  ({rpc_url})")
    print(f"  signer   : {signer.pubkey()}")
    print(f"  keypair  : {args.keypair}")

    # ── Resolve oracle ────────────────────────────────────────────────────────
    oracle_pubkey: Pubkey
    if args.oracle:
        try:
            oracle_pubkey = Pubkey.from_string(args.oracle)
        except Exception:
            sys.exit(f"[ERROR] Invalid oracle public key: '{args.oracle}'")
    else:
        oracle_pubkey = signer.pubkey()
        print(f"  oracle   : {oracle_pubkey}  (defaulting to signer)")

    # ── Resolve mint ──────────────────────────────────────────────────────────
    mint_pubkey: Pubkey | None = None
    if args.mint:
        try:
            mint_pubkey = Pubkey.from_string(args.mint)
        except Exception:
            sys.exit(f"[ERROR] Invalid mint public key: '{args.mint}'")

    # ── Step 1: build ─────────────────────────────────────────────────────────
    if not args.no_build:
        do_build()
    else:
        print("\n[skip] anchor build  (--no-build)")

    # ── Step 2: deploy ────────────────────────────────────────────────────────
    if not args.no_deploy:
        do_deploy(rpc_url, args.keypair)
    else:
        print("\n[skip] solana program deploy  (--no-deploy)")

    # ── Step 3: initialize ────────────────────────────────────────────────────
    asyncio.run(
        do_initialize(
            rpc_url=rpc_url,
            signer=signer,
            oracle_pubkey=oracle_pubkey,
            mint_pubkey=mint_pubkey,
            supply_snapshot=args.supply_snapshot,
            is_localnet=is_local,
        )
    )

    print("\n✓ Done.\n")


if __name__ == "__main__":
    main()
