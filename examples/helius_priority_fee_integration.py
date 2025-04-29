#!/usr/bin/env python3
"""
Helius Priority Fee Integration Example for EVMVolumeMaker

This example demonstrates how to initialize the PriorityFeeManager with Helius RPC
and integrate it with the Solana Volume Maker. It shows both direct usage and
integration with the existing codebase.

Usage:
  python examples/helius_priority_fee_integration.py
"""
import os
import sys
import time
import json
import logging
import random
import requests
import base58
from typing import Optional
from pathlib import Path

# Add the parent directory to sys.path to import from the root
parent_dir = str(Path(__file__).parent.parent)
if parent_dir not in sys.path:
    sys.path.append(parent_dir)

# Import libraries with better error handling
try:
    from solana.rpc.api import Client
    from solana.rpc.types import TxOpts
    from solana.rpc.commitment import Confirmed
    from solders.transaction import Transaction, VersionedTransaction
    from solders.message import MessageV0
    from solders.keypair import Keypair
    from solders.system_program import TransferParams, transfer
    from solders.compute_budget import set_compute_unit_price, set_compute_unit_limit
except ImportError as e:
    print(f"ERROR: Missing required Solana libraries: {e}")
    print("Please install with: pnpm add @solana/web3.js solana solders")
    sys.exit(1)

# Import our custom modules
try:
    from utils.priority_fee_manager import PriorityFeeManager
    from utils.solana_utils import set_priority_fee_manager, get_transaction_compute_unit_price
    from solana_volume_maker import SolanaVolumeMaker
except ImportError as e:
    print(f"ERROR: Could not import required modules from project: {e}")
    print("Make sure you're running this from the project root directory")
    sys.exit(1)

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

def load_config():
    """Load configuration from config.json"""
    try:
        config_path = Path(__file__).parent.parent / "config.json"
        with open(config_path, "r") as f:
            config = json.load(f)
        return config
    except Exception as e:
        logger.error(f"Error loading config.json: {str(e)}")
        raise

def get_helius_rpc_url() -> str:
    """Get Helius RPC URL from config"""
    config = load_config()
    solana_config = config.get("chains", {}).get("solana", {})
    
    # Check for Helius URL in alternative RPCs
    rpc_url = solana_config.get("rpc_url", "")
    alt_rpcs = solana_config.get("alternative_rpcs", [])
    
    # Look for Helius URL
    for url in [rpc_url] + alt_rpcs:
        if url and "helius" in url.lower():
            return url
    
    # If no Helius-specific URL is found, return the primary RPC URL with a warning
    logger.warning("No Helius RPC URL found in config. Helius-specific features may not work.")
    return rpc_url or "https://api.mainnet-beta.solana.com"

def initialize_priority_fee_manager() -> PriorityFeeManager:
    """Initialize the PriorityFeeManager with Helius RPC"""
    helius_url = get_helius_rpc_url()
    if not helius_url:
        logger.error("No valid RPC URL found for priority fee manager.")
        sys.exit(1)
    
    logger.info(f"Initializing priority fee manager with Helius RPC: {helius_url}")
    fee_manager = PriorityFeeManager(helius_url, update_interval=300)  # Update every 5 minutes
    
    # Wait for initial fee fetch
    time.sleep(1)
    
    # Display current fee levels
    logger.info("Current priority fees:")
    for level in ["Min", "Low", "Medium", "High", "VeryHigh", "UnsafeMax"]:
        fee = fee_manager.get_priority_fee(level)
        logger.info(f"  {level}: {fee} microlamports")
    
    return fee_manager

