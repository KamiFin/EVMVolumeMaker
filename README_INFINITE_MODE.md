# Solana Volume Maker - Infinite Batch Mode

This document explains how to use the infinite batch mode feature of the Solana Volume Maker, which allows continuous transaction volume generation without stopping.

## Overview

The infinite batch mode runs wallet transactions continuously in cycles until one of the following conditions is met:
- The user stops the process (by creating a stop flag file or sending CTRL+C)
- A maximum runtime is reached (if specified)
- A target number of transactions is completed (if specified)

This mode is useful for:
- Extended volume generation for testing
- Long-running operations to build trading volume
- Automated liquidity generation on DEXes

## Usage

### Running the Example Script

The simplest way to use infinite batch mode is with the provided example script:

```bash
python example_infinite_batch_mode.py [OPTIONS]
```

### Available Options

| Option | Description | Default |
|--------|-------------|---------|
| `--wallets` | Number of wallets to use per cycle | 5 (max: 10) |
| `--amount` | Amount of SOL to use per wallet | 0.001 |
| `--swap-amount` | Optional specific swap amount | Same as amount |
| `--no-multisig` | Disable multi-signature mode | Multi-sig enabled |
| `--retries` | Max retries per cycle | 3 |
| `--pause` | Seconds to pause between cycles | 15 |
| `--hours` | Maximum runtime in hours | No limit |
| `--target-tx` | Target number of transactions | No limit |
| `--token` | Token address to buy | Uses default from config |

### Example Commands

Basic usage with defaults:
```bash
python example_infinite_batch_mode.py
```

Run for 2 hours with 3 wallets per cycle:
```bash
python example_infinite_batch_mode.py --wallets 3 --hours 2
```

Run until 500 transactions are completed:
```bash
python example_infinite_batch_mode.py --target-tx 500
```

Advanced configuration:
```bash
python example_infinite_batch_mode.py --wallets 7 --amount 0.002 --swap-amount 0.0015 --retries 2 --pause 30 --no-multisig
```

### Stopping the Process

You can stop the infinite batch mode in two ways:

1. Press `CTRL+C` in the terminal (recommended)
2. Create a file named `stop_infinite_mode.flag` in the same directory

The process will gracefully complete the current cycle before stopping.

## Monitoring

The infinite batch mode provides extensive monitoring capabilities:

- Real-time terminal output with transaction statistics
- Detailed logging to a timestamped log file
- JSON results file with comprehensive statistics after completion
- Flag file (`infinite_mode_running.flag`) to indicate active operation

## Understanding Statistics

The final statistics include:

- Total runtime in hours
- Completed cycles and transactions
- Success and failure rates
- Transaction rate (TX per hour)
- Start and end timestamps

## Safety Features

The infinite batch mode includes several safety features:

- Graceful shutdown with CTRL+C
- Maximum runtime limit option
- Transaction target limit option
- Flag files for monitoring process state
- Exception handling with error logging
- Pause between cycles to prevent rate limiting
- Retry mechanism for failed cycles

## Integration with Other Scripts

You can also use the infinite batch mode directly in your own scripts:

```python
from solana_volume_maker import SolanaVolumeMaker

# Initialize for Solana
maker = SolanaVolumeMaker("solana", "buy", False)

stats = maker.infinite_batch_mode(
    wallets_per_cycle=5,
    amount_per_wallet=0.001,
    use_multi_sig=True,
    max_runtime_hours=24,
    pause_between_cycles=15
)

print(f"Completed {stats['successful_transactions']} transactions")
```

## Troubleshooting

If you encounter issues:

1. Check the log file for detailed error messages
2. Ensure you have sufficient SOL in your accounts
3. Try increasing the pause between cycles if you hit rate limits
4. Check if any stop flag files exist in the directory
5. Verify network connectivity to Solana RPC nodes
6. Make sure your token address is correct if you specified a custom one 