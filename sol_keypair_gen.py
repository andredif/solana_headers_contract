import json
from solders.keypair import Keypair


def generate_and_save_keypair(filename: str = "id.json") -> None:
    """
    Generate a new Solana keypair and save it to a JSON file.
    
    Args:
        filename: The output filename for the keypair (default: id.json)
    """
    # Generate a new keypair
    keypair = Keypair()
    
    # Get the keypair in JSON format (list of bytes)
    keypair_data = keypair.to_json()
    
    # Save to file
    with open(filename, 'w') as f:
        json.dump(keypair_data, f)
    
    print(f"Keypair generated and saved to {filename}")
    print(f"Public key: {keypair.pubkey()}")


if __name__ == "__main__":
    generate_and_save_keypair()
