"""
Pytest configuration and fixtures for contract testing

This module provides shared fixtures and configuration for all tests.
"""

import pytest
import json
import subprocess
import os
from pathlib import Path
from solders.pubkey import Pubkey
from anchorpy import Program, Provider, Wallet, Idl
from solana.rpc.async_api import AsyncClient
from solana.rpc.commitment import Confirmed


@pytest.fixture
def owner_address():
    """Fixture providing a test owner address"""
    return Pubkey.from_string("11111111111111111111111111111112")


@pytest.fixture
def governance_token_mint():
    """Fixture providing a test governance token mint address"""
    return Pubkey.from_string("11111111111111111111111111111113")


@pytest.fixture
def test_recipient():
    """Fixture providing a test recipient address"""
    return Pubkey.from_string("11111111111111111111111111111114")


@pytest.fixture
def test_payer():
    """Fixture providing a test payer address"""
    return Pubkey.from_string("11111111111111111111111111111115")


@pytest.fixture
def contract_config(owner_address, governance_token_mint):
    """Fixture providing contract configuration"""
    return {
        "owner": owner_address,
        "governance_token_mint": governance_token_mint,
    }


@pytest.fixture
def payment_data(test_recipient):
    """Fixture providing test payment data"""
    return {
        "recipient": test_recipient,
        "expiration_time": 1704067200,
        "payment_amount": 1000000,  # 1 SOL in lamports
    }


@pytest.fixture(scope="session")
def workspace():
    """Custom workspace fixture that loads the Anchor program without xprocess dependency.
    
    This fixture builds the Anchor project and loads the IDL, returning a dictionary
    with program names as keys and Program objects as values.
    """
    # Get workspace root (parent of tests directory)
    workspace_root = Path(__file__).parent.parent
    
    # Build the Anchor project
    build_cmd = "anchor build"
    try:
        result = subprocess.run(
            build_cmd,
            shell=True,
            cwd=workspace_root,
            capture_output=True,
            text=True,
            timeout=300,
        )
        if result.returncode != 0:
            print(f"Build stderr: {result.stderr}")
            raise RuntimeError(f"Anchor build failed: {result.stderr}")
    except subprocess.TimeoutExpired:
        raise RuntimeError("Anchor build timed out after 300 seconds")
    except Exception as e:
        raise RuntimeError(f"Failed to build Anchor project: {e}")
    
    # Load the IDL
    idl_path = workspace_root / "target" / "idl" / "fee_distribution.json"
    if not idl_path.exists():
        raise FileNotFoundError(f"IDL not found at {idl_path}")
    
    with open(idl_path, "r") as f:
        idl_dict = json.load(f)
    
    idl = Idl.from_json(idl_dict)
    
    # Create a workspace dictionary with programs
    # This uses localnet by default
    workspace_dict = {}
    
    # Get program ID from id.json
    id_path = workspace_root / "id.json"
    if id_path.exists():
        with open(id_path, "r") as f:
            program_id_str = json.load(f)
            if isinstance(program_id_str, str):
                program_id = Pubkey.from_string(program_id_str)
            elif isinstance(program_id_str, list):
                # It's a keypair array, extract the public key
                from solders.keypair import Keypair
                keypair = Keypair.from_secret_key(bytes(program_id_str))
                program_id = keypair.pubkey()
            else:
                raise ValueError(f"Unexpected id.json format: {program_id_str}")
    else:
        raise FileNotFoundError(f"id.json not found at {id_path}")
    
    # Create provider and program for localnet
    # Note: This assumes localnet is running or tests will need to mock it
    try:
        import asyncio
        from anchorpy import Provider, Wallet
        from solders.keypair import Keypair
        
        # Create a simple provider with default settings
        # Tests can override this with their own provider if needed
        provider = asyncio.run(_create_provider())
        program = Program(idl, program_id, provider)
        
        workspace_dict["fee_distribution"] = program
    except Exception as e:
        # If we can't create the provider, at least return the program struct
        # Tests might not need the actual provider connection
        print(f"Warning: Could not create provider: {e}")
        from anchorpy import Program
        program = Program(idl, program_id, None)
        workspace_dict["fee_distribution"] = program
    
    return workspace_dict


async def _create_provider():
    """Helper to create an AsyncProvider for localnet."""
    from anchorpy import Provider, Wallet
    from solana.rpc.async_api import AsyncClient
    from solders.keypair import Keypair
    import os
    
    # Use localnet by default
    endpoint = os.getenv("RPC_URL", "http://localhost:8899")
    client = AsyncClient(endpoint)
    
    # Create a dummy wallet (tests should provide their own payer)
    dummy_keypair = Keypair()
    wallet = Wallet(dummy_keypair)
    
    provider = Provider(client, wallet)
    return provider

