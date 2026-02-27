"""
Pytest configuration for localnet integration testing of the fee_distribution program.

Architecture
------------
_program_info  (session-scoped, sync)
    • Runs ``anchor build`` once for the whole test session.
    • Deploys the .so to the running local validator via ``solana program deploy``.
    • Returns (Idl, program_id, wallet_bytes) — no async objects.

workspace  (module-scoped, async)
    • Runs inside the test module's event loop.
    • Creates an AsyncClient, airdrops SOL to the payer wallet,
      then yields {"fee_distribution": Program}.
    • Closes the async client on teardown.

Notes
-----
Anchor 0.30+ generates IDL v2 format.  anchorpy_core 0.2.0 still expects the
older v1 format.  ``_convert_idl_v2_to_v1()`` bridges the gap.
"""

import json
import subprocess
from pathlib import Path

import pytest
import pytest_asyncio
from anchorpy import Idl, Program, Provider, Wallet
from solana.rpc.async_api import AsyncClient
from solana.rpc.commitment import Confirmed
from solders.keypair import Keypair

# ── paths ─────────────────────────────────────────────────────────────────────
_ROOT        = Path(__file__).parent.parent
_DEPLOY_KP   = _ROOT / "target" / "deploy" / "fee_distribution-keypair.json"
_SO_FILE     = _ROOT / "target" / "deploy" / "fee_distribution.so"
_IDL_FILE    = _ROOT / "target" / "idl"    / "fee_distribution.json"
_WALLET_FILE = _ROOT / "id.json"
_RPC_URL     = "http://localhost:8899"


# ══════════════════════════════════════════════════════════════════════════════
#  IDL format converter (Anchor 0.30+ v2 → anchorpy_core v1)
# ══════════════════════════════════════════════════════════════════════════════

def _convert_idl_v2_to_v1(idl_v2: dict) -> dict:
    """
    Convert the Anchor 0.30+ IDL format (v2/spec 0.1.0) to the legacy v1
    format that anchorpy_core 0.2.x understands.

    Key differences handled:
    • Top-level ``address``/``metadata`` → ``version``/``name``
    • Instruction accounts: ``writable``/``signer`` → ``isMut``/``isSigner``
    • Account type definitions live in ``types``; rebuild ``accounts`` from them
    • Event fields need a boolean ``index`` field
    • Simple type ``"pubkey"`` → ``"publicKey"``
    """
    metadata      = idl_v2.get("metadata", {})
    types_by_name = {t["name"]: t for t in idl_v2.get("types", [])}
    account_names = {a["name"] for a in idl_v2.get("accounts", [])}
    event_names   = {e["name"] for e in idl_v2.get("events",   [])}

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
        return [{"name": f["name"], "type": _ty(f["type"]), "index": f.get("index", False)}
                for f in (fields or [])]

    def _type_def(td):
        ty   = td.get("type", {})
        kind = ty.get("kind", "struct")
        if kind == "struct":
            return {"name": td["name"], "type": {"kind": "struct", "fields": _fields(ty.get("fields", []))}}
        if kind == "enum":
            return {"name": td["name"], "type": {"kind": "enum", "variants": ty.get("variants", [])}}
        return {"name": td["name"], "type": ty}

    def _acct_item(acc):
        if "accounts" in acc:                              # nested group
            return {"name": acc["name"], "accounts": [_acct_item(a) for a in acc["accounts"]]}
        r = {
            "name":     acc["name"],
            "isMut":    acc.get("writable", False),
            "isSigner": acc.get("signer",   False),
        }
        if acc.get("isOptional"): r["isOptional"] = True
        if "docs" in acc:         r["docs"]       = acc["docs"]
        return r

    def _instr(instr):
        return {
            "name": instr["name"],
            **({"docs": instr["docs"]} if "docs" in instr else {}),
            "accounts": [_acct_item(a) for a in instr.get("accounts", [])],
            "args":     [{"name": a["name"], "type": _ty(a["type"])}
                         for a in instr.get("args", [])],
        }

    old_accounts = [_type_def(types_by_name[n])
                    for acc in idl_v2.get("accounts", [])
                    for n in (acc["name"],)
                    if n in types_by_name]

    old_types    = [_type_def(t)
                    for t in idl_v2.get("types", [])
                    if t["name"] not in account_names and t["name"] not in event_names]

    old_events   = [{"name": e["name"],
                     "fields": _event_fields(types_by_name[e["name"]].get("type", {}).get("fields", []))}
                    for e in idl_v2.get("events", [])
                    if e["name"] in types_by_name]

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
#  Session fixture — build & deploy (sync, runs once per test session)
# ══════════════════════════════════════════════════════════════════════════════

