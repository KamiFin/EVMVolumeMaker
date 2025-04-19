#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import logging
import time
import signal
import argparse
from datetime import datetime, timedelta

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(f"infinite_batch_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"),
        logging.StreamHandler()
    ]
)

logger = logging.getLogger("InfiniteBatchExample")

# Add the parent directory to the path so we can import the solana_volume_maker module
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# Import our volume maker
from solana_volume_maker import SolanaVolumeMaker
# Import necessary configuration from solana_config instead of SolanaConfig class
from solana_config import (
    CHAIN_NAME, RPC_URL, ALTERNATIVE_RPCS, DEX_TYPE, POOL_ADDRESS, 
    UNIT_BUDGET, UNIT_PRICE, MIN_BUY_AMOUNT, MAX_BUY_AMOUNT,
    TRANSFER_PERCENTAGE, WAIT_TIME, TRADE_WAIT_TIME, MAX_RETRIES,
    BACKOFF_FACTOR, MIN_BALANCE_THRESHOLD, BUY_SLIPPAGE, SELL_SLIPPAGE
)


def signal_handler(sig, frame):
    """
    Handle Ctrl+C (SIGINT) to gracefully exit the program
    """
    logger.info("Interrupt received, creating stop flag file...")
    with open("stop_infinite_mode.flag", "w") as f:
        f.write(f"Stop requested at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info("Waiting for the current cycle to complete...")


def main():
    """
    Run the infinite batch mode with command line arguments
    """
    parser = argparse.ArgumentParser(description="Run Solana Volume Maker in infinite batch mode")
    
    parser.add_argument("--wallets", type=int, default=5, help="Number of wallets to use per cycle (default: 5, max: 10)")
    parser.add_argument("--amount", type=float, default=0.001, help="Amount of SOL to use per wallet (default: 0.001)")
    parser.add_argument("--swap-amount", type=float, help="Optional specific swap amount (defaults to amount if not specified)")
    parser.add_argument("--no-multisig", action="store_false", dest="use_multisig", help="Disable multi-signature mode")
    parser.add_argument("--retries", type=int, default=3, help="Max retries per cycle (default: 3)")
    parser.add_argument("--pause", type=int, default=2, help="Seconds to pause between cycles (default: 2)")
    parser.add_argument("--hours", type=float, help="Maximum runtime in hours (default: run indefinitely)")
    parser.add_argument("--target-tx", type=int, help="Target number of transactions to complete before stopping")
    parser.add_argument("--token", type=str, help="Token address to buy (default: uses configured default token)")
    
    args = parser.parse_args()
    
    # Register signal handler for graceful shutdown
    signal.signal(signal.SIGINT, signal_handler)
    
    # Print configuration
    logger.info("Starting Solana Volume Maker in infinite batch mode with configuration:")
    logger.info(f"- Wallets per cycle: {args.wallets}")
    logger.info(f"- Amount per wallet: {args.amount} SOL")
    logger.info(f"- Swap amount: {args.swap_amount if args.swap_amount else args.amount} SOL")
    logger.info(f"- Using multi-signature: {args.use_multisig}")
    logger.info(f"- Max retries per cycle: {args.retries}")
    logger.info(f"- Pause between cycles: {args.pause} seconds")
    
    if args.hours:
        end_time = datetime.now() + timedelta(hours=args.hours)
        logger.info(f"- Maximum runtime: {args.hours} hours (until {end_time.strftime('%Y-%m-%d %H:%M:%S')})")
    else:
        logger.info("- No maximum runtime (will run until stopped or target reached)")
        
    if args.target_tx:
        logger.info(f"- Target transactions: {args.target_tx}")
    else:
        logger.info("- No transaction target (will run until stopped or time limit reached)")
    
    # Initialize volume maker with chain name
    volume_maker = SolanaVolumeMaker("solana", "buy", False)
    
    # Set token if specified
    if args.token:
        volume_maker.config.POOL_ADDRESS = args.token
        logger.info(f"- Using custom token address: {args.token}")
    else:
        logger.info(f"- Using default token address: {POOL_ADDRESS}")
    
    # Run infinite batch mode
    try:
        logger.info("Starting infinite batch mode...")
        stats = volume_maker.infinite_batch_mode(
            wallets_per_cycle=args.wallets,
            amount_per_wallet=args.amount,
            use_multi_sig=args.use_multisig,
            swap_amount=args.swap_amount,
            max_cycle_retries=args.retries,
            max_runtime_hours=args.hours,
            target_tx_count=args.target_tx,
            pause_between_cycles=args.pause
        )
        
        # Print summary statistics
        logger.info("Infinite batch mode completed!")
        logger.info(f"Total runtime: {stats['runtime_hours']} hours")
        logger.info(f"Successful transactions: {stats['successful_transactions']}")
        logger.info(f"Transaction rate: {stats['tx_per_hour']} tx/hour")
        logger.info(f"Overall success rate: {stats['success_rate']}")
        logger.info(f"Detailed results saved to: infinite_mode_results_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
        
    except Exception as e:
        logger.error(f"Error running infinite batch mode: {e}")
        return 1
    
    return 0


if __name__ == "__main__":
    sys.exit(main()) 