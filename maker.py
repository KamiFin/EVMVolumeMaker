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
        self.TOKEN_CONTRACT = next(iter(chain_config['token'].values()))['contract_address']
        
        # Transaction configuration
        self.BUY_AMOUNT = chain_config['transaction']['buy_amount']
        self.TRANSFER_PERCENTAGE = chain_config['transaction']['transfer_percentage']
        self.GAS_MULTIPLIER = chain_config['transaction']['gas_multiplier']
        self.WAIT_TIME = chain_config['transaction']['wait_time']
        self.MAX_RETRIES = chain_config['transaction'].get('max_retries', 3)
        self.BACKOFF_FACTOR = chain_config['transaction'].get('backoff_factor', 2)
        
        # File paths
        self.CONFIG_FILE = 'config.json'

class VolumeMaker:
    def __init__(self, chain_name):
        """Initialize the volume maker with web3 connection and wallet management."""
        self.config = Config(chain_name)
        self.chain_name = chain_name
        self.current_rpc_index = 0
        self.w3 = self._get_web3_connection()
        
        if not self.w3.is_connected():
            logger.error(f"Failed to connect to any RPC endpoint")
            raise ConnectionError(f"Could not connect to any RPC endpoint")
            
        logger.info(f"Connected to network: {self.w3.eth.chain_id}")
        logger.info(f"Using native token: {self.config.NATIVE_TOKEN}")
        
        self.wallets = []
        self.index = 0
        
        # Load existing wallets if available
        self._load_wallets()
        
        # Create initial wallet if none exists
        if not self.wallets:
            self._initialize()
        
        self.gas_manager = GasManager(self.w3, self.config.CHAIN_ID)

    def _get_web3_connection(self):
        """Get a Web3 connection, trying alternative RPCs if needed."""
        all_rpcs = [self.config.RPC_URL] + self.config.ALTERNATIVE_RPCS
        
        # Try the current RPC first
        rpc_url = all_rpcs[self.current_rpc_index % len(all_rpcs)]
        w3 = get_web3_connection(rpc_url, self.config.CHAIN_ID)
        
        # If it works, return it
        if w3.is_connected():
            logger.info(f"Connected to RPC: {rpc_url}")
            return w3
            
        # Otherwise, try all other RPCs
        for i, rpc in enumerate(all_rpcs):
            if i == self.current_rpc_index % len(all_rpcs):
                continue  # Skip the one we just tried
                
            logger.info(f"Trying alternative RPC: {rpc}")
            w3 = get_web3_connection(rpc, self.config.CHAIN_ID)
            if w3.is_connected():
                self.current_rpc_index = i
                logger.info(f"Connected to alternative RPC: {rpc}")
                return w3
                
        # If we get here, no RPC worked
        raise ConnectionError("Failed to connect to any RPC endpoint")

    def _switch_rpc(self):
        """Switch to the next RPC endpoint."""
        self.current_rpc_index += 1
        logger.info(f"Switching to next RPC endpoint")
        self.w3 = self._get_web3_connection()
        
        # Also update the RPC in sniper.py
        all_rpcs = [self.config.RPC_URL] + self.config.ALTERNATIVE_RPCS
        rpc_url = all_rpcs[self.current_rpc_index % len(all_rpcs)]
        sniper.web3 = Web3(Web3.HTTPProvider(rpc_url))
        sniper.rpc = rpc_url
        
        return self.w3.is_connected()

    def _load_wallets(self):
        """Load wallets from config file if it exists."""
        if os.path.exists(self.config.CONFIG_FILE):
            try:
                with open(self.config.CONFIG_FILE, 'r') as f:
                    data = json.load(f)
                    self.wallets = data.get("wallets", [])
                logger.info(f"Loaded {len(self.wallets)} wallets from config file")
            except Exception as e:
                logger.error(f"Error loading wallets from config: {e}")
        else:
            logger.info("No config file found, will create new wallets")

    def _save_wallets(self):
        """Save wallets to config file."""
        try:
            # Load existing config to preserve other settings
            with open(self.config.CONFIG_FILE, 'r') as f:
                config_data = json.load(f)
            
            # Update wallets
            config_data["wallets"] = self.wallets
            
            # Write back to file
            with open(self.config.CONFIG_FILE, 'w') as f:
                json.dump(config_data, f, indent=4)
                
            logger.info(f"Saved {len(self.wallets)} wallets to config file")
        except Exception as e:
            logger.error(f"Error saving wallets to config: {e}")

    def _initialize(self):
        """Create the first wallet to start the process."""
        self._generate_wallet()
        logger.info(f"Initialized with wallet: {self.wallets[0]['address']}")

    def _generate_wallet(self):
        """Generate a new wallet and add it to the list."""
        acct = Account.create()
        wallet = {
            "address": acct.address,
            "private_key": acct._private_key.hex()
        }
        self.wallets.append(wallet)
        self._save_wallets()
        logger.info(f"Generated new wallet: {wallet['address']}")
        return wallet

    def _increment_index(self):
        """Increment the wallet index."""
        self.index += 1
        if self.index >= len(self.wallets):
            self.index = 0
        logger.info(f"Moved to wallet index {self.index}")

    def _get_current_gas_price(self):
        """Get the current gas price with a multiplier."""
        try:
            gas_price = self.w3.eth.gas_price
            gas_price_gwei = gas_price / 10**9
            adjusted_gas_price = gas_price_gwei * self.config.GAS_MULTIPLIER
            logger.info(f"Current gas price: {adjusted_gas_price:.2f} Gwei")
            return adjusted_gas_price
        except Exception as e:
            logger.error(f"Error getting gas price: {e}")
            # Try switching RPC
            if self._switch_rpc():
                return self._get_current_gas_price()
            # Fallback to a reasonable gas price
            return 50

    def _check_wallet_balance(self, address):
        """Check the balance of a wallet."""
        try:
            balance = self.w3.eth.get_balance(address)
            balance_in_eth = self.w3.from_wei(balance, 'ether')
            logger.info(f"Wallet {address} balance: {balance_in_eth} {self.config.NATIVE_TOKEN}")
            return balance, balance_in_eth
        except Exception as e:
            logger.error(f"Error checking wallet balance: {e}")
            # Try switching RPC
            if self._switch_rpc():
                return self._check_wallet_balance(address)
            return 0, 0

    def buy_tokens(self):
        """Buy tokens using the current wallet."""
        try:
            current_wallet = self.wallets[self.index]
            gas_price = self._get_current_gas_price()
            
            logger.info(f"Buying tokens with wallet {current_wallet['address']}")
            
            # Initialize sniper with the current chain configuration
            sniper.init_globals(self.chain_name)
            
            # Use a higher amount for the transaction to ensure it goes through
            buy_amount = self.config.BUY_AMOUNT  # Use a higher amount that will be visible on-chain
            
            # Use gas manager for transaction parameters
            tx_params = {
                'from': current_wallet['address'],
                'value': self.w3.to_wei(buy_amount, 'ether'),
                'nonce': self.w3.eth.get_transaction_count(current_wallet['address']),
                'chainId': self.config.CHAIN_ID
            }
            
            tx_params = self.gas_manager.prepare_transaction_params(tx_params)
            
            # Execute the buy transaction
            success = sniper.ExactETHSwap(
                buy_amount,
                self.config.TOKEN_CONTRACT,
                current_wallet["address"],
                current_wallet["private_key"],
                gas_price
            )
            
            if success:
                logger.info(f"Successfully bought tokens with wallet {current_wallet['address']}")
                return True
            else:
                logger.warning(f"Failed to buy tokens with wallet {current_wallet['address']}")
                return False
        except Exception as e:
            logger.error(f"Error buying tokens: {str(e)}")
            return False

    def transfer_funds(self, from_index, to_index):
        """Transfer maximum funds from one wallet to another with minimal buffer strategy."""
        try:
            if from_index >= len(self.wallets) or to_index >= len(self.wallets):
                logger.error(f"Invalid wallet indices: {from_index}, {to_index}")
                return False
            
            from_wallet = self.wallets[from_index]
            to_wallet = self.wallets[to_index]
            
            logger.info(f"Transferring funds from {from_wallet['address']} to {to_wallet['address']}")
            
            # Use the shared implementation
            return transfer_max_native(self, from_wallet, to_wallet['address'])
            
        except Exception as e:
            logger.error(f"Error in transfer_funds: {e}")
            return False

    def start_cycle(self):
        """Start a volume making cycle."""
        try:
            # Check if current wallet has funds
            current_wallet = self.wallets[self.index]
            balance, balance_in_eth = self._check_wallet_balance(current_wallet['address'])
            
            if balance <= self.w3.to_wei(0.0001, 'ether'):
                logger.warning(f"Current wallet {current_wallet['address']} has insufficient funds: {balance_in_eth} {self.config.NATIVE_TOKEN}")
                
                # If this is not the first wallet, try to go back to the first wallet
                if self.index > 0:
                    logger.info("Attempting to return to the first wallet which should have funds")
                    self.index = 0
                    return False
            
            # Initialize sniper with current chain configuration
            logger.info("Initializing sniper module...")
            sniper.init_globals(self.chain_name)
            
            # Check if the token pair exists on the DEX
            logger.info("Checking if token pair exists...")
            if not sniper.check_pair_exists(self.config.TOKEN_CONTRACT):
                logger.error(f"Token pair does not exist on the DEX. Please create liquidity first.")
                return False
            
            # 1. Buy tokens with current wallet
            buy_success = self.buy_tokens()
            if not buy_success:
                logger.warning("Buy operation failed, continuing with next steps")
            
            # 2. Generate a new wallet
            new_wallet = self._generate_wallet()
            logger.info(f"Generated new wallet for next cycle: {new_wallet['address']}")
            
            # 3. Wait for transactions to be mined
            wait_time = self.config.WAIT_TIME
            logger.info(f"Waiting {wait_time} seconds for transactions to be mined")
            time.sleep(wait_time)
            
            # 4. Transfer funds to the new wallet
            transfer_success = self.transfer_funds(self.index, self.index + 1)
            if not transfer_success:
                logger.warning("Transfer operation failed, but continuing to next cycle")
                return False
            
            # 5. Move to the next wallet
            self._increment_index()
            
            logger.info(f"Completed cycle {self.index}. Moving to next wallet.")
            return True
            
        except Exception as e:
            logger.error(f"Error in volume making cycle: {e}")
            return False

    def run(self):
        """Run the volume maker continuously."""
        logger.info("Starting volume maker process")
        
        cycle_count = 0
        while True:
            try:
                cycle_count += 1
                logger.info(f"Starting cycle {cycle_count}")
                
                success = self.start_cycle()
                if not success:
                    wait_time = min(30, self.config.WAIT_TIME * 2)
                    logger.warning(f"Cycle {cycle_count} had issues, waiting {wait_time} seconds before next attempt")
                    time.sleep(wait_time)
                else:
                    # Wait between successful cycles
                    time.sleep(self.config.WAIT_TIME)
                
            except KeyboardInterrupt:
                logger.info("Process interrupted by user")
                break
            except Exception as e:
                logger.error(f"Unexpected error in cycle {cycle_count}: {e}")
                time.sleep(30)  # Wait longer after an error


if __name__ == "__main__":
    try:
        # Set up argument parser
        parser = argparse.ArgumentParser(description='Volume maker for DEX trading')
        parser.add_argument('chain', help='Chain name from config (e.g., sonic, ethereum)')
        args = parser.parse_args()

        maker = VolumeMaker(args.chain)
        maker.run()
    except Exception as e:
        logger.critical(f"Fatal error: {e}")

