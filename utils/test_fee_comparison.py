#!/usr/bin/env python3
"""
Compare different priority fee strategies.

This script compares:
1. Legacy fee calculation based on network congestion
2. Fixed fee with exponential backoff on failure
3. Helius RPC API priority fee estimates

Run with: python -m utils.test_fee_comparison
"""
import os
import sys
import time
import logging
import json
from pathlib import Path

# Add the parent directory to sys.path to import from the root
parent_dir = str(Path(__file__).parent.parent)
if parent_dir not in sys.path:
    sys.path.append(parent_dir)

# Import core modules - using try/except for better error handling
try:
    from solana.rpc.api import Client
    from solders.transaction import Transaction, VersionedTransaction
    from solders.message import MessageV0
    from solders.keypair import Keypair
    from solders.system_program import TransferParams, transfer
    from solders.compute_budget import set_compute_unit_price, set_compute_unit_limit
except ImportError as e:
    print(f"ERROR: Missing required Solana libraries: {e}")
    print("Please install with: pnpm add @solana/web3.js solana solders")
    sys.exit(1)

# Import our modules with error handling
try:
    from solana_config import UNIT_PRICE
    from utils.solana_utils import get_optimal_compute_unit_price, get_retry_compute_unit_price
    from utils.priority_fee_manager import PriorityFeeManager
except ImportError as e:
    print(f"ERROR: Could not import required modules from project: {e}")
    print("Make sure you're running this from the project root directory")
    sys.exit(1)

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
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

def initialize_fee_manager() -> PriorityFeeManager:
    """Initialize the PriorityFeeManager with Helius RPC"""
    helius_url = get_helius_rpc_url()
    if not helius_url:
        logger.error("No valid RPC URL found for priority fee manager.")
        sys.exit(1)
    
    logger.info(f"Initializing priority fee manager with RPC: {helius_url}")
    fee_manager = PriorityFeeManager(helius_url, update_interval=300)
    
    # Wait for initial fee fetch
    time.sleep(2)
    return fee_manager

def create_keypair_safely(private_key: str) -> Keypair:
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
    raise ValueError("Could not create keypair from provided private key")

def create_test_transaction():
    """
    Create a simple test transaction for fee estimation
    
    Returns:
        VersionedTransaction: A properly formatted versioned transaction for testing
        or None if transaction creation fails
    """
    # Get a wallet from config
    try:
        config = load_config()
        if "solana_wallets" not in config or not config["solana_wallets"]:
            logger.error("No Solana wallets found in config.json")
            return None
            
        wallet_info = config.get("solana_wallets", [])[0]
        private_key = wallet_info.get("private_key", "")
        
        if not private_key:
            logger.error("No private key found in config.")
            return None
    except Exception as e:
        logger.error(f"Error getting wallet from config: {e}")
        return None
    
    # Create keypair using the safe utility function
    try:
        keypair = create_keypair_safely(private_key)
    except Exception as e:
        logger.error(f"Could not create keypair: {e}")
        return None
    
    # Destination is just sender for testing
    receiver_pubkey = keypair.pubkey()
    
    # Create instructions
    instructions = []
    
    # Add priority fee instruction (optional)
    # The priority fee will be calculated by the manager, but we need to
    # include a placeholder instruction for the compute unit price
    compute_price_ix = set_compute_unit_price(1_000)  # Just a placeholder value
    compute_limit_ix = set_compute_unit_limit(200_000)  # Standard compute limit
    instructions.append(compute_price_ix)
    instructions.append(compute_limit_ix)
    
    # Add transfer instruction
    transfer_ix = transfer(
        TransferParams(
            from_pubkey=keypair.pubkey(),
            to_pubkey=receiver_pubkey,
            lamports=1000  # Minimal amount
        )
    )
    instructions.append(transfer_ix)
    
    try:
        # Get real blockhash from the network
        # This is more reliable than using a dummy blockhash
        client = Client(get_helius_rpc_url())
        real_blockhash_response = client.get_latest_blockhash()
        if not hasattr(real_blockhash_response, 'value') or not hasattr(real_blockhash_response.value, 'blockhash'):
            logger.error("Failed to get valid blockhash")
            return None
            
        real_blockhash = real_blockhash_response.value.blockhash
        
        # Create versioned transaction (v0)
        # Properly compile MessageV0 with all necessary parameters
        try:
            compiled_message = MessageV0.try_compile(
                payer=keypair.pubkey(),
                instructions=instructions,
                address_lookup_table_accounts=[],  # No lookup tables used
                recent_blockhash=real_blockhash
            )
        except Exception as e:
            logger.error(f"Error compiling message: {e}")
            return None
        
        # Create the versioned transaction and sign it
        try:
            # Create and sign with keypair
            transaction = VersionedTransaction(
                message=compiled_message,
                keypairs=[keypair]
            )
        except TypeError as e:
            logger.error(f"Incompatible VersionedTransaction API: {e}")
            logger.info("Attempting to use alternative transaction creation approach")
            
            # If that fails, create a legacy transaction as a fallback
            try:
                from solders.transaction import Transaction
                transaction = Transaction.new_with_payer(
                    instructions,
                    keypair.pubkey(),
                    keypair
                )
                logger.info("Created legacy transaction as fallback")
            except Exception as e2:
                logger.error(f"Failed to create fallback transaction: {e2}")
                return None
            
        logger.info("Successfully created test transaction")
        return transaction
    except Exception as e:
        logger.error(f"Error creating test transaction: {e}")
        return None

