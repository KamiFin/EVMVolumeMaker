#!/usr/bin/env python3
"""
Batch Wallet Recovery Script

This script is used to recover funds from batch wallets if the program
was interrupted during batch mode operation.

Usage:
    python recover_batch_wallets.py

Requirements:
    - A batch_wallets_recovery.json file must exist in the current directory
    - This file is automatically created during batch mode operations
"""

import logging
import sys
from solana_volume_maker import SolanaVolumeMaker

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("batch_recovery.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

def main():
    """Run the batch wallet recovery process"""
    
    try:
        # Banner
        print("\n" + "="*60)
        print("  SOLANA VOLUME MAKER - BATCH WALLET RECOVERY TOOL")
        print("="*60)
        print("\nThis tool will attempt to recover funds from batch wallets")
        print("if the program was interrupted during batch mode operation.\n")
        
        # Check if user wants to continue
        print("WARNING: This will sell any tokens in batch wallets and transfer")
        print("all funds back to the main wallet in your config.json file.")
        confirmation = input("\nDo you want to proceed? (yes/no): ")
        
        if confirmation.lower() not in ["yes", "y"]:
            logger.info("Recovery cancelled by user")
            print("\nRecovery cancelled.")
            return
            
        # Initialize the volume maker
        print("\nInitializing Solana Volume Maker...")
        maker = SolanaVolumeMaker("solana", "buy", False)
        
        # Run the recovery function
        print("Scanning for batch wallets to recover...\n")
        success = maker.recover_batch_wallets()
        
        if success:
            print("\n✓ Recovery operation completed successfully!")
            print("  Check batch_recovery.log for details.")
        else:
            print("\n✗ Recovery operation failed or no wallets to recover.")
            print("  Check batch_recovery.log for details.")
            
    except Exception as e:
        logger.error(f"Error in recovery: {e}")
        print(f"\n✗ Error during recovery: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main() 