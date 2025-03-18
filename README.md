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

Recover funds to a specific address:

```bash
python recovery.py sonic -d 0xYOUR_DESTINATION_ADDRESS
```

Or use the default destination (first wallet in config):

```bash
python recovery.py sonic
```

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
- Supports multiple chains
- Includes gas optimization
- Handles failed transactions
- Comprehensive balance checking

## Safety Features

- Gas price monitoring and adjustment
- Transaction retry mechanism with backoff
- Balance checks before transactions
- RPC failover handling
- Comprehensive error logging
- Safety margins for native token transfers

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