def compare_fee_strategies():
    """Compare different fee calculation strategies"""
    # Initialize fee manager
    try:
        fee_manager = initialize_fee_manager()
    except Exception as e:
        logger.error(f"Failed to initialize fee manager: {e}")
        return
    
    # Create test transaction - with error handling
    try:
        transaction = create_test_transaction()
        if not transaction:
            logger.warning("Using placeholder values since transaction creation failed")
    except Exception as e:
        logger.error(f"Error creating transaction: {e}")
        transaction = None
    
    # Print header
    print("\n" + "=" * 80)
    print("PRIORITY FEE STRATEGY COMPARISON")
    print("=" * 80)
    
    # Strategy 1: Legacy network congestion-based fee
    try:
        legacy_fee = get_optimal_compute_unit_price()
        print(f"\n1. LEGACY NETWORK CONGESTION-BASED FEE:")
        print(f"   Base fee: {legacy_fee} microlamports")
    except Exception as e:
        logger.error(f"Error getting legacy fee: {e}")
        legacy_fee = UNIT_PRICE
        print(f"\n1. LEGACY NETWORK CONGESTION-BASED FEE:")
        print(f"   Base fee (fallback): {legacy_fee} microlamports")
    
    # Strategy 2: Fixed fee with exponential backoff
    print(f"\n2. FIXED FEE WITH EXPONENTIAL BACKOFF:")
    base_fee = UNIT_PRICE
    print(f"   Base fee: {base_fee} microlamports")
    try:
        for attempt in range(1, 6):
            retry_fee = get_retry_compute_unit_price(attempt, base_fee)
            increase = ((retry_fee - base_fee) / base_fee) * 100
            print(f"   Attempt {attempt}: {retry_fee} microlamports ({increase:.1f}% increase)")
    except Exception as e:
        logger.error(f"Error calculating retry fees: {e}")
    
    # Strategy 3: Helius RPC priority fee estimates
    print(f"\n3. HELIUS PRIORITY FEE API ESTIMATES:")
    try:
        for level in ["Min", "Low", "Medium", "High", "VeryHigh", "UnsafeMax"]:
            try:
                fee = fee_manager.get_priority_fee(level)
                print(f"   {level}: {fee} microlamports")
            except Exception as e:
                logger.error(f"Error getting fee for level {level}: {e}")
    except Exception as e:
        logger.error(f"Error getting priority fees: {e}")
    
    # Strategy 4: Transaction-specific priority fee
    try:
        if transaction:
            tx_fee = fee_manager.get_priority_fee_for_transaction(transaction, "Medium")
            print(f"\n4. TRANSACTION-SPECIFIC PRIORITY FEE:")
            print(f"   Medium priority: {tx_fee} microlamports")
        else:
            print(f"\n4. TRANSACTION-SPECIFIC PRIORITY FEE:")
            print(f"   Not available (transaction creation failed)")
    except Exception as e:
        logger.error(f"Error getting transaction-specific fee: {e}")
    
    # Strategy 5: Recovery mode progression
    print(f"\n5. RECOVERY MODE PROGRESSION:")
    try:
        # Reset fee manager state
        fee_manager.failed_tx_count = 0
        fee_manager.in_recovery_mode = False
        fee_manager.current_level = "Medium"
        
        # Simulate failures
        print(f"   Initial state: Level={fee_manager.current_level}, Fee={fee_manager.get_priority_fee()}")
        for i in range(5):
            fee = fee_manager.handle_transaction_failure(Exception("compute budget exceeded"))
            print(f"   Failure {i+1}: Level={fee_manager.current_level}, "
                  f"Fee={fee}, RecoveryMode={fee_manager.in_recovery_mode}")
    except Exception as e:
        logger.error(f"Error simulating recovery mode: {e}")
    
    # Comparison table
    print("\n" + "=" * 80)
    print("SUMMARY COMPARISON")
    print("=" * 80)
    print(f"{'Strategy':<30} {'Normal':<15} {'Retry/High':<15} {'Recovery/VeryHigh':<20}")
    print("-" * 80)
    
    try:
        medium_fee = fee_manager.get_priority_fee('Medium')
        high_fee = fee_manager.get_priority_fee('High')
        very_high_fee = fee_manager.get_priority_fee('VeryHigh')
        
        print(f"{'Legacy (congestion)':<30} {legacy_fee:<15} {legacy_fee*1.5:<15.0f} {legacy_fee*3:<20.0f}")
        print(f"{'Fixed with backoff':<30} {base_fee:<15} {base_fee*1.5:<15.0f} {base_fee*3:<20.0f}")
        print(f"{'Helius API':<30} {medium_fee:<15.0f} {high_fee:<15.0f} {very_high_fee:<20.0f}")
        
        if transaction:
            tx_fee = fee_manager.get_priority_fee_for_transaction(transaction, "Medium")
            print(f"{'Transaction-specific':<30} {tx_fee:<15.0f} {'-':<15} {'-':<20}")
        else:
            print(f"{'Transaction-specific':<30} {'N/A':<15} {'-':<15} {'-':<20}")
    except Exception as e:
        logger.error(f"Error generating comparison table: {e}")
    
    print("=" * 80)
    
    # Recommendations
    print("\nRECOMMENDATIONS:")
    print("1. For normal operations: Use Helius API with 'Medium' priority level")
    print("2. For retry handling: Use Helius API with transaction-specific fee estimates")
    print("3. For recovery mode: Use PriorityFeeManager's built-in recovery handling")
    print("4. If Helius API is unavailable: Fall back to legacy computation as implemented")
    print("=" * 80 + "\n")
    
    # Clean up
    try:
        fee_manager.shutdown()
    except Exception as e:
        logger.error(f"Error shutting down fee manager: {e}")

if __name__ == "__main__":
    try:
        compare_fee_strategies()
    except Exception as e:
        logger.error(f"Unhandled exception: {e}")
        sys.exit(1) 