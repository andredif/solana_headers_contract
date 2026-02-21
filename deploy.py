#!/usr/bin/env python3
"""
Solana Fee Distribution Contract Deployment Script (Python-only)

This script deploys the contract to Solana testnet using only Python.
No external CLI tools required (except Rust/Anchor for compilation).

Usage: python deploy.py <path_to_keypair> [network]

Arguments:
    path_to_keypair: Path to Solana keypair JSON file (required)
    network: Solana network (devnet, testnet, mainnet-beta, localnet) - default: testnet
"""

import json
import sys
import os
import subprocess
from pathlib import Path
from typing import Optional, Tuple
from solders.keypair import Keypair
from solana.rpc.api import Client
from solana.rpc.commitment import Confirmed


def get_rpc_url(network: str) -> str:
    """Get RPC URL for the specified network."""
    rpc_urls = {
        "devnet": "https://api.devnet.solana.com",
        "testnet": "https://api.testnet.solana.com",
        "mainnet-beta": "https://api.mainnet-beta.solana.com",
        "localnet": "http://127.0.0.1:8899"
    }
    return rpc_urls.get(network, rpc_urls["testnet"])


def load_keypair(keypair_path: str) -> Keypair:
    """Load keypair from JSON file."""
    try:
        with open(keypair_path, 'r') as f:
            keypair_data = json.load(f)
        keypair = Keypair.from_bytes(bytes(keypair_data))
        print(f"✓ Keypair loaded from {keypair_path}")
        print(f"  Public key: {keypair.pubkey()}")
        return keypair
    except FileNotFoundError:
        print(f"✗ Error: Keypair file not found at {keypair_path}")
        sys.exit(1)
    except json.JSONDecodeError:
        print(f"✗ Error: Invalid JSON in keypair file")
        sys.exit(1)
    except Exception as e:
        print(f"✗ Error loading keypair: {e}")
        sys.exit(1)


def validate_network(network: str) -> str:
    """Validate network parameter."""
    valid_networks = ["devnet", "testnet", "mainnet-beta", "localnet"]
    if network not in valid_networks:
        print(f"✗ Invalid network: {network}")
        print(f"  Valid options: {', '.join(valid_networks)}")
        sys.exit(1)
    return network


def check_solana_balance(client: Client, keypair: Keypair) -> Tuple[float, bool]:
    """Check account balance and ensure sufficient funds."""
    try:
        response = client.get_balance(keypair.pubkey(), Confirmed)
        balance_lamports = response.value
        balance_sol = balance_lamports / 1_000_000_000
        
        print(f"✓ Account balance: {balance_sol:.4f} SOL ({balance_lamports} lamports)")
        
        # Need at least 2 SOL for deployment
        if balance_sol < 2:
            print(f"⚠ Warning: Low balance. Deployment may require at least 2 SOL")
            return balance_sol, False
        return balance_sol, True
    except Exception as e:
        print(f"✗ Error checking balance: {e}")
        return 0, False


def build_anchor_program(project_path: str = "/home/andrea/Desktop/solana_contract") -> bool:
    """Build the Anchor program using anchor CLI (required for Rust compilation)."""
    try:
        print("\n📦 Building Anchor program...")
        result = subprocess.run(
            ["anchor", "build"],
            check=True,
            capture_output=True,
            text=True,
            cwd=project_path
        )
        print("✓ Program built successfully")
        return True
    except subprocess.CalledProcessError as e:
        print(f"✗ Build failed: {e.stderr}")
        return False
    except FileNotFoundError:
        print("✗ Anchor CLI not found. Please install Anchor first:")
        print("   https://www.anchor-lang.com/docs/installation")
        return False


def read_program_idl(project_path: str = "/home/andrea/Desktop/solana_contract") -> Optional[dict]:
    """Read the IDL from the built program."""
    try:
        idl_path = Path(project_path) / "target" / "idl" / "fee_distribution.json"
        
        if not idl_path.exists():
            print(f"✗ IDL file not found at {idl_path}")
            print("  Make sure the program has been built with: anchor build")
            return None
        
        with open(idl_path, 'r') as f:
            idl = json.load(f)
        
        print(f"✓ IDL loaded from {idl_path}")
        return idl
    except Exception as e:
        print(f"✗ Error reading IDL: {e}")
        return None


def read_program_binary(project_path: str = "/home/andrea/Desktop/solana_h_contract/solana_headers_contract") -> Optional[bytes]:
    """Read the compiled program binary."""
    try:
        # Try to find the .so file in the target directory
        target_dir = Path(project_path) / "target" / "deploy"
        
        so_files = list(target_dir.glob("*.so"))
        
        if not so_files:
            print(f"✗ No compiled program found in {target_dir}")
            print("  Make sure the program has been built with: anchor build")
            return None
        
        # Use the first .so file found (or fee_distribution.so if it exists)
        program_file = next(
            (f for f in so_files if "fee_distribution" in f.name),
            so_files[0]
        )
        
        print(f"✓ Reading program binary from {program_file}")
        
        with open(program_file, 'rb') as f:
            program_data = f.read()
        
        print(f"  Program size: {len(program_data) / 1024:.2f} KB")
        return program_data
    except Exception as e:
        print(f"✗ Error reading program binary: {e}")
        return None


