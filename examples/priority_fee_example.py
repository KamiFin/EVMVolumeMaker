#!/usr/bin/env python3
"""
Example implementation of Priority Fee Manager with Helius RPC
Shows how to:
1. Set up the priority fee manager
2. Get priority fees for transactions
3. Handle transaction failures and successes
4. Monitor and adapt to network conditions
"""
import os
import sys
import time
import logging
import json
from typing import Optional
from pathlib import Path

# Add the parent directory to sys.path to import from the root
sys.path.append(str(Path(__file__).parent.parent))

from solana.rpc.api import Client
from solana.rpc.types import TxOpts
from solders.transaction import Transaction
from solders.keypair import Keypair
from solders.system_program import TransferParams, transfer
from solana.exceptions import SolanaRpcException
from solana.rpc.commitment import Confirmed
from solders.compute_budget import set_compute_unit_price

# Import our Priority Fee Manager
from utils.priority_fee_manager import PriorityFeeManager, PriorityLevel

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Constants
DEFAULT_PRIORITY_LEVEL: PriorityLevel = "Medium"
TRANSFER_AMOUNT = 0.0001 * 10**9  # 0.0001 SOL in lamports

def load_config():
    """Load configuration from config.json"""
    try:
        with open("config.json", "r") as f:
            config = json.load(f)
        return config
    except Exception as e:
        logger.error(f"Error loading config.json: {e}")
        raise

def get_helius_rpc_url():
    """Get Helius RPC URL from config"""
    config = load_config()
    solana_config = config.get("chains", {}).get("solana", {})
    
    # Check for Helius URL in alternative RPCs
    rpc_url = solana_config.get("rpc_url", "")
    alt_rpcs = solana_config.get("alternative_rpcs", [])
    
    # Look for Helius URL
    for url in [rpc_url] + alt_rpcs:
        if "helius" in url.lower():
            return url
    
    # No Helius URL found, use the main RPC URL
    logger.warning("No Helius RPC URL found in config. Using primary RPC URL.")
    return rpc_url

def create_keypair_safely(private_key: str) -> Optional[Keypair]:
    """
    Create a keypair from a private key string in any supported format
    
    Args:
        private_key: Private key string (base58 or hex format)
        
    Returns:
        Keypair object or None if all methods fail
    """
    # Try various methods to create a keypair
    try:
        # Try base58 format first (most common in Solana projects)
        return Keypair.from_base58_string(private_key)
    except Exception as e1:
        logger.debug(f"Base58 keypair creation failed: {e1}")
        
        try:
            # Try hex format
            return Keypair.from_secret_key(bytes.fromhex(private_key))
        except Exception as e2:
            logger.debug(f"Hex keypair creation failed: {e2}")
            
            try:
                # As a last resort, try to check if it's a uint8array format
                if private_key.startswith('[') and private_key.endswith(']'):
                    # Parse array-like string
                    try:
                        array_data = json.loads(private_key)
                        return Keypair.from_bytes(bytes(array_data))
                    except:
                        pass
            except Exception as e3:
                logger.debug(f"Array keypair creation failed: {e3}")
    
    logger.error("All keypair creation methods failed")
    return None

def get_sender_keypair():
    """Get sender keypair from config"""
    config = load_config()
    wallet_info = config.get("solana_wallets", [])[0]
    private_key = wallet_info.get("private_key", "")
    
    if not private_key:
        raise ValueError("No private key found in config")
    
    keypair = create_keypair_safely(private_key)
    if not keypair:
        raise ValueError("Could not create a valid keypair from the provided private key")
    
    return keypair

