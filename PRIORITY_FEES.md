# Priority Fee Management with Helius RPC

This document explains how to use the priority fee management system with Helius RPC in EVMVolumeMaker.

## Overview

The priority fee manager provides intelligent fee estimation and management for Solana transactions. It includes features for:

- Auto-fetching priority fees every 5 minutes
- Dynamically adjusting fees based on transaction success rates
- Providing transaction-specific fee estimates
- Automatically entering recovery mode with higher fees when transactions fail
- Gradually returning to normal fees when network conditions improve

## Requirements

1. A Helius RPC URL with API key (`https://mainnet.helius-rpc.com/?api-key=YOUR_API_KEY`)
2. The URL must be configured in your `config.json` file

## Configuration

Add your Helius RPC URL to your `config.json` file in either:
- The main RPC URL: `chains.solana.rpc_url`
- Alternative RPCs: `chains.solana.alternative_rpcs`

Example:
```json
{
  "chains": {
    "solana": {
      "rpc_url": "https://mainnet.helius-rpc.com/?api-key=YOUR_API_KEY",
      "alternative_rpcs": [
        "https://api.mainnet-beta.solana.com",
        "https://solana-api.projectserum.com"
      ]
    }
  }
}
```

## Usage

The priority fee system is designed to be integrated seamlessly with the existing codebase. Here are different ways to use it:

### 1. Direct integration with Solana Volume Maker

```python
from utils.priority_fee_manager import PriorityFeeManager
from utils.solana_utils import set_priority_fee_manager

# Initialize the priority fee manager with Helius RPC
helius_url = "https://mainnet.helius-rpc.com/?api-key=YOUR_API_KEY"
fee_manager = PriorityFeeManager(helius_url)

# Register with the solana_utils module
set_priority_fee_manager(fee_manager)

# Now all transactions will automatically use the priority fee manager
# The existing code doesn't need any changes
```

### 2. Transaction-specific priority fees

```python
from utils.priority_fee_manager import PriorityFeeManager
from solders.compute_budget import set_compute_unit_price

# Initialize the fee manager
fee_manager = PriorityFeeManager(helius_url)

# Create your transaction
transaction = create_transaction()

# Get priority fee for this specific transaction
priority_fee = fee_manager.get_priority_fee_for_transaction(transaction, "Medium")

# Add the compute unit price instruction
transaction.add(set_compute_unit_price(int(priority_fee)))
```

### 3. Handling transaction failures

```python
try:
    # Send transaction
    signature = client.send_transaction(transaction, keypair)
    
    # Handle success
    fee_manager.handle_transaction_success()
    
except Exception as e:
    # Handle failure - this will automatically adjust the fee level if needed
    new_fee = fee_manager.handle_transaction_failure(e)
    logger.info(f"Will use new fee for retry: {new_fee}")
```

## Priority Levels

The priority fee manager supports these levels:
- `"Min"`: Minimum fees (0th percentile)
- `"Low"`: Low priority (25th percentile)
- `"Medium"`: Medium priority (50th percentile) - Default
- `"High"`: High priority (75th percentile)
- `"VeryHigh"`: Very high priority (95th percentile)
- `"UnsafeMax"`: Maximum observed fee (100th percentile) - Use with caution

Example:
```python
# Get fee for a specific level
high_fee = fee_manager.get_priority_fee("High")

# Manually set the global priority level
fee_manager.set_priority_level("High")
```

## Recovery Mode

The system automatically enters recovery mode when multiple transaction failures occur. In recovery mode:
1. The priority level is automatically increased
2. Transaction fees are higher to ensure transactions succeed
3. The system gradually returns to normal fees when transactions start succeeding

## Examples

See these files for complete examples:
- `examples/priority_fee_example.py`: Basic example
- `examples/helius_priority_fee_integration.py`: Integration with Solana Volume Maker
- `utils/test_fee_comparison.py`: Compare different fee strategies

## Testing

To test the priority fee system:

```bash
# Compare different fee strategies
python -m utils.test_fee_comparison

# Run the basic example
python examples/priority_fee_example.py

# Try the integration example
python examples/helius_priority_fee_integration.py
``` 