def create_keypair_safely(private_key: str) -> Keypair:
    """
    Create a keypair from a private key string in any supported format
    
    Args:
        private_key: Private key string (base58 or hex format)
        
    Returns:
        Keypair object
    Raises:
        ValueError: If keypair cannot be created
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
    raise ValueError("Could not create keypair from provided private key")

def example_transaction_with_priority_fees(fee_manager: PriorityFeeManager):
    """Example of a transaction using the priority fee manager"""
    if not fee_manager:
        logger.error("Priority fee manager is not initialized.")
        return
    
    # Get RPC URL
    helius_url = get_helius_rpc_url()
    if not helius_url:
        logger.error("No valid RPC URL found.")
        return
    
    # Initialize client
    client = Client(helius_url)
    
    # Get a wallet from config
    try:
        config = load_config()
        wallet_info = config.get("solana_wallets", [])[0]
        private_key = wallet_info.get("private_key", "")
        
        if not private_key:
            logger.error("No private key found in config.")
            return
    except Exception as e:
        logger.error(f"Error getting wallet from config: {e}")
        return
    
    # Create keypair safely
    try:
        keypair = create_keypair_safely(private_key)
    except Exception as e:
        logger.error(f"Could not create keypair: {e}")
        return
        
    # Destination is just sender (for demonstration)
    receiver_pubkey = keypair.pubkey()
    
    # Create instructions list
    instructions = []
    
    # Add transfer instruction (sending a tiny amount to self)
    transfer_ix = transfer(
        TransferParams(
            from_pubkey=keypair.pubkey(),
            to_pubkey=receiver_pubkey,
            lamports=1000  # Just 0.000001 SOL
        )
    )
    
    # Get real blockhash for estimation
    try:
        blockhash_response = client.get_latest_blockhash()
        if not hasattr(blockhash_response, 'value') or not hasattr(blockhash_response.value, 'blockhash'):
            logger.error("Failed to get valid blockhash")
            return
            
        estimation_blockhash = blockhash_response.value.blockhash
        
        # Create a mock transaction for fee estimation using MessageV0
        try:
            # Create a mock transaction for fee estimation
            mock_message = MessageV0.try_compile(
                payer=keypair.pubkey(),
                instructions=[transfer_ix],
                address_lookup_table_accounts=[],  # No lookup tables
                recent_blockhash=estimation_blockhash
            )
            
            # Create mock transaction based on the available API
            try:
                mock_transaction = VersionedTransaction(
                    message=mock_message,
                    keypairs=[keypair]
                )
            except TypeError:
                # Fallback if the API requires different parameters
                mock_transaction = VersionedTransaction(mock_message)
        
            # Get priority fee for this specific transaction
            priority_fee = fee_manager.get_priority_fee_for_transaction(mock_transaction, "Medium")
            logger.info(f"Transaction-specific priority fee: {priority_fee} microlamports")
            
        except Exception as e:
            logger.error(f"Error creating mock transaction: {e}")
            # Fallback to level-based fee
            priority_fee = fee_manager.get_priority_fee("Medium")
            logger.info(f"Using level-based priority fee: {priority_fee} microlamports")
        
        # Add compute budget instructions first
        instructions.append(set_compute_unit_limit(2500))  # Set compute budget
        instructions.append(set_compute_unit_price(int(priority_fee)))  # Set price
        
        # Now add the transfer instruction
        instructions.append(transfer_ix)
        
        # Get fresh blockhash for the real transaction
        blockhash = client.get_latest_blockhash().value.blockhash
        
        # Create the real transaction using MessageV0
        real_message = MessageV0.try_compile(
            keypair.pubkey(),
            instructions,
            [],  # Address lookup tables
            blockhash
        )
        
        # Create and sign the real transaction
        try:
            # Try with the keypairs parameter
            transaction = VersionedTransaction(
                message=real_message,
                keypairs=[keypair]
            )
        except TypeError:
            # Fallback to older API
            transaction = VersionedTransaction(real_message)
            # If needed, sign the transaction separately
            
        # Send transaction with correct transaction opts
        try:
            # Create proper transaction options
            opts = TxOpts(skip_preflight=False, preflight_commitment=Confirmed)
            
            # Send the transaction correctly
            # Different APIs require different parameter orders
            try:
                # Try the standard API
                signature = client.send_transaction(transaction, opts=opts).value
            except TypeError:
                # Fallback to the alternate API that includes the signer
                signature = client.send_transaction(transaction, keypair, opts=opts).value
            except Exception as e:
                # Last resort, try without opts
                logger.warning(f"Falling back to simpler transaction sending due to: {e}")
                signature = client.send_transaction(transaction).value
                
            logger.info(f"Transaction sent with signature: {signature}")
            
            # Wait for confirmation
            status = client.confirm_transaction(signature)
            if status.value:
                logger.info(f"Transaction confirmed: {signature}")
                fee_manager.handle_transaction_success()
            else:
                logger.error(f"Transaction failed to confirm: {signature}")
                fee_manager.handle_transaction_failure(Exception("Transaction failed to confirm"))
                
        except Exception as e:
            logger.error(f"Error sending transaction: {e}")
            # Handle the failure
            new_fee = fee_manager.handle_transaction_failure(e)
            logger.info(f"Updated priority fee for next attempt: {new_fee}")
    
    except Exception as e:
        logger.error(f"Error creating transaction: {e}")

def integrate_with_solana_volume_maker():
    """Demonstrate how to integrate the priority fee manager with SolanaVolumeMaker"""
    try:
        # Initialize the priority fee manager
        fee_manager = initialize_priority_fee_manager()
        
        # Register the fee manager with solana_utils
        set_priority_fee_manager(fee_manager)
        logger.info("Priority fee manager registered with solana_utils.")
        
        # Now any SolanaVolumeMaker instance will automatically use the priority fee manager
        # through the existing get_optimal_compute_unit_price function
        
        # Here's how you would typically initialize SolanaVolumeMaker
        try:
            maker = SolanaVolumeMaker('solana', mode='buy')
            logger.info("Solana Volume Maker initialized with priority fee manager integration.")
            
            # The Volume Maker will now automatically use priority fees through the utility functions
            # If you want to test a transaction, uncomment the following lines:
            
            # logger.info("Testing token buy with priority fees...")
            # maker.buy_tokens()
            
        except Exception as e:
            logger.error(f"Error initializing Solana Volume Maker: {e}")
    except Exception as e:
        logger.error(f"Error setting up priority fee manager integration: {e}")

def simulate_error_recovery():
    """Simulate error recovery with the priority fee manager"""
    try:
        # Initialize the priority fee manager
        fee_manager = initialize_priority_fee_manager()
        
        logger.info("\n=== Simulating Transaction Failures and Recovery ===")
        
        # Show initial priority level
        logger.info(f"Initial priority level: {fee_manager.get_current_priority_level()}")
        logger.info(f"Initial priority fee: {fee_manager.get_priority_fee()} microlamports")
        
        # Simulate a series of failures
        error_types = [
            "compute budget exceeded",
            "transaction simulation failed",
            "block height exceeded",
            "vote landing failed"
        ]
        
        # Simulate 5 random failures
        for i in range(5):
            error_msg = random.choice(error_types)
            logger.info(f"\nSimulating failure {i+1}: {error_msg}")
            
            # Process the failure
            new_fee = fee_manager.handle_transaction_failure(Exception(error_msg))
            
            # Show updated state
            logger.info(f"Updated priority level: {fee_manager.get_current_priority_level()}")
            logger.info(f"Updated priority fee: {new_fee} microlamports")
            logger.info(f"Recovery mode active: {fee_manager.in_recovery_mode}")
            
            # Small delay to make the sequence easier to follow
            time.sleep(0.5)
        
        # Simulate successful transactions to recover
        logger.info("\n=== Simulating Recovery with Successful Transactions ===")
        
        # Simulate 5 successful transactions
        for i in range(5):
            logger.info(f"\nSimulating successful transaction {i+1}")
            
            # Process the success
            fee_manager.handle_transaction_success()
            
            # Show updated state
            logger.info(f"Updated priority level: {fee_manager.get_current_priority_level()}")
            logger.info(f"Updated priority fee: {fee_manager.get_priority_fee()} microlamports")
            logger.info(f"Recovery mode active: {fee_manager.in_recovery_mode}")
            
            # Small delay to make the sequence easier to follow
            time.sleep(0.5)
        
        logger.info("\nRecovery simulation complete.")
        
        # Clean up
        fee_manager.shutdown()
    except Exception as e:
        logger.error(f"Error in error recovery simulation: {e}")

def send_transaction_with_priority_fee(self, transaction, signer, priority_level="Medium"):
    """
    Send a transaction with priority fees using Helius API
    
    Args:
        transaction: The transaction to send
        signer: The keypair to sign with
        priority_level: Priority level ("Min", "Low", "Medium", "High", "VeryHigh", "UnsafeMax")
        
    Returns:
        str: Transaction signature if successful
    """
    try:
        # First, serialize the transaction
        transaction_bytes = transaction.serialize()
        
        # Encode transaction to base58
        encoded_transaction = base58.b58encode(transaction_bytes).decode('utf-8')
        
        # Get priority fee from Helius API
        helius_url = self.config.RPC_URL  # Assuming this is the Helius RPC URL
        
        # Create payload for Helius API
        payload = {
            "jsonrpc": "2.0",
            "id": "1",
            "method": "getPriorityFeeEstimate",
            "params": [{
                "transaction": encoded_transaction,
                "options": {"priorityLevel": priority_level}
            }]
        }
        
        # Send request to Helius API
        response = requests.post(
            helius_url,
            headers={"Content-Type": "application/json"},
            json=payload,
            timeout=10
        )
        
        # Parse response
        if response.status_code == 200:
            result = response.json()
            if "result" in result and "priorityFeeEstimate" in result["result"]:
                fee_estimate = result["result"]["priorityFeeEstimate"]
                logger.info(f"Priority fee estimate: {fee_estimate} microlamports (level: {priority_level})")
                
                # Create compute budget instructions
                compute_unit_price_ix = set_compute_unit_price(int(fee_estimate))
                compute_unit_limit_ix = set_compute_unit_limit(2500)  # Standard compute limit
                
                # Get blockhash for new transaction
                blockhash_response = self.client.get_latest_blockhash()
                blockhash = blockhash_response.value.blockhash
                
                # Create new instructions list with compute budget at the beginning
                if isinstance(transaction, VersionedTransaction):
                    # For versioned transactions
                    instructions = [compute_unit_price_ix, compute_unit_limit_ix]
                    
                    # Extract existing instructions
                    for ix in transaction.message.instructions:
                        instructions.append(ix)
                    
                    # Create new message and transaction
                    compiled_message = MessageV0.try_compile(
                        signer.pubkey(),
                        instructions,
                        [],  # No lookup tables
                        blockhash
                    )
                    new_transaction = VersionedTransaction(compiled_message, [signer])
               
                
                # Send transaction with priority fee
                txn_sig = self.client.send_transaction(
                    txn=new_transaction,
                    opts=TxOpts(skip_preflight=False)
                ).value
                
                logger.info(f"Transaction with priority fee sent! Signature: {txn_sig}")
                return txn_sig
            else:
                logger.warning(f"Unexpected response format: {result}")
        else:
            logger.error(f"Failed to fetch priority fees: {response.status_code} - {response.text}")
        
        # If we couldn't get priority fee, send the original transaction
        logger.warning("Falling back to sending transaction without priority fee")
        txn_sig = self.client.send_transaction(
            txn=transaction,
            opts=TxOpts(skip_preflight=False)
        ).value
        return txn_sig
    
    except Exception as e:
        logger.error(f"Error sending transaction with priority fee: {e}")
        raise

if __name__ == "__main__":
    try:
        logger.info("=== Helius Priority Fee Integration Example ===")
        
        # First demonstrate direct usage
        fee_manager = initialize_priority_fee_manager()
        example_transaction_with_priority_fees(fee_manager)
            
        # Demonstrate error recovery
        simulate_error_recovery()
        
        # Then demonstrate integration with Solana Volume Maker
        logger.info("\n=== Integrating with Solana Volume Maker ===")
        integrate_with_solana_volume_maker()
        
        logger.info("\nExample completed.")
    except Exception as e:
        logger.error(f"Unhandled exception in main: {e}")
        sys.exit(1) 