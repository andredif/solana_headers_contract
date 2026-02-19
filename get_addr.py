#!/usr/bin/env python3
"""
Solana Address Extractor

This script reads a keypair from id.json and prints the public address (SOL address).
Usage: python get_addr.py [id_file]

Arguments:
    id_file: Path to the keypair JSON file (default: id.json)
"""

import json
import sys
from pathlib import Path
from solders.keypair import Keypair


def get_address(keypair_file: str = "id.json") -> str:
    """
    Read keypair from JSON file and return the public address.
    
    Args:
        keypair_file: Path to the keypair JSON file
        
    Returns:
        The public SOL address
    """
    try:
        with open(keypair_file, 'r') as f:
            keypair_data = json.load(f)
        
        # Create keypair from JSON (string representation)
        keypair = Keypair.from_json(keypair_data)
        
        # Get the public address
        address = str(keypair.pubkey())
        
        return address
    except FileNotFoundError:
        print(f"✗ Error: Keypair file not found at {keypair_file}")
        sys.exit(1)
    except json.JSONDecodeError:
        print(f"✗ Error: Invalid JSON in keypair file")
        sys.exit(1)
    except Exception as e:
        print(f"✗ Error reading keypair: {e}")
        sys.exit(1)


if __name__ == "__main__":
    keypair_file = sys.argv[1] if len(sys.argv) > 1 else "id.json"
    address = get_address(keypair_file)
    print(f"SOL Address: {address}")
