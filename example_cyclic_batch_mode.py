#!/usr/bin/env python3
import sys
import logging
import argparse
import json
from solana_volume_maker import SolanaVolumeMaker

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('cyclic_batch_mode.log')
    ]
)

logger = logging.getLogger(__name__)

def main():
    """Run the cyclic batch mode example"""
    parser = argparse.ArgumentParser(description="Run Solana Volume Maker in cyclic batch mode")
    parser.add_argument('--total-wallets', type=int, default=20, help='Total number of wallets to process (default: 20)')
    parser.add_argument('--wallets-per-cycle', type=int, default=5, help='Maximum wallets per cycle (default: 5, max: 10)')
    parser.add_argument('--amount-per-wallet', type=float, default=0.001, help='Amount of SOL to send to each wallet (default: 0.001)')
    parser.add_argument('--multi-sig', action='store_true', help='Use multi-signature mode (default: False)')
    parser.add_argument('--swap-amount', type=float, help='Specific amount to swap in SOL (default: 0.00001 SOL for multi-sig mode)')
    parser.add_argument('--max-cycle-retries', type=int, default=3, help='Maximum number of retries for failed cycles (default: 1)')
    parser.add_argument('--output-file', type=str, help='File to save statistics to (optional)')
    
    args = parser.parse_args()
    
    logger.info(f"Starting cyclic batch mode example with:")
    logger.info(f"- Total wallets: {args.total_wallets}")
    logger.info(f"- Wallets per cycle: {args.wallets_per_cycle}")
    logger.info(f"- Amount per wallet: {args.amount_per_wallet} SOL")
    logger.info(f"- Multi-sig mode: {args.multi_sig}")
    if args.swap_amount:
        logger.info(f"- Swap amount: {args.swap_amount} SOL")
    
    try:
        # Initialize the volume maker
        maker = SolanaVolumeMaker("solana", "buy", False)
        
        # Calculate total SOL needed
        total_sol = args.total_wallets * args.amount_per_wallet
        
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
        
        logger.info(f"This operation will create {args.total_wallets} wallets in batches of {args.wallets_per_cycle}")
        logger.info(f"Total SOL needed: approximately {total_sol} SOL plus fees")
        
        # Check if user wants to continue
        confirmation = input(f"Do you want to proceed with this operation? (yes/no): ")
        if confirmation.lower() not in ["yes", "y"]:
            logger.info("Operation cancelled by user")
            return
        
        # Run the cyclic batch mode
        logger.info(f"Starting cyclic batch mode with {args.total_wallets} total wallets and {args.wallets_per_cycle} wallets per cycle")
        stats = maker.cyclic_batch_mode(
            total_wallet_count=args.total_wallets,
            wallets_per_cycle=args.wallets_per_cycle,
            amount_per_wallet=args.amount_per_wallet,
            use_multi_sig=args.multi_sig,
            swap_amount=args.swap_amount,
            max_cycle_retries=args.max_cycle_retries
        )
        
        # Display summary
        logger.info("Cyclic batch mode completed")
        logger.info(f"Successful wallets: {stats['successful_wallets']}/{args.total_wallets} ({stats['success_rate']})")
        logger.info(f"Cycles completed: {stats['cycles_completed']}/{stats['cycles_planned']}")
        
        # Save statistics to file if requested
        if args.output_file:
            with open(args.output_file, 'w') as f:
                json.dump(stats, f, indent=4)
            logger.info(f"Statistics saved to {args.output_file}")
        
    except Exception as e:
        logger.error(f"Error running cyclic batch mode: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main() 