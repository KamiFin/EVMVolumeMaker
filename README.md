# EVM Chains | Trading and Recovery Tools

A collection of Python scripts for automated trading and fund recovery on various blockchain networks. This project includes tools for volume making, token sniping, and wallet recovery operations.

## Features

- Multi-chain support through configuration
- Volume making with automatic wallet generation
- Token sniping with customizable parameters
- Wallet recovery tool for both native tokens and ERC20 tokens
- RPC failover with automatic switching
- Comprehensive logging
- Gas optimization

## Prerequisites

- Python 3.8+
- pip (Python package installer)
- A valid configuration file (`config.json`)

## Installation

1. Clone the repository:
```bash
git clone <repository-url>
cd <repository-name>
```

2. Create and activate a virtual environment (recommended):
```bash
# On Windows
python -m venv venv
.\venv\Scripts\activate

# On macOS/Linux
python3 -m venv venv
source venv/bin/activate
```

3. Install dependencies:
```bash
pip install web3
pip install requests
```

# Or install all dependencies at once using requirements.txt:
```bash
pip install -r requirements.txt
```

4. Create your configuration file (`config.json`) following the template structure.

## Configuration

The `config.json` file contains all necessary settings for different chains. Example structure:

```json
{
    "chains": {
        "sonic": {
            "name": "Sonic Chain",
            "rpc_url": "https://rpc.example.com",
            "chain_id": 146,
            "native_token": "SONIC",
            "explorer_url": "https://explorer.example.com",
            "alternative_rpcs": [
                "https://rpc1.example.com",
                "https://rpc2.example.com"
            ],
            "dex": {
                "router_address": "0x...",
                "wrapped_native_token": "0x...",
                "router_abi": [...]
            },
            "token": {
                "TOKEN_NAME": {
                    "contract_address": "0x...",
                    "name": "Token Name",
                    "symbol": "SYMBOL"
                }
            },
            "transaction": {
                "buy_amount": 1e-10,
                "transfer_percentage": 0.998,
                "gas_multiplier": 1.1,
                "wait_time": 3,
                "max_retries": 3,
                "backoff_factor": 2
            }
        }
    },
    "wallets": [
        {
            "address": "0x...",
            "private_key": "0x..."
        }
    ]
}
```

## Usage

### Volume Maker

Run the volume maker script for a specific chain:

```bash
python maker.py sonic
```

You can also specify the operation mode:

```bash
# Buy tokens (default mode)
python maker.py sonic --mode buy

# Sell tokens
python maker.py sonic --mode sell

# Buy then sell (trade mode)
python maker.py sonic --mode trade

# Use single wallet mode (no new wallet creation or fund transfers)
python maker.py sonic --single-wallet

# Combine modes
python maker.py sonic --mode trade --single-wallet
```

Different modes:
- `buy`: Purchases tokens and transfers funds to a new wallet
- `sell`: Sells tokens and transfers remaining funds to a new wallet
- `trade`: First buys tokens, then sells them before transferring funds to a new wallet
- `single-wallet`: Uses only the first wallet without creating new ones or transferring funds

### Token Sniper

Run the sniper in monitoring mode:

```bash
python sniper.py sonic y
```

Or execute a direct buy:

```bash
python sniper.py sonic n
```

### Wallet Recovery

Recover native tokens to a specific address:

```bash
python recovery.py sonic -d 0xYOUR_DESTINATION_ADDRESS
```

Recover both native and token balances:

```bash
python recovery.py sonic -d 0xYOUR_DESTINATION_ADDRESS --with-tokens
```

Or use the short form:
```bash
python recovery.py sonic -d 0xYOUR_DESTINATION_ADDRESS -t
```

Use the default destination (first wallet in config):

```bash
python recovery.py sonic
```

By default:
- Only native tokens are recovered (more gas efficient)
- The first wallet in your config is preserved and excluded from recovery
- Funds are sent to the first wallet if no destination is specified

Additional options:
```bash
# Include first wallet in recovery (not recommended):
python recovery.py sonic --include-first

# Recover everything to a specific address:
python recovery.py sonic -d 0xADDRESS --with-tokens
```

## Chain-Specific Considerations
The tools automatically adapt to different chains:
- BSC: Applies PoA middleware, optimizes gas prices (capped at 5 Gwei), uses higher gas limits for token transfers
- Polygon: Uses minimum gas prices of 30 Gwei to prevent stuck transactions
- Base: Uses lower gas prices suitable for L2
- Sonic: Handles specialized DEX interface structure

## Special Token Handling
Some tokens require special handling due to their transfer mechanisms:
- DAWAE Token: Uses 200,000 gas limit on BSC (instead of standard 100,000)
- Tokens with Transfer Fees: Scripts automatically handle tokens with transfer taxes/fees

## Gas Efficiency
- Default mode recovers only native tokens to minimize gas costs
- Use --with-tokens flag when you need to recover both native and token balances
- The script optimizes gas usage by:
  - Prioritizing wallets with sufficient gas
  - Batching operations efficiently
  - Using chain-specific gas strategies

## Script Descriptions

### maker.py
- Implements automated volume making strategy
- Generates new wallets dynamically
- Handles buy/sell operations
- Includes RPC failover mechanism
- Comprehensive logging

### sniper.py
- Monitors and executes token purchases
- Supports multiple trading functions
- Includes slippage protection
- Gas optimization features
- Error handling and retries

### recovery.py
- Recovers both native and ERC20 tokens
- Multi-phase recovery strategy:
  - First recovers tokens from wallets with sufficient gas
  - Recovers native tokens from wallets with significant balances
  - Funds wallets with tokens but insufficient gas
  - Finally sweeps all remaining native tokens
- Adaptive gas strategies
- Chain-specific optimizations
- Token-specific handling

## Safety Features

- Gas price monitoring and adjustment
- Adaptive safety margins for native token transfers
- Transaction retry mechanism with backoff
- Balance checks before transactions
- RPC failover handling
- Comprehensive error logging
- Protection of first wallet (designated as the main wallet)

## Troubleshooting

### Common Issues

- Out of Gas Errors:
  - For tokens like DAWAE, increase gas limit to 200,000+
  - In config.json, add a higher gas limit for specific chains
- PoA Chain Errors:
  - The script automatically applies PoA middleware for BSC and Polygon
If you see errors about "extraData", check that middleware is correctly applied
Unable to Recover Tokens:
Ensure destination wallet has enough native tokens to fund other wallets
Check token contract is valid and supports standard ERC20 functions
RPC Connection Issues:
Add multiple alternative RPCs in your config
The script will automatically rotate through available endpoints

## Error Handling

The scripts include comprehensive error handling:
- Network connectivity issues
- RPC endpoint failures
- Transaction failures
- Gas estimation errors
- Balance insufficiency

## Logging

All operations are logged to both console and file:
- Transaction details
- Balance changes
- Error messages
- RPC switching events
- Operation status

## Best Practices

1. Always test with small amounts first
2. Keep your private keys secure
3. Monitor gas prices
4. Check transaction status on block explorer
5. Maintain sufficient native tokens for gas
6. Regular backup of wallet information

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

## License

[Your chosen license]

## Disclaimer

This software is for educational purposes only. Use at your own risk. Always verify transactions and configurations before executing operations with real assets. 