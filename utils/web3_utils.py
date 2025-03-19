"""Utility functions for Web3 initialization and configuration"""

import logging
from web3 import Web3
from web3.middleware import geth_poa_middleware

logger = logging.getLogger(__name__)

def get_web3_connection(rpc_url: str, chain_id: int) -> Web3:
    """
    Initialize Web3 connection with proper middleware configuration.
    
    Args:
        rpc_url: RPC endpoint URL
        chain_id: Chain ID to determine middleware needs
        
    Returns:
        Web3 instance with proper configuration
    """
    w3 = Web3(Web3.HTTPProvider(rpc_url))
    
    # Add PoA middleware for specific chains
    if chain_id in [56, 97]:  # BSC Mainnet and Testnet
        w3.middleware_onion.inject(geth_poa_middleware, layer=0)
        logger.info("Injected PoA middleware for BSC chain")
    
    return w3 