#!/usr/bin/env python3
"""
Batch Wallet Recovery Script

This script is used to recover funds from batch wallets if the program
was interrupted during batch mode operation.

Usage:
    python recover_batch_wallets.py [--use-multisig] [--swap-amount AMOUNT] [--file FILE]

Options:
    --use-multisig     Use multi-signature buy/sell method for recovery (recommended)
    --swap-amount      Specific amount of SOL to use for swap (default: 0.00001)
    --file             Recovery file to use. Options:
                       'batch' - Use batch_wallets_recovery.json (default)
                       'failed' - Use failed_batch_wallets.json

Recovery Files:
    - batch_wallets_recovery.json: Contains wallets from the last batch mode run
    - failed_batch_wallets.json: Contains all batch wallets that have failed

Recovery Methods:
    1. Standard Recovery:
       - Each batch wallet sells tokens individually
       - Proceeds are transferred back to main wallet
       - May not succeed if there are issues with token balances or permissions
       
    2. Multi-Signature Recovery (Recommended):
       - Uses advanced multi-signature mechanism mimicking professional trading ops
       - Main wallet creates transactions, batch wallets appear as initiators
       - More effective at recovering tokens and emptying batch wallets
       - Works even in cases where standard recovery fails
"""

import argparse
import logging
import sys
import json
import os
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

def parse_args():
    """
    Parse command line arguments for the recovery script.
    
    Returns:
        argparse.Namespace: Parsed command line arguments
    """
    parser = argparse.ArgumentParser(description="Recover funds from batch wallets")
    parser.add_argument("--use-multisig", action="store_true", 
                        help="Use multi-signature buy/sell method for recovery (recommended)")
    parser.add_argument("--swap-amount", type=float, default=0.00001,
                        help="Specific amount of SOL to use for swap (default: 0.00001)")
    parser.add_argument("--file", type=str, choices=['batch', 'failed'], default='batch',
                        help="Recovery file to use: 'batch' for batch_wallets_recovery.json or 'failed' for failed_batch_wallets.json")
    return parser.parse_args()

