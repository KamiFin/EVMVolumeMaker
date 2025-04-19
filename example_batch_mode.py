#!/usr/bin/env python3
"""
Example script to demonstrate the batch mode in Solana Volume Maker.
This mode creates multiple wallets, funds them all at once, performs buys,
and then returns funds to the main wallet.

Requirements:
- Sufficient SOL balance in the main wallet (first wallet in config.json)
- Properly configured config.json with Solana settings
"""

import logging
import argparse
import sys
from solana_volume_maker import SolanaVolumeMaker

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('batch_mode.log')
    ]
)
logger = logging.getLogger(__name__)

def main():
    """Run the batch mode example"""
    parser = argparse.ArgumentParser(description="Run Solana Volume Maker in batch mode")
    parser.add_argument('--wallet-count', type=int, default=5, help='Number of batch wallets to create (default: 5)')
    parser.add_argument('--amount-per-wallet', type=float, default=0.001, help='Amount of SOL to send to each wallet (default: 0.001)')
    parser.add_argument('--multi-sig', action='store_true', help='Use multi-signature mode (default: False)')
    parser.add_argument('--swap-amount', type=float, help='Specific amount to swap in SOL (default: 0.00001 SOL for multi-sig mode)')
    
    args = parser.parse_args()
    
    logger.info(f"Starting batch mode example with:")
    logger.info(f"- Wallet count: {args.wallet_count}")
    logger.info(f"- Amount per wallet: {args.amount_per_wallet} SOL")
    logger.info(f"- Multi-sig mode: {args.multi_sig}")
    if args.swap_amount:
        logger.info(f"- Swap amount: {args.swap_amount} SOL")
    
    try:
        # Initialize the volume maker
        maker = SolanaVolumeMaker("solana", "buy", False)
        
        # Calculate total SOL needed
        total_sol = args.wallet_count * args.amount_per_wallet
        
        # Add explanation for multi-signature mode
        if args.multi_sig:
            logger.info("Using MULTI-SIGNATURE mode where:")
            logger.info("- Main wallet creates accounts and provides token liquidity")
            logger.info("- Batch wallets act as co-signers and appear as initiators")
            logger.info("- Tokens are directly received by the main wallet")
            logger.info("- Buy transactions appear on-chain as initiated by the batch wallets")
            if args.swap_amount:
                logger.info(f"- Using exact swap amount of {args.swap_amount} SOL")
            else:
                logger.info(f"- Using default swap amount of 0.00001 SOL (very small transaction)")
        else:
            logger.info("Using STANDARD mode where:")
            logger.info("- Each batch wallet independently buys tokens")
            logger.info("- Main wallet only provides initial funding")
            logger.info("- Tokens need to be sold and transferred back to main wallet")
        
        logger.info(f"This operation will create {args.wallet_count} wallets and use approximately {total_sol} SOL")
        
        # Check if user wants to continue
        confirmation = input(f"Do you want to proceed with this operation? (yes/no): ")
        if confirmation.lower() not in ["yes", "y"]:
            logger.info("Operation cancelled by user")
            return
        
        # Run the batch mode
        logger.info(f"Starting batch mode with {args.wallet_count} wallets and {args.amount_per_wallet} SOL per wallet")
        success = maker.batch_mode(
            args.wallet_count, 
            args.amount_per_wallet, 
            args.multi_sig,
            args.swap_amount
        )
        
        if success:
            logger.info("Batch mode completed successfully")
        else:
            logger.error("Batch mode failed")
            sys.exit(1)
            
    except Exception as e:
        logger.error(f"Error running batch mode: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main() 