def create_and_send_transaction(
    client: Client,
    fee_manager: PriorityFeeManager,
    sender_keypair: Keypair,
    receiver_pubkey: str,
    amount: int,
    priority_level: PriorityLevel = DEFAULT_PRIORITY_LEVEL
):
    """Create and send a transaction with priority fees"""
    try:
        # Create instructions
        instructions = []
        
        # Add compute budget instructions
        priority_fee = fee_manager.get_priority_fee_for_transaction(None, priority_level)
        logger.info(f"Using priority fee: {priority_fee} microlamports (level: {priority_level})")
        
        # Add compute unit price instruction first
        compute_unit_price_ix = set_compute_unit_price(int(priority_fee))
        instructions.append(compute_unit_price_ix)
        
        # Add transfer instruction
        transfer_ix = transfer(
            TransferParams(
                from_pubkey=sender_keypair.pubkey(),
                to_pubkey=receiver_pubkey,
                lamports=amount
            )
        )
        instructions.append(transfer_ix)
        
        # Get recent blockhash
        blockhash_response = client.get_latest_blockhash()
        blockhash = blockhash_response.value.blockhash
        
        # Create transaction using MessageV0 and VersionedTransaction
        from solders.message import MessageV0
        from solders.transaction import VersionedTransaction
        
        compiled_message = MessageV0.try_compile(
            sender_keypair.pubkey(),
            instructions,
            [],  # Address lookup tables
            blockhash
        )
        transaction = VersionedTransaction(compiled_message, [sender_keypair])
        
        # Send transaction
        opts = TxOpts(skip_preflight=False, preflight_commitment=Confirmed)
        signature = client.send_transaction(transaction, sender_keypair, opts=opts).value
        logger.info(f"Transaction sent with signature: {signature}")
        
        # Wait for confirmation
        status = client.confirm_transaction(signature)
        if status.value:
            logger.info(f"Transaction confirmed: {signature}")
            fee_manager.handle_transaction_success()
            return True, signature
        else:
            logger.error(f"Transaction failed to confirm: {signature}")
            fee_manager.handle_transaction_failure(Exception("Transaction failed to confirm"))
            return False, signature
        
    except SolanaRpcException as e:
        logger.error(f"Solana RPC error: {e}")
        # Adjust fees based on failure
        new_fee = fee_manager.handle_transaction_failure(e)
        logger.info(f"Updated priority fee for next attempt: {new_fee}")
        return False, None
    except Exception as e:
        logger.error(f"Error sending transaction: {e}")
        return False, None

def demo_priority_fee_system():
    """Demonstrate the priority fee system"""
    # Get RPC URL
    helius_rpc_url = get_helius_rpc_url()
    logger.info(f"Using RPC URL: {helius_rpc_url}")
    
    # Initialize client
    client = Client(helius_rpc_url)
    
    # Initialize fee manager
    fee_manager = PriorityFeeManager(helius_rpc_url, update_interval=60)  # Update every minute for demo
    
    # Get sender keypair
    sender_keypair = get_sender_keypair()
    
    # Destination is just sender (for demo)
    receiver_pubkey = sender_keypair.pubkey()
    
    # Get current priority fees for all levels
    logger.info("Current priority fees:")
    for level in ["Min", "Low", "Medium", "High", "VeryHigh", "UnsafeMax"]:
        fee = fee_manager.get_priority_fee(level)
        logger.info(f"  {level}: {fee} microlamports")
    
    # Send a transaction with each priority level
    for level in ["Low", "Medium", "High"]:
        logger.info(f"\nSending transaction with {level} priority...")
        success, signature = create_and_send_transaction(
            client, fee_manager, sender_keypair, receiver_pubkey, 
            TRANSFER_AMOUNT, level
        )
        
        # Wait a bit between transactions
        time.sleep(2)
    
    # Simulate a failure scenario
    logger.info("\nSimulating transaction failure...")
    fee_manager.handle_transaction_failure(Exception("compute budget exceeded"))
    fee_manager.handle_transaction_failure(Exception("compute budget exceeded"))
    fee_manager.handle_transaction_failure(Exception("compute budget exceeded"))
    
    logger.info(f"After failures, fee manager status:")
    logger.info(f"  In recovery mode: {fee_manager.in_recovery_mode}")
    logger.info(f"  Current priority level: {fee_manager.get_current_priority_level()}")
    logger.info(f"  Current priority fee: {fee_manager.get_priority_fee()} microlamports")
    
    # Send one more transaction with current level
    logger.info("\nSending final transaction with current global priority level...")
    success, signature = create_and_send_transaction(
        client, fee_manager, sender_keypair, receiver_pubkey, 
        TRANSFER_AMOUNT
    )
    
    # Clean shutdown
    fee_manager.shutdown()
    logger.info("Priority fee demo completed")

if __name__ == "__main__":
    demo_priority_fee_system() 