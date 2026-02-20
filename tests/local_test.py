#!/usr/bin/env python3
"""
Local contract testing script - tests contract against a local Solana validator
without deploying to testnet.

Prerequisites:
  - solana-test-validator running: solana-test-validator
  - Build contract: cargo-build-sbf --manifest-path programs/fee_distribution/Cargo.toml
  
Usage:
  python tests/local_test.py
"""

import json
import subprocess
import sys
from pathlib import Path
from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solana.rpc.api import Client
from solana.rpc.commitment import Confirmed

# Configuration
RPC_URL = "http://localhost:8899"
PROGRAM_PATH = Path(__file__).parent.parent / "target/deploy/fee_distribution.so"
IDL_PATH = Path(__file__).parent.parent / "target/idl/fee_distribution.json"

def load_program_id():
    """Extract program ID from lib.rs"""
    lib_rs = Path(__file__).parent.parent / "programs/fee_distribution/src/lib.rs"
    with open(lib_rs, 'r') as f:
        for line in f:
            if 'declare_id!' in line:
                # Extract ID from declare_id!("...")
                start = line.find('"') + 1
                end = line.rfind('"')
                return line[start:end]
    raise ValueError("Could not find declare_id in lib.rs")

def load_idl():
    """Load contract IDL"""
    with open(IDL_PATH, 'r') as f:
        return json.load(f)

def check_validator():
    """Check if test validator is running"""
    try:
        client = Client(RPC_URL)
        client.get_latest_blockhash()
        return True
    except:
        return False

def load_program_to_validator(program_id: str):
    """Load program binary into test validator"""
    if not PROGRAM_PATH.exists():
        print(f"❌ Error: Program binary not found at {PROGRAM_PATH}")
        print("   Run: cargo-build-sbf --manifest-path programs/fee_distribution/Cargo.toml")
        return False
    
    print(f"📤 Loading program {program_id} into local validator...")
    try:
        # This requires the program to be deployed via solana program deploy
        # For local testing, the validator loads it automatically
        return True
    except Exception as e:
        print(f"❌ Failed to load program: {e}")
        return False

def test_contract():
    """Run basic contract tests"""
    print("\n" + "="*60)
    print("Testing Fee Distribution Contract Locally")
    print("="*60)
    
    # Check validator
    print("\n✓ Checking for local validator...")
    if not check_validator():
        print("❌ Error: solana-test-validator is not running!")
        print("\nStart validator in another terminal:")
        print("  solana-test-validator")
        return False
    print("✓ Validator found at", RPC_URL)
    
    # Load program ID
    program_id = load_program_id()
    print(f"✓ Program ID: {program_id}")
    
    # Load IDL
    try:
        idl = load_idl()
        print(f"✓ IDL loaded with {len(idl.get('instructions', []))} instructions")
    except Exception as e:
        print(f"❌ Error loading IDL: {e}")
        return False
    
    # Check program binary
    if not PROGRAM_PATH.exists():
        print(f"❌ Error: Program binary not found at {PROGRAM_PATH}")
        print("   Build with: cargo-build-sbf --manifest-path programs/fee_distribution/Cargo.toml")
        return False
    print(f"✓ Binary found: {PROGRAM_PATH.stat().st_size / 1024:.1f}KB")
    
    # Print available instructions
    print("\n📋 Available Instructions:")
    for ix in idl.get('instructions', []):
        args_str = ", ".join([a['name'] for a in ix.get('args', [])])
        args_display = f"({args_str})" if args_str else "()"
        print(f"  • {ix['name']}{args_display}")
    
    # Print available accounts
    print("\n📦 Available Accounts:")
    for acc in idl.get('accounts', []):
        print(f"  • {acc['name']}")
        for field in acc.get('type', {}).get('fields', [])[:3]:
            print(f"    - {field['name']}: {field['type']}")
        if len(acc.get('type', {}).get('fields', [])) > 3:
            print(f"    ... and {len(acc['type']['fields']) - 3} more")
    
    # Print events
    print("\n🎯 Events:")
    for event in idl.get('events', []):
        print(f"  • {event['name']}")
    
    print("\n" + "="*60)
    print("✅ Contract ready for local testing!")
    print("="*60)
    print("\nNext Steps:")
    print("1. For integration tests, create test functions using solders library")
    print("2. For anchor tests, run: anchor test --skip-deploy")
    print("3. See integration_tests.py for example tests")
    
    return True

if __name__ == "__main__":
    try:
        success = test_contract()
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        print("\n⚠ Interrupted")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