@pytest.fixture(scope="session")
def _program_info():
    """
    Build the Anchor program, deploy to localnet, and return
    (Idl, program_id: Pubkey, wallet_bytes: bytes).
    """

    # ── 1. Build ──────────────────────────────────────────────────────────────
    print("\n[setup] Running anchor build …")
    build = subprocess.run(
        ["anchor", "build"],
        cwd=_ROOT,
        capture_output=True,
        text=True,
        timeout=300,
    )
    if build.returncode != 0:
        raise RuntimeError(
            f"anchor build failed (exit {build.returncode}):\n"
            f"STDOUT:{build.stdout}\nSTDERR:{build.stderr}"
        )
    print("[setup] Build OK.")

    # ── 2. Program ID ─────────────────────────────────────────────────────────
    with open(_DEPLOY_KP) as fh:
        deploy_data = json.load(fh)
    program_kp = Keypair.from_bytes(bytes(deploy_data))
    program_id = program_kp.pubkey()
    print(f"[setup] Program ID: {program_id}")

    # ── 3. Deploy ─────────────────────────────────────────────────────────────
    print("[setup] Deploying to localnet …")
    deploy = subprocess.run(
        [
            "solana", "program", "deploy",
            str(_SO_FILE),
            "--program-id", str(_DEPLOY_KP),
            "--url",         _RPC_URL,
            "--keypair",     str(_WALLET_FILE),
        ],
        cwd=_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if deploy.returncode != 0:
        raise RuntimeError(
            f"solana program deploy failed (exit {deploy.returncode}):\n"
            f"STDOUT:{deploy.stdout}\nSTDERR:{deploy.stderr}"
        )
    print(f"[setup] Deploy OK: {deploy.stdout.strip()}")

    # ── 4. Load & convert IDL ─────────────────────────────────────────────────
    idl_v2  = json.loads(_IDL_FILE.read_text())
    idl_v1  = _convert_idl_v2_to_v1(idl_v2)
    idl     = Idl.from_json(json.dumps(idl_v1))

    # ── 5. Wallet bytes ───────────────────────────────────────────────────────
    with open(_WALLET_FILE) as fh:
        wallet_data = json.load(fh)
    wallet_bytes = bytes(wallet_data)

    return idl, program_id, wallet_bytes


# ══════════════════════════════════════════════════════════════════════════════
#  Module fixture — Provider + Program (async, once per test module)
# ══════════════════════════════════════════════════════════════════════════════

@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def workspace(_program_info):
    """
    Create an AsyncClient + Provider inside the test module's event loop,
    airdrop SOL to the payer wallet, then yield {"fee_distribution": Program}.
    Closes the client on teardown.
    """
    idl, program_id, wallet_bytes = _program_info

    wallet_keypair = Keypair.from_bytes(wallet_bytes)
    client = AsyncClient(_RPC_URL, commitment=Confirmed)

    # Airdrop so there is plenty of SOL for all transactions in this module.
    try:
        resp = await client.request_airdrop(wallet_keypair.pubkey(), 10_000_000_000)
        await client.confirm_transaction(resp.value, commitment=Confirmed)
        print(f"\n[workspace] Airdropped 10 SOL to {wallet_keypair.pubkey()}")
    except Exception as exc:
        print(f"\n[workspace] Airdrop skipped: {exc}")

    provider = Provider(client, Wallet(wallet_keypair))
    program  = Program(idl, program_id, provider)

    yield {"fee_distribution": program}

    await client.close()