def main():
    """
    Run the batch wallet recovery process.
    
    This function:
    1. Parses command line arguments
    2. Loads wallet data from the selected recovery file
    3. Initializes the SolanaVolumeMaker
    4. Executes the recovery process based on the selected mode
    5. Reports success/failure
    
    Returns:
        bool: True if recovery succeeded, False otherwise
    """
    
    # Parse command line arguments
    args = parse_args()
    use_multisig = args.use_multisig
    swap_amount = args.swap_amount
    
    # Determine which recovery file to use based on the --file argument
    if args.file == 'batch':
        recovery_file = "batch_wallets_recovery.json"
        file_desc = "last batch mode run"
    else:  # args.file == 'failed'
        recovery_file = "failed_batch_wallets.json"
        file_desc = "all failed batch operations"
    
    try:
        # Banner - Display script information
        print("\n" + "="*60)
        print("  SOLANA VOLUME MAKER - BATCH WALLET RECOVERY TOOL")
        print("="*60)
        print("\nThis tool will attempt to recover funds from batch wallets")
        print("if the program was interrupted during batch mode operation.\n")
        
        # Check if the recovery file exists
        if not os.path.exists(recovery_file):
            print(f"ERROR: {recovery_file} not found. Cannot proceed with recovery.")
            logger.error(f"{recovery_file} file not found")
            return False
        
        print(f"Using recovery file: {recovery_file} (contains wallets from {file_desc})")
        
        # Recovery mode explanation - describe the selected recovery method
        if use_multisig:
            print("\nUsing MULTI-SIGNATURE recovery mode:")
            print("- Main wallet creates accounts and provides token liquidity")
            print("- Batch wallets act as co-signers and appear as initiators")
            print("- Tokens are directly received by the main wallet")
            print(f"- Using swap amount of {swap_amount} SOL for transactions")
        else:
            print("\nUsing STANDARD recovery mode:")
            print("- Each batch wallet independently sells tokens")
            print("- Proceeds are transferred back to main wallet")
        
        # Confirm user wants to proceed
        print("\nWARNING: This will attempt to recover funds from batch wallets and transfer")
        print("all funds back to the main wallet in your config.json file.")
        confirmation = input("\nDo you want to proceed? (yes/no): ")
        
        if confirmation.lower() not in ["yes", "y"]:
            logger.info("Recovery cancelled by user")
            print("\nRecovery cancelled.")
            return False
            
        # Initialize the volume maker
        # The SolanaVolumeMaker handles all interaction with the Solana blockchain
        print("\nInitializing Solana Volume Maker...")
        maker = SolanaVolumeMaker("solana", "buy", False)
        
        # Load the recovery file manually to check contents before proceeding
        # This ensures we have valid wallet data before attempting recovery
        with open(recovery_file, 'r') as f:
            recovery_data = json.load(f)
            
        # Different recovery files have different structures
        if args.file == 'batch':
            if 'batch_wallets' not in recovery_data or not recovery_data['batch_wallets']:
                print("ERROR: No batch wallets found in recovery file.")
                logger.error("No batch wallets found in recovery file")
                return False
            batch_wallets = recovery_data['batch_wallets']
        else:  # args.file == 'failed'
            # Handle the new structure of failed_batch_wallets.json
            if 'wallets' not in recovery_data or not recovery_data['wallets']:
                print("ERROR: No failed wallets found in recovery file.")
                logger.error("No failed wallets found in recovery file")
                return False
            batch_wallets = recovery_data['wallets']
            
        print(f"\nFound {len(batch_wallets)} batch wallets in recovery file.")
        
        # Set the batch wallets in the maker object
        # This allows the SolanaVolumeMaker to use these wallets for operations
        maker.batch_wallets = batch_wallets
        
        # Execute recovery based on selected mode
        print("\nStarting recovery process...\n")
        
        if use_multisig:
            # Multi-signature recovery mode
            # This uses a more sophisticated approach that can recover funds
            # even in cases where the standard approach fails
            logger.info("Using multi-signature mode for recovery")
            print("Using multi-signature mode for better recovery...")
            
            success_count = 0
            total_wallets = len(batch_wallets)
            failed_wallets_info = []
            
            # Process each wallet with multi-signature buy
            # The multi-signature approach creates transactions that appear to be
            # from the batch wallet but are actually controlled by the main wallet
            for i, wallet in enumerate(batch_wallets):
                print(f"Processing wallet {i+1}/{total_wallets}: {wallet['address']}")
                logger.info(f"Processing batch wallet {i}: {wallet['address']} with multi-sig")
                
                try:
                    # Prepare parameters for multi-signature buy
                    # Create a temporary index entry in batch_wallets
                    tmp_index = len(maker.batch_wallets)
                    maker.batch_wallets.append(wallet)
                    
                    # Execute multi-signature buy to recover funds
                    # This mimics the pattern used by professional trading operations
                    # and is more effective at emptying wallets completely
                    success = maker._batch_multi_sig_buy(tmp_index, swap_amount)
                    
                    # Remove the temporary wallet after use
                    maker.batch_wallets.pop()
                    
                    if success:
                        print(f"✓ Successfully recovered funds from wallet {i+1}")
                        logger.info(f"Successfully recovered funds from wallet {i}")
                        success_count += 1
                    else:
                        print(f"✗ Failed to recover funds from wallet {i+1}")
                        logger.warning(f"Failed to recover funds from wallet {i}")
                        # Track failed wallets for later reference
                        failed_wallets_info.append({
                            "address": wallet["address"],
                            "private_key": wallet["private_key"],
                            "reason": "Multi-signature buy failed"
                        })
                        
                except Exception as e:
                    print(f"✗ Error processing wallet {i+1}: {str(e)}")
                    logger.error(f"Error in multi-sig recovery for wallet {i}: {str(e)}")
                    # Track failed wallets for later reference
                    failed_wallets_info.append({
                        "address": wallet["address"],
                        "private_key": wallet["private_key"],
                        "reason": str(e)
                    })
            
            # Save failed wallets to a file if any
            if failed_wallets_info:
                import time
                timestamp = time.strftime("%Y%m%d-%H%M%S")
                failed_file = f"recovery_failed_wallets_{timestamp}.json"
                with open(failed_file, 'w') as f:
                    json.dump({
                        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                        "wallets": failed_wallets_info
                    }, f, indent=2)
                print(f"\nSaved {len(failed_wallets_info)} failed wallets to {failed_file}")
                logger.info(f"Saved {len(failed_wallets_info)} failed wallets to {failed_file}")
            
            # Determine overall success based on number of wallets processed
            if success_count > 0:
                success = True
                print(f"\nSuccessfully recovered funds from {success_count}/{total_wallets} wallets.")
            else:
                success = False
                print("\nFailed to recover funds from any wallets.")
                
        else:
            # Standard recovery process
            # This uses the built-in recover_batch_wallets function which:
            # 1. Checks balances of all batch wallets
            # 2. Attempts to sell any tokens found
            # 3. Transfers remaining SOL back to main wallet
            logger.info("Using standard mode for recovery")
            print("Using standard recovery process...")
            
            # For the failed wallets file, we need to pass the wallets directly to the recovery function
            if args.file == 'batch':
                # Use the built-in method for batch wallets recovery file
                success = maker.recover_batch_wallets()
            else:  # args.file == 'failed'
                # Convert wallet list to the format expected by _recover_failed_batch_wallets
                failed_wallets_list = []
                for i, wallet in enumerate(batch_wallets):
                    # Each wallet entry needs wallet_index, address, and amount
                    failed_wallets_list.append({
                        "wallet_index": i,
                        "address": wallet["address"],
                        "amount": wallet.get("amount", swap_amount)
                    })
                
                # Add wallets to maker.batch_wallets
                maker.batch_wallets = batch_wallets
                
                # Call recovery with properly formatted wallet list
                recovered_count, recovery_attempted, recovery_success_rate = maker._recover_failed_batch_wallets(
                    failed_wallets_list, 
                    use_multi_sig=True, 
                    swap_amount=swap_amount
                )
                
                success = recovered_count > 0
                print(f"\nRecovered {recovered_count}/{recovery_attempted} wallets ({recovery_success_rate:.2f}% success rate)")
        
        # Cleanup - rename the recovery file once processed to prevent re-use
        if success:
            try:
                import time
                timestamp = time.strftime("%Y%m%d-%H%M%S")
                os.rename(recovery_file, f"{recovery_file}.{timestamp}.bak")
                logger.info(f"Renamed {recovery_file} to {recovery_file}.{timestamp}.bak")
                print(f"\nRenamed {recovery_file} to {recovery_file}.{timestamp}.bak")
            except Exception as e:
                logger.warning(f"Could not rename recovery file: {str(e)}")
        
        # Final status message
        if success:
            print("\n✓ Recovery operation completed successfully!")
            print("  Check batch_recovery.log for details.")
        else:
            print("\n✗ Recovery operation failed or no wallets to recover.")
            print("  Check batch_recovery.log for details.")
            
        return success
            
    except Exception as e:
        # Log any unexpected errors that occur during recovery
        logger.error(f"Error in recovery: {e}")
        print(f"\n✗ Error during recovery: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    main() 