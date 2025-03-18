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
    def __init__(self, chain_name, config_file='config.json', destination_address=None):
        """
        Initialize the wallet recovery tool
        
        Args:
            chain_name: Name of the chain to recover from (e.g., 'sonic', 'ethereum')
            config_file: Path to the config file containing wallets and settings
            destination_address: Address to send all recovered funds to
        """
        self.chain_name = chain_name
        self.config_file = config_file
        self.load_config()
        
        # Set destination address (either provided or first wallet)
        self.destination_address = destination_address or self.wallets[0]['address']
        logger.info(f"Destination address set to: {self.destination_address}")
        
        # Initialize Web3 connection
        self.current_rpc_index = 0
        self.w3 = self._get_web3_connection()
        
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
        
        # Get token decimals
        try:
            self.token_decimals = self.token_contract.functions.decimals().call()
            logger.info(f"Token decimals: {self.token_decimals}")
        except Exception as e:
            logger.warning(f"Could not get token decimals: {e}. Using default of 18.")
            self.token_decimals = 18

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
        self.w3 = self._get_web3_connection()
        return self.w3.is_connected()

    def check_token_balance(self, address):
        """
        Check token balance for a wallet
        
        Args:
            address: Wallet address to check
        
        Returns:
            Tuple of (raw_balance, formatted_balance)
        """
        try:
            address = self.w3.to_checksum_address(address)
            balance = self.token_contract.functions.balanceOf(address).call()
            formatted_balance = balance / (10 ** self.token_decimals)
            logger.info(f"Token balance for {address}: {formatted_balance} {self.token_symbol}")
            return balance, formatted_balance
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

    def transfer_tokens(self, from_wallet, to_address):
        """
        Transfer all tokens from a wallet to the destination address
        
        Args:
            from_wallet: Wallet dict with address and private_key
            to_address: Destination address
        
        Returns:
            Boolean indicating success
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
            
            # Get nonce
            nonce = self.w3.eth.get_transaction_count(from_address)
            
            # Build transaction
            tx = self.token_contract.functions.transfer(
                to_address,
                token_balance
            ).build_transaction({
                'from': from_address,
                'gas': 100000,  # Standard ERC20 transfer gas
                'gasPrice': self.w3.eth.gas_price,
                'nonce': nonce,
                'chainId': self.chain_config['chain_id']
            })
            
            # Try to estimate gas to ensure transaction will succeed
            try:
                gas_estimate = self.w3.eth.estimate_gas(tx)
                tx['gas'] = gas_estimate
                logger.info(f"Estimated gas for token transfer: {gas_estimate}")
            except Exception as e:
                logger.warning(f"Gas estimation failed: {e}. Using default gas limit.")
            
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
        """
        Transfer all native tokens from a wallet to the destination address
        
        Args:
            from_wallet: Wallet dict with address and private_key
            to_address: Destination address
        
        Returns:
            Boolean indicating success
        """
        try:
            from_address = self.w3.to_checksum_address(from_wallet['address'])
            to_address = self.w3.to_checksum_address(to_address)
            
            # Check native balance
            balance, balance_in_eth = self.check_native_balance(from_address)
            
            if balance <= self.w3.to_wei(0.00001, 'ether'):
                logger.info(f"Insufficient native balance to transfer from {from_address}")
                return False
                
            # Calculate gas estimate for a simple transfer
            try:
                gas_estimate = self.w3.eth.estimate_gas({
                    "from": from_address,
                    "to": to_address,
                    "value": balance // 2  # Use half the balance for estimation to ensure it works
                })
                logger.info(f"Gas estimate for native transfer: {gas_estimate}")
            except Exception as e:
                logger.error(f"Error estimating gas: {e}")
                gas_estimate = 21000  # Default gas limit for simple transfers
            
            # Get current gas price
            try:
                gas_price = self.w3.eth.gas_price
                logger.info(f"Gas price for native transfer: {self.w3.from_wei(gas_price, 'gwei')} Gwei")
            except Exception as e:
                logger.error(f"Error getting gas price: {e}")
                gas_price = self.w3.to_wei(50, 'gwei')  # Default fallback
            
            # Calculate gas cost
            gas_cost = gas_estimate * gas_price
            logger.info(f"Estimated gas cost: {self.w3.from_wei(gas_cost, 'ether')} {self.chain_config['native_token']}")
            
            # Calculate amount to send after deducting gas fee
            amount = balance - gas_cost
            
            if amount <= 0:
                logger.warning(f"Amount after gas deduction is zero or negative for wallet {from_address}")
                return False
                
            # Apply a small safety margin (99.5% of available amount)
            transfer_amount = int(amount * 0.995)
            logger.info(f"Transfer amount after applying safety margin: {self.w3.from_wei(transfer_amount, 'ether')} {self.chain_config['native_token']}")
            
            # Get nonce
            nonce = self.w3.eth.get_transaction_count(from_address)
            
            # Build transaction
            transaction = {
                "from": from_address,
                "to": to_address,
                "value": transfer_amount,
                "gas": gas_estimate,
                "gasPrice": gas_price,
                "nonce": nonce,
                "chainId": self.chain_config['chain_id']
            }
            
            # Sign and send transaction
            signed_txn = self.w3.eth.account.sign_transaction(transaction, from_wallet['private_key'])
            tx_hash = self.w3.eth.send_raw_transaction(signed_txn.rawTransaction)
            logger.info(f"Native transfer transaction sent: {tx_hash.hex()}")
            
            # Wait for receipt
            receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
            if receipt['status'] == 1:
                logger.info(f"Native transfer successful: {self.w3.from_wei(transfer_amount, 'ether')} {self.chain_config['native_token']}")
                return True
            else:
                logger.error(f"Native transfer failed with status: {receipt['status']}")
                return False
                
        except Exception as e:
            logger.error(f"Error transferring native tokens: {e}")
            # Try switching RPC for certain errors
            if "429" in str(e) and self._switch_rpc():
                return self.transfer_native(from_wallet, to_address)
            return False

    def recover_all_funds(self):
        """
        Recover all funds from all wallets to the destination address
        
        Strategy:
        1. First recover all tokens from all wallets
        2. Then recover all native tokens from all wallets
        """
        logger.info(f"Starting recovery of all funds on {self.chain_name} to {self.destination_address}")
        
        # Skip the destination wallet if it's in our list
        wallets_to_process = [w for w in self.wallets if w['address'].lower() != self.destination_address.lower()]
        
        if not wallets_to_process:
            logger.warning("No wallets to process (destination wallet is the only wallet)")
            return
            
        logger.info(f"Processing {len(wallets_to_process)} wallets")
        
        # First recover all tokens
        logger.info("=== PHASE 1: Recovering tokens ===")
        for i, wallet in enumerate(wallets_to_process):
            logger.info(f"Processing wallet {i+1}/{len(wallets_to_process)}: {wallet['address']}")
            token_balance, _ = self.check_token_balance(wallet['address'])
            
            if token_balance > 0:
                success = self.transfer_tokens(wallet, self.destination_address)
                if success:
                    logger.info(f"Successfully transferred tokens from {wallet['address']}")
                else:
                    logger.warning(f"Failed to transfer tokens from {wallet['address']}")
            else:
                logger.info(f"No tokens to transfer from {wallet['address']}")
                
            # Small delay between operations
            time.sleep(2)
        
        # Then recover all native tokens
        logger.info("=== PHASE 2: Recovering native tokens ===")
        for i, wallet in enumerate(wallets_to_process):
            logger.info(f"Processing wallet {i+1}/{len(wallets_to_process)}: {wallet['address']}")
            native_balance, _ = self.check_native_balance(wallet['address'])
            
            if native_balance > self.w3.to_wei(0.00001, 'ether'):
                success = self.transfer_native(wallet, self.destination_address)
                if success:
                    logger.info(f"Successfully transferred native tokens from {wallet['address']}")
                else:
                    logger.warning(f"Failed to transfer native tokens from {wallet['address']}")
            else:
                logger.info(f"No native tokens to transfer from {wallet['address']}")
                
            # Small delay between operations
            time.sleep(2)
            
        logger.info("Recovery process completed")
        
        # Final balance check of destination address
        _, token_balance = self.check_token_balance(self.destination_address)
        _, native_balance = self.check_native_balance(self.destination_address)
        
        logger.info(f"Final destination address balances on {self.chain_name}:")
        logger.info(f"  - {token_balance} {self.token_symbol}")
        logger.info(f"  - {native_balance} {self.chain_config['native_token']}")


if __name__ == "__main__":
    try:
        # Set up argument parser
        parser = argparse.ArgumentParser(description='Recover funds from wallets')
        parser.add_argument('chain', help='Chain name from config (e.g., sonic, ethereum)')
        parser.add_argument('--destination', '-d', help='Destination address for recovered funds')
        args = parser.parse_args()

        # You can specify a custom destination address here, or leave it as None to use the first wallet
        destination_address = args.destination or None
        
        recovery = WalletRecovery(
            chain_name=args.chain,
            destination_address=destination_address
        )
        recovery.recover_all_funds()
    except Exception as e:
        logger.critical(f"Fatal error: {e}")