def deploy_program_anchor(
    keypair: Keypair,
    keypair_path: str,
    network: str,
    project_path: str = "/home/andrea/Desktop/solana_contract"
) -> Optional[str]:
    """Deploy program using Anchor CLI."""
    try:
        print(f"\n🚀 Deploying to {network}...")
        
        env = os.environ.copy()
        # with open(keypair_path, 'r') as f:
        #     result = json.loads(f)
        env['ANCHOR_WALLET'] = os.path.abspath(keypair_path)
        
        result = subprocess.run(
            ["anchor", "deploy", "--provider.cluster", network],
            check=True,
            capture_output=True,
            text=True,
            cwd=project_path,
            env=env
        )
        
        # Extract program ID from output
        output = result.stdout + result.stderr
        for line in output.split('\n'):
            if 'Program Id:' in line:
                program_id = line.split(':')[1].strip()
                print(f"✓ Deployment successful!")
                print(f"✓ Program ID: {program_id}")
                return program_id
        
        print("✗ Could not extract program ID from deployment output")
        print(output)
        return None
    except subprocess.CalledProcessError as e:
        print(f"✗ Deployment failed: {e.stderr}")
        return None
    except FileNotFoundError:
        print("✗ Anchor CLI not found. Please install Anchor first:")
        print("   https://www.anchor-lang.com/docs/installation")
        return None


def save_deployment_config(program_id: str, network: str, path: str = "/home/andrea/Desktop/solana_contract/.env") -> bool:
    """Save deployment configuration to .env file."""
    try:
        # Read existing .env if it exists
        env_vars = {}
        if os.path.exists(path):
            with open(path, 'r') as f:
                for line in f:
                    if '=' in line and not line.startswith('#'):
                        key, value = line.strip().split('=', 1)
                        env_vars[key] = value
        
        # Update with new values
        env_vars['PROGRAM_ID'] = program_id
        env_vars['SOLANA_NETWORK'] = network
        
        # Write back to file
        with open(path, 'w') as f:
            f.write(f"# Solana Network Configuration\n")
            f.write(f"SOLANA_NETWORK={network}\n")
            f.write(f"PROGRAM_ID={program_id}\n")
            f.write(f"SOLANA_RPC_URL={get_rpc_url(network)}\n\n")
            f.write(f"# Add your configuration below\n")
            for key, value in env_vars.items():
                if key not in ['SOLANA_NETWORK', 'PROGRAM_ID', 'SOLANA_RPC_URL']:
                    f.write(f"{key}={value}\n")
        
        print(f"✓ Configuration saved to .env")
        return True
    except Exception as e:
        print(f"✗ Error saving configuration: {e}")
        return False


def main():
    """Main deployment function."""
    # Parse arguments
    if len(sys.argv) < 2:
        print("Usage: python deploy.py <path_to_keypair> [network] [--get-latest-build]")
        print("\nArguments:")
        print("  path_to_keypair     Path to Solana keypair JSON file (required)")
        print("  network             Network: devnet, testnet, mainnet-beta, localnet (default: testnet)")
        print("  --get-latest-build  Use latest build from target/ instead of rebuilding")
        print("\nExamples:")
        print("  python deploy.py ./id.json testnet")
        print("  python deploy.py ./id.json devnet --get-latest-build")
        print("  python deploy.py ./id.json localnet")
        sys.exit(1)
    
    # Parse positional arguments and flags
    keypair_path = sys.argv[1]
    network = "testnet"
    get_latest_build = False
    
    # Parse remaining arguments
    for i in range(2, len(sys.argv)):
        if sys.argv[i] == "--get-latest-build":
            get_latest_build = True
        else:
            # Treat as network if it's not a flag
            network = sys.argv[i]
    
    # Validate inputs
    keypair_path = os.path.expanduser(keypair_path)
    network = validate_network(network)
    project_path = "/home/andrea/Desktop/solana_h_contract/solana_headers_contract"
    
    print("=" * 60)
    print("Solana Fee Distribution Contract Deployment")
    print("Python-Only Edition")
    if get_latest_build:
        print("(Using latest build)")
    print("=" * 60)
    
    # Load keypair
    keypair = load_keypair(keypair_path)
    
    # Connect to RPC
    rpc_url = get_rpc_url(network)
    print(f"\n📡 Connecting to {network}...")
    print(f"   RPC URL: {rpc_url}")
    client = Client(rpc_url)
    
    # Check balance
    print("\n💰 Checking account balance...")
    balance, has_sufficient = check_solana_balance(client, keypair)
    
    if not has_sufficient:
        response = input("⚠ Continue anyway? (y/n): ")
        if response.lower() != 'y':
            sys.exit(1)
    
    # Build program or use latest build
    if get_latest_build:
        print("\n📦 Using latest build from target/...")
    else:
        if not build_anchor_program(project_path):
            sys.exit(1)
    
    # Read program data
    print("\n📖 Reading program data...")
    program_data = read_program_binary(project_path)
    idl_data = read_program_idl(project_path)
    
    if not program_data:
        print("✗ Failed to read compiled program")
        if get_latest_build:
            print("  Build the program first with: cargo build --release")
        else:
            print("  Build failed or program not found")
        sys.exit(1)
    
    # Deploy program
    program_id = deploy_program_anchor(keypair, keypair_path, network, project_path)
    
    if not program_id:
        print("\n⚠ Deployment with Anchor CLI is recommended for this step")
        print("  Install Anchor: https://www.anchor-lang.com/docs/installation")
        print("  Then run: anchor deploy --provider.cluster", network)
        sys.exit(1)
    
    # Save configuration
    if save_deployment_config(program_id, network):
        print("\n" + "=" * 60)
        print("✓ Deployment Complete!")
        print("=" * 60)
        print(f"\nYour program has been deployed to {network}")
        print(f"Program ID: {program_id}")
        print(f"\nNext steps:")
        print(f"1. Run tests against the deployed contract:")
        print(f"   pytest tests/integration_tests.py -v")
        print(f"2. View your deployment environment:")
        print(f"   cat .env")
    else:
        print("⚠ Deployment succeeded but could not save configuration")
        print(f"Program ID: {program_id}")
        print("Please update .env manually with the program ID")


if __name__ == "__main__":
    main()
