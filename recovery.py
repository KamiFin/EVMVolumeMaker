import json
import time
import logging
import os
import random
from web3 import Web3
from eth_account import Account
import requests
from requests.exceptions import RequestException
import argparse
from web3.middleware import geth_poa_middleware
import sys
from utils.gas_manager import GasManager
from utils.web3_utils import get_web3_connection
from utils.transfer_utils import transfer_max_native

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("recovery.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Retry decorator with exponential backoff
def retry_with_backoff(max_retries=5, backoff_factor=1.5):
    """
    Decorator that retries the decorated function with exponential backoff
    
    Args:
        max_retries: Maximum number of retry attempts
        backoff_factor: Factor to increase wait time between retries
    
    Returns:
        Decorated function with retry logic
    """
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

class WalletRecovery:
    def __init__(self, chain_name, config_file='config.json', destination_address=None, recover_tokens=True):
        """
        Initialize the wallet recovery tool
        
        Args:
            chain_name: Name of the chain to recover from (e.g., 'sonic', 'ethereum')
            config_file: Path to the config file containing wallets and settings
            destination_address: Address to send all recovered funds to
            recover_tokens: Boolean flag to control whether to recover tokens or only native currency
        """
        self.chain_name = chain_name
        self.config_file = config_file
        self.load_config()
        
        # IMPORTANT CHANGE: Set destination address to the first wallet if not provided
        # This ensures our "star" wallet is always preserved
        self.destination_address = destination_address or self.wallets[0]['address']
        logger.info(f"Destination address set to: {self.destination_address}")
        
        # Initialize web3 with proper middleware
        self.w3 = get_web3_connection(self.chain_config['rpc_url'], self.chain_config['chain_id'])
        
        if not self.w3.is_connected():
            logger.error(f"Failed to connect to any RPC endpoint")
            raise ConnectionError(f"Could not connect to any RPC endpoint")
            
        logger.info(f"Connected to network: {self.w3.eth.chain_id}")
        logger.info(f"Using native token: {self.chain_config['native_token']}")
        
        # Token contract setup
        self.token_contract_address = next(iter(self.chain_config['token'].values()))['contract_address']
        self.token_symbol = next(iter(self.chain_config['token'].values()))['symbol']
        self.token_abi = [
            {"constant": True, "inputs": [], "name": "decimals", "outputs": [{"name": "", "type": "uint8"}], "payable": False, "stateMutability": "view", "type": "function"},
            {"inputs": [{"internalType": "address", "name": "spender", "type": "address"}, {"internalType": "uint256", "name": "amount", "type": "uint256"}], "name": "approve", "outputs": [{"internalType": "bool", "name": "", "type": "bool"}], "stateMutability": "nonpayable", "type": "function"},
            {"inputs": [{"internalType": "address", "name": "account", "type": "address"}], "name": "balanceOf", "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}], "stateMutability": "view", "type": "function"},
            {"inputs": [{"internalType": "address", "name": "recipient", "type": "address"}, {"internalType": "uint256", "name": "amount", "type": "uint256"}], "name": "transfer", "outputs": [{"internalType": "bool", "name": "", "type": "bool"}], "stateMutability": "nonpayable", "type": "function"}
        ]
        self.token_contract = self.w3.eth.contract(
            address=self.w3.to_checksum_address(self.token_contract_address), 
            abi=self.token_abi
        )
        
        # FIXED: Initialize token decimals
        try:
            self.token_decimals = self.token_contract.functions.decimals().call()
            logger.info(f"Token decimals: {self.token_decimals}")
        except Exception as e:
            logger.warning(f"Could not get token decimals: {e}. Using default of 18.")
            self.token_decimals = 18

        # Initialize gas manager
        self.gas_manager = GasManager(self.w3, self.chain_config['chain_id'])

        self.recover_tokens = recover_tokens  # Add new parameter

    def load_config(self):
        """Load configuration from the config file"""
        try:
            with open(self.config_file, 'r') as f:
                self.config = json.load(f)
                
            if self.chain_name not in self.config['chains']:
                raise ValueError(f"Chain '{self.chain_name}' not found in config file")
                
            self.chain_config = self.config['chains'][self.chain_name]
            self.wallets = self.config.get('wallets', [])
            
            logger.info(f"Loaded configuration for chain: {self.chain_name}")
            logger.info(f"Loaded {len(self.wallets)} wallets from config file")
            
            if not self.wallets:
                logger.warning("No wallets found in config file")
                
        except Exception as e:
            logger.error(f"Error loading config: {e}")
            raise

    def _get_web3_connection(self):
        """Get a Web3 connection, trying alternative RPCs if needed"""
        all_rpcs = [self.chain_config['rpc_url']] + self.chain_config.get('alternative_rpcs', [])
        
        # Try the current RPC first
        rpc_url = all_rpcs[self.current_rpc_index % len(all_rpcs)]
        w3 = Web3(Web3.HTTPProvider(rpc_url))
        
        # Apply PoA middleware for specific chains (BSC, Polygon, etc.)
        chain_id = self.chain_config.get('chain_id')
        if chain_id == 56:  # BSC
            from web3.middleware import geth_poa_middleware
            w3.middleware_onion.inject(geth_poa_middleware, layer=0)
            logger.info("Applied PoA middleware for BSC")
        elif chain_id == 137:  # Polygon
            from web3.middleware import geth_poa_middleware
            w3.middleware_onion.inject(geth_poa_middleware, layer=0)
            logger.info("Applied PoA middleware for Polygon")
        
        # If it works, return it
        if w3.is_connected():
            logger.info(f"Connected to RPC: {rpc_url}")
            return w3
            
        # Otherwise, try all other RPCs
        for i, rpc in enumerate(all_rpcs):
            if i == self.current_rpc_index % len(all_rpcs):
                continue  # Skip the one we just tried
                
            logger.info(f"Trying alternative RPC: {rpc}")
            w3 = Web3(Web3.HTTPProvider(rpc))
            
            # Apply PoA middleware again for alternative RPCs
            if chain_id == 56 or chain_id == 137:  # BSC or Polygon
                from web3.middleware import geth_poa_middleware
                w3.middleware_onion.inject(geth_poa_middleware, layer=0)
            
            if w3.is_connected():
                self.current_rpc_index = i
                logger.info(f"Connected to alternative RPC: {rpc}")
                return w3
                
        # If we get here, no RPC worked
        raise ConnectionError("Failed to connect to any RPC endpoint")

    def _switch_rpc(self):
        """Switch to the next RPC endpoint"""
        self.current_rpc_index += 1
        logger.info(f"Switching to next RPC endpoint")
        self.w3 = self._get_web3_connection()  # This will apply the middleware as needed
        return self.w3.is_connected()

    def check_token_balance(self, address):
        """
        Check token balance for a wallet with better error handling
        
        Args:
            address: Wallet address to check
        
        Returns:
            Tuple of (raw_balance, formatted_balance)
        """
        try:
            address = self.w3.to_checksum_address(address)
            
            # Verify the token contract is properly initialized
            if not hasattr(self, 'token_contract') or self.token_contract is None:
                logger.error("Token contract not initialized")
                return 0, 0
            
            # Try to get balance with detailed error handling
            try:
                balance = self.token_contract.functions.balanceOf(address).call()
                
                # Ensure token_decimals is initialized
                if not hasattr(self, 'token_decimals'):
                    logger.warning("Token decimals not initialized, using default of 18")
                    self.token_decimals = 18
                
                formatted_balance = balance / (10 ** self.token_decimals)
                logger.info(f"Token balance for {address}: {formatted_balance} {self.token_symbol}")
                return balance, formatted_balance
            
            except Exception as e:
                logger.error(f"Error calling balanceOf: {e}")
                # Try one more time with a delay
                time.sleep(1)
                try:
                    balance = self.token_contract.functions.balanceOf(address).call()
                    formatted_balance = balance / (10 ** self.token_decimals)
                    logger.info(f"Token balance (retry) for {address}: {formatted_balance} {self.token_symbol}")
                    return balance, formatted_balance
                except:
                    pass
            
            # If still failing, try switching RPC
            if self._switch_rpc():
                # Reinitialize the token contract with the new connection
                self.token_contract = self.w3.eth.contract(
                    address=self.w3.to_checksum_address(self.token_contract_address), 
                    abi=self.token_abi
                )
                return self.check_token_balance(address)
            return 0, 0
        except Exception as e:
            logger.error(f"Error checking token balance: {e}")
            # Try switching RPC
            if self._switch_rpc():
                return self.check_token_balance(address)
            return 0, 0

    def check_native_balance(self, address):
        """
        Check native token balance for a wallet
        
        Args:
            address: Wallet address to check
        
        Returns:
            Tuple of (raw_balance, formatted_balance)
        """
        try:
            balance = self.w3.eth.get_balance(address)
            balance_in_eth = self.w3.from_wei(balance, 'ether')
            logger.info(f"Native balance for {address}: {balance_in_eth} {self.chain_config['native_token']}")
            return balance, balance_in_eth
        except Exception as e:
            logger.error(f"Error checking native balance: {e}")
            # Try switching RPC
            if self._switch_rpc():
                return self.check_native_balance(address)
            return 0, 0

    def get_optimal_gas_price(self):
        """Get optimal gas price for the current chain with chain-specific adjustments"""
        try:
            # Get gas price from the network
            gas_price = self.w3.eth.gas_price
            
            # Chain-specific adjustments
            chain_id = self.chain_config['chain_id']
            
            if chain_id == 56:  # BSC
                # For BSC, cap the gas price at 5 Gwei
                max_gas = self.w3.to_wei(5, 'gwei')
                gas_price = min(gas_price, max_gas)
            elif chain_id == 137:  # Polygon
                # For Polygon, use at least 30 Gwei to avoid stuck transactions
                min_gas = self.w3.to_wei(30, 'gwei')
                gas_price = max(gas_price, min_gas) 
            elif chain_id == 8453:  # Base
                # For Base, use at least 0.1 Gwei
                min_gas = self.w3.to_wei(0.1, 'gwei')
                gas_price = max(gas_price, min_gas)
            elif chain_id == 146:  # Sonic
                # For Sonic - might need specific adjustments
                pass
            
            logger.info(f"Optimal gas price for chain {self.chain_name}: {self.w3.from_wei(gas_price, 'gwei')} Gwei")
            return gas_price
            
        except Exception as e:
            logger.error(f"Error getting optimal gas price: {e}")
            # Return chain-specific default values if network query fails
            if self.chain_config['chain_id'] == 56:  # BSC
                return self.w3.to_wei(3, 'gwei')
            elif self.chain_config['chain_id'] == 137:  # Polygon
                return self.w3.to_wei(50, 'gwei')
            else:
                return self.w3.to_wei(20, 'gwei')  # Safe default for most chains

    def get_fallback_gas_limit(self, operation_type):
        """Get fallback gas limit based on chain and operation type and token type"""
        chain_id = self.chain_config['chain_id']
        
        # Special handling for DAWAE token which requires higher gas
        if operation_type == 'token_transfer' and self.token_symbol == 'DAWAE':
            if chain_id == 56:  # BSC
                return 200000  # Much higher gas limit for DAWAE on BSC
            else:
                return 150000  # Higher gas limit for DAWAE on other chains
        
        gas_limits = {
            # BSC chain - INCREASED from 35000 to 100000 for token transfers
            56: {
                'token_transfer': 100000,  # Increased from previous 35000
                'native_transfer': 21000
            },
            # Other chains remain the same
            1: {'token_transfer': 65000, 'native_transfer': 21000},
            137: {'token_transfer': 65000, 'native_transfer': 21000},
            8453: {'token_transfer': 60000, 'native_transfer': 21000},
            146: {'token_transfer': 65000, 'native_transfer': 21000}
        }
        
        # Use chain-specific values if available, otherwise use defaults
        chain_limits = gas_limits.get(chain_id, {'token_transfer': 65000, 'native_transfer': 21000})
        return chain_limits.get(operation_type, 65000)

    def transfer_tokens(self, from_wallet, to_address):
        """
        Transfer all tokens from a wallet to the destination address with dynamic gas estimation
        """
        try:
            from_address = self.w3.to_checksum_address(from_wallet['address'])
            to_address = self.w3.to_checksum_address(to_address)
            
            # Check token balance
            token_balance, formatted_balance = self.check_token_balance(from_address)
            
            if token_balance <= 0:
                logger.info(f"No tokens to transfer from {from_address}")
                return False
                
            logger.info(f"Transferring {formatted_balance} {self.token_symbol} from {from_address} to {to_address}")
            
            # Check if sender has enough for gas
            native_balance, native_formatted = self.check_native_balance(from_address)
            
            # Set up a dummy transaction to estimate gas properly
            test_tx = self.token_contract.functions.transfer(
                to_address,
                token_balance
            ).build_transaction({
                'from': from_address,
                'nonce': self.w3.eth.get_transaction_count(from_address),
                'gas': 100000,  # This is just for estimation
                'chainId': self.chain_config['chain_id']
            })
            
            # Get actual gas price from the network - with chain-specific adjustments
            gas_price = self.get_optimal_gas_price()
            logger.info(f"Using gas price: {self.w3.from_wei(gas_price, 'gwei')} Gwei")
            
            # Try to accurately estimate required gas
            try:
                # Get precise gas estimate from the network
                estimated_gas = self.w3.eth.estimate_gas(test_tx)
                
                # Add a safety buffer (10% extra)
                gas_limit = int(estimated_gas * 1.1)
                logger.info(f"Chain estimated gas requirement: {estimated_gas}, using {gas_limit} with buffer")
            except Exception as e:
                logger.warning(f"Gas estimation failed: {e}")
                
                # Use chain-specific fallback values
                gas_limit = self.get_fallback_gas_limit('token_transfer')
                logger.info(f"Using fallback gas limit for this chain: {gas_limit}")
            
            # Calculate total gas cost
            gas_cost = gas_limit * gas_price
            
            # Check if wallet has enough native tokens for gas
            if native_balance < gas_cost:
                logger.warning(f"Insufficient funds for gas: have {self.w3.from_wei(native_balance, 'ether')} {self.chain_config['native_token']}, need {self.w3.from_wei(gas_cost, 'ether')}")
                return False
            
            # Use gas manager for transaction parameters
            tx_params = {
                'from': from_address,
                'nonce': self.w3.eth.get_transaction_count(from_address),
                'chainId': self.chain_config['chain_id']
            }
            
            tx_params = self.gas_manager.prepare_transaction_params(tx_params)
            gas_limit = self.gas_manager.estimate_gas_limit(tx_params)
            tx_params['gas'] = gas_limit
            
            # Build the actual transaction
            tx = self.token_contract.functions.transfer(
                to_address,
                token_balance
            ).build_transaction(tx_params)
            
            # Sign and send transaction
            signed_tx = self.w3.eth.account.sign_transaction(tx, from_wallet['private_key'])
            tx_hash = self.w3.eth.send_raw_transaction(signed_tx.rawTransaction)
            logger.info(f"Token transfer transaction sent: {tx_hash.hex()}")
            
            # Wait for receipt
            receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
            if receipt['status'] == 1:
                logger.info(f"Token transfer successful")
                return True
            else:
                logger.error(f"Token transfer failed with status: {receipt['status']}")
                return False
                
        except Exception as e:
            logger.error(f"Error transferring tokens: {e}")
            # Try switching RPC for certain errors
            if "429" in str(e) and self._switch_rpc():
                return self.transfer_tokens(from_wallet, to_address)
            return False

    def transfer_native(self, from_wallet, to_address):
        """Transfer all native tokens from a wallet with minimal buffer strategy."""
        try:
            # Use the shared implementation
            return transfer_max_native(self, from_wallet, to_address)
        except Exception as e:
            logger.error(f"Error in transfer_native: {e}")
            return False

    def transfer_dawae_tokens(self, from_wallet, to_address):
        """Special transfer method for DAWAE tokens which need higher gas"""
        try:
            from_address = self.w3.to_checksum_address(from_wallet['address'])
            to_address = self.w3.to_checksum_address(to_address)
            
            # Check token balance
            token_balance, formatted_balance = self.check_token_balance(from_address)
            
            if token_balance <= 0:
                logger.info(f"No DAWAE tokens to transfer from {from_address}")
                return False
            
            logger.info(f"Transferring {formatted_balance} DAWAE from {from_address} to {to_address} with high gas")
            
            # Check native balance
            native_balance, _ = self.check_native_balance(from_address)
            
            # Use higher gas limit specifically for DAWAE
            gas_limit = 200000  # Set high gas limit for DAWAE
            gas_price = self.get_optimal_gas_price()
            
            # Calculate gas cost
            gas_cost = gas_limit * gas_price
            
            # Check if wallet has enough native tokens for gas
            if native_balance < gas_cost:
                logger.warning(f"Insufficient funds for DAWAE transfer: have {self.w3.from_wei(native_balance, 'ether')} BNB, need {self.w3.from_wei(gas_cost, 'ether')} BNB")
                return False
            
            # Build transaction
            nonce = self.w3.eth.get_transaction_count(from_address)
            tx = self.token_contract.functions.transfer(
                to_address,
                token_balance
            ).build_transaction({
                'from': from_address,
                'gas': gas_limit,
                'gasPrice': gas_price,
                'nonce': nonce,
                'chainId': self.chain_config['chain_id']
            })
            
            # Sign and send
            signed_tx = self.w3.eth.account.sign_transaction(tx, from_wallet['private_key'])
            tx_hash = self.w3.eth.send_raw_transaction(signed_tx.rawTransaction)
            logger.info(f"DAWAE transfer transaction sent: {tx_hash.hex()}")
            
            # Wait for receipt
            receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=180)  # Longer timeout
            if receipt['status'] == 1:
                logger.info(f"DAWAE transfer successful!")
                return True
            else:
                logger.error(f"DAWAE transfer failed with status: {receipt['status']}")
                logger.info(f"Check transaction on explorer: https://bscscan.com/tx/{tx_hash.hex()}")
                return False
            
        except Exception as e:
            logger.error(f"Error transferring DAWAE tokens: {e}")
            if "429" in str(e) and self._switch_rpc():
                return self.transfer_dawae_tokens(from_wallet, to_address)
            return False

    def recover_all_funds(self):
        """
        Recover all funds from all wallets to the destination address using an optimized strategy
        Skip the first wallet as it's our main wallet
        """
        logger.info(f"Starting recovery of all funds on {self.chain_name} to {self.destination_address}")
        
        # IMPORTANT: Skip both the destination wallet AND the first wallet
        first_wallet_address = self.wallets[0]['address']
        wallets_to_process = []
        
        for w in self.wallets[1:]:  # Start from index 1 to skip first wallet
            if w['address'].lower() != self.destination_address.lower():
                wallets_to_process.append(w)
        
        logger.info(f"First wallet {first_wallet_address} excluded from recovery (preserved)")
        
        if not wallets_to_process:
            logger.warning("No wallets to process (only found first wallet or destination wallet)")
            return
        
        logger.info(f"Processing {len(wallets_to_process)} wallets")
        
        # Calculate required gas for token transfers
        token_gas_limit = self.get_fallback_gas_limit('token_transfer')
        gas_price = self.get_optimal_gas_price()
        token_gas_cost = token_gas_limit * gas_price
        
        # Check initial balance of destination wallet
        dest_native_balance, dest_native_balance_formatted = self.check_native_balance(self.destination_address)
        logger.info(f"Initial destination wallet balance: {dest_native_balance_formatted} {self.chain_config['native_token']}")
        
        # Group wallets into categories WITH CACHED BALANCES to avoid redundant RPC calls
        wallets_with_tokens_and_gas = []
        wallets_with_tokens_no_gas = []
        wallets_with_only_native = []
        wallets_with_significant_native = []
        
        # Store wallet balances to avoid rechecking
        wallet_balances = {}  # Structure: {address: {'native': (balance, formatted), 'token': (balance, formatted)}}
        
        # Determine minimum valuable native balance (3x regular transfer gas cost)
        min_valuable_native = 21000 * gas_price * 3
        min_threshold = self.w3.to_wei(0.00001, 'ether')
        
        for wallet in wallets_to_process:
            address = wallet['address']
            
            # Check and store token balance if we need to recover tokens
            if self.recover_tokens:
                token_balance, token_formatted = self.check_token_balance(address)
                wallet_balances[address] = {'token': (token_balance, token_formatted)}
            else:
                wallet_balances[address] = {'token': (0, 0)}
            
            # Check and store native balance
            native_balance, native_formatted = self.check_native_balance(address)
            wallet_balances[address]['native'] = (native_balance, native_formatted)
            
            # Categorize wallets based on balances
            if wallet_balances[address]['token'][0] > 0:
                if native_balance >= token_gas_cost:
                    wallets_with_tokens_and_gas.append(wallet)
                else:
                    wallets_with_tokens_no_gas.append(wallet)
                    
                    # If the wallet has tokens AND some native balance that's worth recovering first
                    if native_balance >= min_valuable_native:
                        wallets_with_significant_native.append(wallet)
                    
            elif native_balance > min_threshold:
                wallets_with_only_native.append(wallet)
                
                # If the wallet has a significant native balance, prioritize it
                if native_balance >= min_valuable_native:
                    wallets_with_significant_native.append(wallet)
        
        # Skip token-related operations if recover_tokens is False
        if not self.recover_tokens:
            logger.info("Token recovery disabled - recovering only native currency")
            # Process wallets with native tokens only
            for wallet in wallets_to_process:
                native_balance = wallet_balances[wallet['address']]['native'][0]
                if native_balance > min_threshold:
                    success = self.transfer_native(wallet, self.destination_address)
                    if success:
                        logger.info(f"Successfully transferred native tokens from {wallet['address']}")
                    else:
                        logger.warning(f"Failed to transfer native tokens from {wallet['address']}")
                    time.sleep(2)
            return
        
        # Process wallets with tokens and sufficient gas
        logger.info(f"=== PHASE 1: Processing {len(wallets_with_tokens_and_gas)} wallets with tokens and sufficient gas ===")
        for wallet in wallets_with_tokens_and_gas:
            logger.info(f"Processing wallet with tokens and gas: {wallet['address']}")
            success = self.transfer_tokens(wallet, self.destination_address)
            if success:
                logger.info(f"Successfully transferred tokens from {wallet['address']}")
            else:
                logger.warning(f"Failed to transfer tokens from {wallet['address']}")
            time.sleep(2)
        
        # First recover native tokens from wallets with significant balances
        if wallets_with_significant_native:
            logger.info(f"=== PHASE 2A: Recovering native tokens from {len(wallets_with_significant_native)} wallets with significant native balance ===")
            for wallet in wallets_with_significant_native:
                logger.info(f"Recovering significant native tokens from {wallet['address']}")
                success = self.transfer_native(wallet, self.destination_address)
                if success:
                    logger.info(f"Successfully transferred significant native tokens from {wallet['address']}")
                else:
                    logger.warning(f"Failed to transfer significant native tokens from {wallet['address']}")
                time.sleep(2)
        
        # Then recover from wallets with only native tokens
        if wallets_with_only_native:
            logger.info(f"=== PHASE 2B: Recovering native tokens from {len(wallets_with_only_native)} wallets with only native tokens ===")
            for wallet in wallets_with_only_native:
                # Skip if we already processed this wallet in the significant native phase
                if wallet in wallets_with_significant_native:
                    continue
                    
                logger.info(f"Recovering native tokens from {wallet['address']}")
                success = self.transfer_native(wallet, self.destination_address)
                if success:
                    logger.info(f"Successfully transferred native tokens from {wallet['address']}")
                else:
                    logger.warning(f"Failed to transfer native tokens from {wallet['address']}")
                time.sleep(2)
        
        # Check destination balance again to see if we can fund wallets with tokens but no gas
        destination_native_balance, dest_formatted = self.check_native_balance(self.destination_address)
        
        # Process wallets with tokens but insufficient gas - only if we have enough in destination
        if wallets_with_tokens_no_gas:
            logger.info(f"=== PHASE 3: Handling {len(wallets_with_tokens_no_gas)} wallets with tokens but insufficient gas ===")
            
            # Check if we have enough to fund at least some wallets
            single_funding_cost = 21000 * gas_price + token_gas_cost  # Cost to fund + recover tokens
            total_funding_needed = single_funding_cost * len(wallets_with_tokens_no_gas)
            
            if destination_native_balance >= single_funding_cost:
                logger.info(f"Destination has {dest_formatted} {self.chain_config['native_token']}, enough for some funding operations")
                
                # Get the destination wallet private key
                destination_wallet = next((w for w in self.wallets if w['address'].lower() == self.destination_address.lower()), None)
                if not destination_wallet:
                    logger.error(f"Cannot find destination wallet {self.destination_address} in wallet list")
                    # Can't fund wallets if we don't have the destination private key
                else:
                    # Sort wallets by token balance (highest first) to prioritize
                    wallets_with_tokens_no_gas.sort(key=lambda w: self.check_token_balance(w['address'])[0], reverse=True)
                    
                    # Fund as many wallets as we can afford
                    for wallet in wallets_with_tokens_no_gas:
                        # Re-check destination balance for each iteration
                        current_dest_balance, _ = self.check_native_balance(self.destination_address)
                        
                        # Calculate affordable funding amount based on current balance
                        max_available = current_dest_balance - (21000 * gas_price * 1.1)  # Keep some for the tx fee
                        
                        if max_available <= 0:
                            logger.warning(f"Insufficient balance in destination to fund more wallets")
                            break
                        
                        # IMPORTANT: Increase funding for DAWAE token specifically
                        if self.token_symbol == 'DAWAE':
                            # DAWAE needs much more gas, fund with at least 200K gas worth
                            min_required_funding = 200000 * gas_price * 1.2  # 20% safety buffer
                        else:
                            min_required_funding = token_gas_cost * 1.2  # 20% safety buffer
                            
                        funding_amount = min(min_required_funding, max_available)

                        if funding_amount <= 0:
                            logger.warning(f"Cannot fund wallet {wallet['address']}: insufficient funds")
                            continue

                        logger.info(f"Funding wallet {wallet['address']} with {self.w3.from_wei(funding_amount, 'ether')} {self.chain_config['native_token']} for gas")

                        # Build a transaction to send some native tokens for gas
                        nonce = self.w3.eth.get_transaction_count(self.destination_address)
                        tx = {
                            "from": self.destination_address,
                            "to": wallet['address'],
                            "value": int(funding_amount),
                            "gas": 21000,
                            "gasPrice": gas_price,
                            "nonce": nonce,
                            "chainId": self.chain_config['chain_id']
                        }

                        try:
                            # Sign and send
                            signed_tx = self.w3.eth.account.sign_transaction(tx, destination_wallet['private_key'])
                            tx_hash = self.w3.eth.send_raw_transaction(signed_tx.rawTransaction)
                            logger.info(f"Gas funding transaction sent: {tx_hash.hex()}")
                            
                            # Wait for confirmation
                            receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
                            time.sleep(2)
                            
                            # Verify the funded wallet has enough for the gas
                            funded_native_balance, _ = self.check_native_balance(wallet['address'])
                            if funded_native_balance < token_gas_cost:
                                logger.warning(f"Funded wallet still has insufficient gas: {self.w3.from_wei(funded_native_balance, 'ether')} < {self.w3.from_wei(token_gas_cost, 'ether')} {self.chain_config['native_token']}")
                                
                                # Send additional funding if needed and if possible
                                additional_needed = token_gas_cost - funded_native_balance
                                if max_available >= additional_needed:
                                    logger.info(f"Sending additional {self.w3.from_wei(additional_needed, 'ether')} {self.chain_config['native_token']} to ensure sufficient gas")
                                    # [Code to send additional funds...]
                                else:
                                    logger.warning("Cannot send additional funds. Will still attempt token transfer.")
                            
                            # Now try to transfer the tokens
                            logger.info(f"Now transferring tokens from funded wallet {wallet['address']}")
                            if self.token_symbol == 'DAWAE':
                                # Use special higher-gas method for DAWAE
                                success = self.transfer_dawae_tokens(wallet, self.destination_address)
                            else:
                                # Regular transfer for other tokens
                                success = self.transfer_tokens(wallet, self.destination_address)
                        except Exception as e:
                            logger.error(f"Error funding wallet: {e}")
                            # Continue with next wallet
            else:
                logger.warning(f"Insufficient funds in destination wallet to fund other wallets: have {dest_formatted}, need at least {self.w3.from_wei(single_funding_cost, 'ether')} {self.chain_config['native_token']}")
                logger.info("Skipping funding phase - will attempt direct token transfers with minimal gas")
                
                # Try direct transfers with minimal gas (might work for some tokens with low gas requirements)
                for wallet in wallets_with_tokens_no_gas:
                    logger.info(f"Attempting direct token transfer from unfunded wallet {wallet['address']}")
                    success = self.transfer_tokens(wallet, self.destination_address)
                    if success:
                        logger.info(f"Successfully transferred tokens from unfunded wallet {wallet['address']}")
                    else:
                        logger.warning(f"Failed to transfer tokens from unfunded wallet {wallet['address']}")
                    time.sleep(2)
        
        # Final sweep of native tokens from all wallets
        logger.info("=== FINAL PHASE: Sweeping remaining native tokens from all wallets ===")
        for wallet in wallets_to_process:
            # Skip if we already processed this wallet in a previous native token phase
            if wallet in wallets_with_significant_native:
                continue
            
            logger.info(f"Sweeping native tokens from {wallet['address']}")
            native_balance = wallet_balances[wallet['address']]['native'][0]
            
            if native_balance > min_threshold:
                success = self.transfer_native(wallet, self.destination_address)
                if success:
                    logger.info(f"Successfully swept native tokens from {wallet['address']}")
                else:
                    logger.warning(f"Failed to sweep native tokens from {wallet['address']}")
            else:
                logger.info(f"No native tokens to sweep from {wallet['address']}")
            
            time.sleep(2)
        
        logger.info("Recovery process completed")
        
        # Final balance check
        _, token_balance = self.check_token_balance(self.destination_address)
        _, native_balance = self.check_native_balance(self.destination_address)
        
        logger.info(f"Final destination address balances on {self.chain_name}:")
        logger.info(f"  - {token_balance} {self.token_symbol}")
        logger.info(f"  - {native_balance} {self.chain_config['native_token']}")


def configure_web3_for_chain(w3, chain_id):
    """Apply appropriate middleware based on chain ID"""
    if chain_id in [56, 137, 100, 77]:  # BSC, Polygon, xDai, Sokol
        from web3.middleware import geth_poa_middleware
        w3.middleware_onion.inject(geth_poa_middleware, layer=0)
        logger.info(f"Applied PoA middleware for chain ID {chain_id}")
    return w3


if __name__ == "__main__":
    try:
        # Set up argument parser
        parser = argparse.ArgumentParser(description='Recover funds from wallets')
        parser.add_argument('chain', help='Chain name from config (e.g., sonic, ethereum)')
        parser.add_argument('--destination', '-d', help='Destination address for recovered funds')
        parser.add_argument('--include-first', action='store_true', help='Include first wallet in recovery (not recommended)')
        # Change argument to enable token recovery instead of native-only
        parser.add_argument('--with-tokens', '-t', action='store_true', 
                          help='Recover both native and token balances (default: native only)')
        args = parser.parse_args()

        # By default, we'll send to the first wallet if destination not specified
        destination_address = args.destination or None
        
        recovery = WalletRecovery(
            chain_name=args.chain,
            destination_address=destination_address,
            recover_tokens=args.with_tokens  # Now directly use with_tokens flag
        )
        
        # Display warning if first wallet will be processed
        if args.include_first:
            first_address = recovery.wallets[0]['address']
            logger.warning(f"WARNING: First wallet {first_address} will be included in recovery!")
            confirm = input("Are you sure you want to include the first wallet? This is NOT recommended. (y/N): ")
            if confirm.lower() != 'y':
                logger.info("Recovery cancelled to protect first wallet.")
                sys.exit(0)
        
        recovery.recover_all_funds()
    except Exception as e:
        logger.critical(f"Fatal error: {e}")
