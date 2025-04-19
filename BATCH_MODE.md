# Batch Mode for Solana Volume Maker

## Overview

The Batch Mode is a specialized operating mode for the Solana Volume Maker that enables high-efficiency volume generation through multiple wallets in a single operation. Unlike the standard sequential mode, Batch Mode:

1. Creates multiple wallets (default: 5) at once
2. Funds all wallets in a single transaction from the main wallet
3. Performs buy operations with each wallet
4. Returns all funds back to the main wallet

This approach is significantly more efficient for generating volume quickly, with reduced overhead from wallet creation and management.

## Operation Modes

Batch Mode supports two distinct operation patterns:

### 1. Standard Mode

In standard mode, each batch wallet operates independently:
- Main wallet funds each batch wallet
- Each batch wallet independently buys tokens
- Each batch wallet sells tokens and returns funds to main wallet

### 2. Multi-Signature Mode (Default)

In multi-signature mode, transactions appear to be initiated by batch wallets but are actually controlled by the main wallet:
- Main wallet funds each batch wallet with a small amount (for fees)
- Main wallet creates token accounts and provides the SOL for swaps
- Batch wallets act as co-signers and appear as transaction initiators 
- Tokens are received directly by the main wallet
- Transactions appear on-chain as initiated by the batch wallets

The multi-signature approach mirrors the pattern observed in professional trading operations and has several advantages:
- More efficient fund management (main wallet retains most funds)
- Better attribution of volume to multiple wallets
- Reduced risk of funds being locked in batch wallets

## How It Works

The Batch Mode operation follows these steps:

1. **Wallet Generation**: Creates a specified number of new Solana wallets
2. **Multi-Send Transaction**: Funds all wallets at once using a single transaction from the main wallet
3. **Buy Operations**: Each wallet performs a buy operation for the target token (using standard or multi-sig approach)
4. **Return to Main Wallet**: In standard mode, funds are sold and transferred back; in multi-sig mode, they're already in the main wallet

## Requirements

- Solana blockchain only (not supported for EVM chains)
- Sufficient SOL balance in the main wallet (wallet #0 in config.json)
- Properly configured `config.json` with valid Solana settings

## Usage

### Command Line

You can run Batch Mode via the main `maker.py` script:

```bash
# Default (multi-signature mode)
python maker.py solana --mode batch --wallet-count 5 --amount-per-wallet 0.01

# Disable multi-signature mode
python maker.py solana --mode batch --wallet-count 5 --amount-per-wallet 0.01
```

Parameters:
- `--wallet-count`: Number of wallets to create (default: 5)
- `--amount-per-wallet`: Amount of SOL to send to each wallet (default: 0.01)

### Example Script

Alternatively, you can use the provided example script:

```bash
# Default (multi-signature mode)
python example_batch_mode.py --wallet-count 10 --amount-per-wallet 0.02

# Disable multi-signature mode
python example_batch_mode.py --wallet-count 10 --amount-per-wallet 0.02 --no-multi-sig
```

## Technical Details

### Multi-Transfer Transaction

Batch Mode uses a single transaction with multiple transfer instructions to fund all created wallets at once. This is more efficient than sending individual transactions and helps avoid transaction failures.

```python
# Transfer funds to multiple wallets in one transaction
transaction = self._create_multi_transfer_transaction(
    main_keypair,
    recipient_pubkeys,
    amount_in_lamports
)
```

### Multi-Signature Transaction Structure

In multi-signature mode, the transaction structure is more complex:

1. The main wallet creates and initializes a temporary WSOL account
2. The main wallet provides the SOL for the swap
3. The swap is executed with the main wallet as the token recipient
4. The batch wallet contributes a small transfer back to the main wallet
5. Both the main wallet and batch wallet sign the transaction

This creates an on-chain appearance that the batch wallet initiated the transaction, while the main wallet maintains control of the funds.

### Benefits

1. **Efficiency**: Creating and funding multiple wallets in a single transaction reduces time and fees
2. **Volume Generation**: Multiple wallets can generate more volume in a shorter time period
3. **Cleanup**: Automatic cleanup process returns all funds to the main wallet after operations complete
4. **Attribution**: In multi-sig mode, volume is correctly attributed to multiple wallet addresses

## Fund Recovery

To ensure you never lose funds if the program stops unexpectedly during batch operations, the system includes a robust recovery mechanism:

### Recovery Process

1. During batch mode operation, all batch wallet details are automatically saved to a recovery file (`batch_wallets_recovery.json`)
2. If the program crashes or is interrupted, you can run the recovery script to retrieve funds:

```bash
python recover_batch_wallets.py
```

The recovery script will:
1. Scan all batch wallets from the recovery file
2. Attempt to sell any tokens held in these wallets
3. Transfer all SOL back to your main wallet
4. Rename the recovery file to indicate it has been processed

### When to Use Recovery

You should run the recovery script if:
- The batch operation was interrupted unexpectedly
- You see a `batch_wallets_recovery.json` file in your directory
- You suspect funds might be stuck in temporary batch wallets

The recovery process is safe to run even if no funds are recoverable - it will simply check and report that no wallets need recovery.

## Troubleshooting

### Common Issues

1. **Insufficient Funds**
   - Ensure your main wallet has enough SOL to fund all batch wallets
   - For standard mode: (wallet_count × amount_per_wallet) + transaction fees
   - For multi-sig mode: More SOL is required in the main wallet for account creation and swaps

2. **Transaction Failures**
   - If the multi-transfer transaction fails, try reducing the number of wallets or the amount per wallet
   - For multi-sig mode, ensure batch wallets have at least 0.001 SOL for fees
   - Check the logs for specific error messages

3. **RPC Errors**
   - The system will automatically try alternative RPCs if available
   - Consider adding more alternative RPCs in your config file

### Logs

Detailed logs are written to:
- `batch_mode.log` (when using example script)
- `volume_maker.log` (when using maker.py)

## Security Considerations

- Private keys for all wallets are stored in `config.json`
- Ensure your environment is secure and this file is properly protected
- The batch mode does not persist the generated wallets between runs 