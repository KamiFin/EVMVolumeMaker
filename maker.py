import json
import time
import logging
import os
import random
from web3 import Web3
from eth_account import Account
import sniper
import requests
from requests.exceptions import RequestException
import argparse
from utils.gas_manager import GasManager
from web3.middleware import geth_poa_middleware
from utils.web3_utils import get_web3_connection
from utils.transfer_utils import transfer_max_native
import signal
import sys
from enum import Enum
from evm_volume_maker import EVMVolumeMaker
from solana_volume_maker import SolanaVolumeMaker
from solana_config import (
    CHAIN_NAME as SOLANA_CHAIN_NAME,
    RPC_URL as SOLANA_RPC_URL,
    ALTERNATIVE_RPCS as SOLANA_ALTERNATIVE_RPCS,
    DEX_TYPE as SOLANA_DEX_TYPE,
    POOL_ADDRESS as SOLANA_POOL_ADDRESS,
    UNIT_BUDGET as SOLANA_UNIT_BUDGET,
    UNIT_PRICE as SOLANA_UNIT_PRICE,
    MIN_BUY_AMOUNT as SOLANA_MIN_BUY_AMOUNT,
    MAX_BUY_AMOUNT as SOLANA_MAX_BUY_AMOUNT,
    TRANSFER_PERCENTAGE as SOLANA_TRANSFER_PERCENTAGE,
    WAIT_TIME as SOLANA_WAIT_TIME,
    TRADE_WAIT_TIME as SOLANA_TRADE_WAIT_TIME,
    MAX_RETRIES as SOLANA_MAX_RETRIES,
    BACKOFF_FACTOR as SOLANA_BACKOFF_FACTOR,
    MIN_BALANCE_THRESHOLD as SOLANA_MIN_BALANCE_THRESHOLD
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("volume_maker.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Retry decorator with exponential backoff
def retry_with_backoff(max_retries=5, backoff_factor=1.5):
    def decorator(func):
        def wrapper(*args, **kwargs):
            retries = 0
            while retries < max_retries:
                try:
                    return func(*args, **kwargs)
                except (RequestException, ValueError) as e:
                    wait_time = backoff_factor * (2 ** retries) + random.uniform(0, 1)
                    retries += 1
                    if retries < max_retries:
                        logger.warning(f"Request failed with {e}. Retrying in {wait_time:.2f} seconds... (Attempt {retries}/{max_retries})")
                        time.sleep(wait_time)
                    else:
                        logger.error(f"Max retries reached. Last error: {e}")
                        raise
            return func(*args, **kwargs)
        return wrapper
    return decorator

# Configuration
class Config:
    def __init__(self, chain_name):
        """Initialize configuration for specified chain"""
        # Special handling for Solana chain
        if chain_name.lower() == "solana":
            # Chain configuration
            self.RPC_URL = SOLANA_RPC_URL
            self.ALTERNATIVE_RPCS = SOLANA_ALTERNATIVE_RPCS
            self.CHAIN_NAME = SOLANA_CHAIN_NAME
            self.DEX_TYPE = SOLANA_DEX_TYPE
            
            # DEX configuration
            self.POOL_ADDRESS = SOLANA_POOL_ADDRESS
            self.UNIT_BUDGET = SOLANA_UNIT_BUDGET
            self.UNIT_PRICE = SOLANA_UNIT_PRICE
            
            # Transaction configuration
            self.MIN_BUY_AMOUNT = SOLANA_MIN_BUY_AMOUNT
            self.MAX_BUY_AMOUNT = SOLANA_MAX_BUY_AMOUNT
            self.TRANSFER_PERCENTAGE = SOLANA_TRANSFER_PERCENTAGE
            self.WAIT_TIME = SOLANA_WAIT_TIME
            self.TRADE_WAIT_TIME = SOLANA_TRADE_WAIT_TIME
            self.MAX_RETRIES = SOLANA_MAX_RETRIES
            self.BACKOFF_FACTOR = SOLANA_BACKOFF_FACTOR
            self.MIN_BALANCE_THRESHOLD = SOLANA_MIN_BALANCE_THRESHOLD
            
            # File paths
            self.CONFIG_FILE = 'config.json'
            return
            
        # For other chains, load from config.json
        with open('config.json', 'r') as f:
            config = json.load(f)
            
        if chain_name not in config['chains']:
            raise ValueError(f"Chain '{chain_name}' not found in config.json")
            
        chain_config = config['chains'][chain_name]
        
        # Chain configuration
        self.RPC_URL = chain_config['rpc_url']
        self.ALTERNATIVE_RPCS = chain_config.get('alternative_rpcs', [])
        self.CHAIN_ID = chain_config['chain_id']
        self.NATIVE_TOKEN = chain_config['native_token']
        
        # DEX configuration
        self.ROUTER_ADDRESS = chain_config['dex']['router_address']
        self.ROUTER_ABI = chain_config['dex']['router_abi']
        self.WRAPPED_NATIVE_TOKEN = chain_config['dex']['wrapped_native_token']
        
        # Token configuration
        token_data = next(iter(chain_config['token'].values()))
        self.TOKEN_CONTRACT = token_data['contract_address']
        self.TOKEN_SYMBOL = token_data.get('symbol', '')
        
        # Transaction configuration
        self.MIN_BUY_AMOUNT = chain_config['transaction'].get('min_buy_amount')
        self.MAX_BUY_AMOUNT = chain_config['transaction'].get('max_buy_amount')
        self.TRANSFER_PERCENTAGE = chain_config['transaction']['transfer_percentage']
        self.GAS_MULTIPLIER = chain_config['transaction']['gas_multiplier']
        self.WAIT_TIME = chain_config['transaction']['wait_time']
        self.TRADE_WAIT_TIME = chain_config['transaction'].get('trade_wait_time', 1)  # Default to 1 second
        self.MAX_RETRIES = chain_config['transaction'].get('max_retries', 3)
        self.BACKOFF_FACTOR = chain_config['transaction'].get('backoff_factor', 2)
        self.MIN_BALANCE_THRESHOLD = chain_config['transaction'].get('min_balance_threshold', 0.00001)
        
        # Slippage settings
        self.BUY_SLIPPAGE = chain_config['transaction'].get('buy_slippage', 0.005)  # Default 0.5%
        self.SELL_SLIPPAGE = chain_config['transaction'].get('sell_slippage', 0.005)  # Default 0.5%
        
        # File paths
        self.CONFIG_FILE = 'config.json'

class CycleResult(Enum):
    CONTINUE = True   # Continue to next cycle
    STOP = False     # Stop and mark wallet as failed


if __name__ == "__main__":
    try:
        # Set up argument parser
        parser = argparse.ArgumentParser(description='Volume maker for DEX trading')
        parser.add_argument('chain', help='Chain name from config (e.g., sonic, ethereum, solana)')
        parser.add_argument('--mode', choices=['buy', 'sell', 'trade'], default='buy', 
                          help='Operation mode: buy tokens, sell tokens, or buy then sell (default: buy)')
        parser.add_argument('--single-wallet', '-s', action='store_true',
                          help='Use only the first wallet without creating new ones')
        args = parser.parse_args()

        # Select the appropriate volume maker based on chain
        if args.chain.lower() == "solana":
            maker = SolanaVolumeMaker(args.chain, args.mode, args.single_wallet)
        else:
            maker = EVMVolumeMaker(args.chain, args.mode, args.single_wallet)

        # Run the volume maker
        maker.run()

    except Exception as e:
        logger.critical(f"Fatal error: {e}")
        sys.exit(1)

