import argparse
import sys

def main():
    """
    Main entry point for the volume maker CLI.
    Parses command line arguments and executes the appropriate mode.
    """
    parser = argparse.ArgumentParser(description='Crypto Volume Maker CLI')
    
    # Required arguments
    parser.add_argument('chain', choices=['solana', 'eth', 'bsc', 'polygon', 'arbitrum', 'optimism', 'base', 'avalanche'],
                        help='Blockchain to use (solana or EVM chains)')
    parser.add_argument('mode', choices=['swap', 'transfer'],
                        help='Operation mode: swap tokens or transfer native token')
    
    # Optional arguments
    parser.add_argument('-c', '--config', default='config.yaml',
                        help='Path to configuration file (default: config.yaml)')
    parser.add_argument('-i', '--infinite', action='store_true',
                        help='Run in infinite batch mode')
    parser.add_argument('-w', '--wallets',
                        help='Number of wallets to process per cycle in infinite mode (default: 3)')
    parser.add_argument('-a', '--amount',
                        help='Amount of native token per wallet in infinite mode')
    parser.add_argument('-r', '--runtime',
                        help='Maximum runtime in hours for infinite mode (optional)')
    parser.add_argument('-t', '--target',
                        help='Target number of transactions for infinite mode (optional)')
    parser.add_argument('-p', '--pause',
                        help='Pause between cycles in seconds for infinite mode (default: 30)')
    parser.add_argument('-rt', '--retries',
                        help='Maximum number of retries per cycle in infinite mode (default: 1)')
    
    args = parser.parse_args()

    # Check if the chain is supported
    print(f"Selected chain: {args.chain}")
    if args.chain == 'solana':
        from solana_volume_maker import SolanaVolumeMaker
        maker = SolanaVolumeMaker(
            mode=args.mode,
            config_file=args.config
        )
    elif args.chain in ['eth', 'bsc', 'polygon', 'arbitrum', 'optimism', 'base', 'avalanche']:
        from evm_volume_maker import EVMVolumeMaker
        maker = EVMVolumeMaker(
            chain=args.chain,
            mode=args.mode,
            config_file=args.config
        )
    else:
        print(f"Unsupported chain: {args.chain}")
        parser.print_help()
        sys.exit(1)

    # Execute based on the options
    if args.infinite:
        # Parse infinite mode arguments from the command line
        wallets_per_cycle = int(args.wallets) if args.wallets else 3
        max_runtime_hours = float(args.runtime) if args.runtime else None
        target_tx_count = int(args.target) if args.target else None
        pause_between_cycles = int(args.pause) if args.pause else 30
        max_cycle_retries = int(args.retries) if args.retries else 1
        amount_per_wallet = float(args.amount) if args.amount else None
        
        # Use the default amount if none specified
        if amount_per_wallet is None:
            if args.chain == 'solana':
                amount_per_wallet = 0.001  # SOL default amount
            else:
                # Default amounts for EVM chains
                defaults = {
                    'eth': 0.001,      # ETH
                    'bsc': 0.005,      # BNB
                    'polygon': 0.1,    # MATIC
                    'arbitrum': 0.001, # ETH
                    'optimism': 0.001, # ETH
                    'base': 0.001,     # ETH
                    'avalanche': 0.05  # AVAX
                }
                amount_per_wallet = defaults.get(args.chain, 0.001)
        
        print(f"Starting infinite batch mode with {wallets_per_cycle} wallets per cycle")
        print(f"Amount per wallet: {amount_per_wallet} {maker.native_token_symbol}")
        if max_runtime_hours:
            print(f"Maximum runtime: {max_runtime_hours} hours")
        if target_tx_count:
            print(f"Target transaction count: {target_tx_count}")
        print(f"Pause between cycles: {pause_between_cycles} seconds")
        print(f"Max cycle retries: {max_cycle_retries}")
        
        try:
            stats = maker.infinite_batch_mode(
                wallets_per_cycle=wallets_per_cycle,
                amount_per_wallet=amount_per_wallet,
                max_cycle_retries=max_cycle_retries,
                max_runtime_hours=max_runtime_hours,
                target_tx_count=target_tx_count,
                pause_between_cycles=pause_between_cycles
            )
            print(f"Infinite batch mode completed with {stats['successful_transactions']} successful transactions")
        except KeyboardInterrupt:
            print("Interrupted by user. Exiting...")
        except Exception as e:
            print(f"Error in infinite batch mode: {e}")
    else:
        # Run in regular mode
        try:
            maker.run()
            print("Operation completed successfully")
        except KeyboardInterrupt:
            print("Interrupted by user. Exiting...")
        except Exception as e:
            print(f"Error during operation: {e}")
            sys.exit(1)

if __name__ == "__main__":
    main() 