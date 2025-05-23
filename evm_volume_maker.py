import json
import time
import logging
import random
import os
from web3 import Web3
from eth_account import Account
from web3.middleware import geth_poa_middleware
from base_volume_maker import BaseVolumeMaker, CycleResult
from utils.gas_manager import GasManager
from utils.web3_utils import get_web3_connection
from utils.transfer_utils import transfer_max_native
import sniper
import signal

logger = logging.getLogger(__name__)

class EVMVolumeMaker(BaseVolumeMaker):
    """EVM-specific implementation of volume maker"""
    
    def __init__(self, chain_name, mode='buy', single_wallet=False):
        """Initialize the EVM volume maker
        
        Args:
            chain_name (str): Name of the chain to operate on
            mode (str): Operation mode ('buy', 'sell', or 'trade')
            single_wallet (bool): Whether to use only the first wallet
        """
        # Initialize Web3 connection and RPC tracking
        self.w3 = None
        self.current_rpc_index = 0
        
        # Load configuration
        self.config = self._load_config(chain_name)
        
        # Initialize base class
        super().__init__(chain_name, mode, single_wallet)
        
        # Initialize Web3 connection
        self.w3 = self._get_connection()
        
        # Set up signal handlers
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

    def _load_config(self, chain_name):
        """Load EVM chain configuration"""
        # Special handling for Solana chain
        if chain_name.lower() == "solana":
            raise ValueError("Use SolanaVolumeMaker for Solana chain")
            
        # For other chains, load from config.json
        with open('config.json', 'r') as f:
            config = json.load(f)
            
        if chain_name not in config['chains']:
            raise ValueError(f"Chain '{chain_name}' not found in config.json")
            
        chain_config = config['chains'][chain_name]
        
        # Create a Config object with all necessary attributes
        class Config:
            def __init__(self, chain_config):
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
                self.TRADE_WAIT_TIME = chain_config['transaction'].get('trade_wait_time', 1)
                self.MAX_RETRIES = chain_config['transaction'].get('max_retries', 3)
                self.BACKOFF_FACTOR = chain_config['transaction'].get('backoff_factor', 2)
                self.MIN_BALANCE_THRESHOLD = chain_config['transaction'].get('min_balance_threshold', 0.00001)
                
                # Slippage settings
                self.BUY_SLIPPAGE = chain_config['transaction'].get('buy_slippage', 0.005)
                self.SELL_SLIPPAGE = chain_config['transaction'].get('sell_slippage', 0.005)
                
                # File paths
                self.CONFIG_FILE = 'config.json'
                
                # Load token ABI
                self.token_abi = json.loads('[{"constant":true,"inputs":[],"name":"name","outputs":[{"name":"","type":"string"}],"payable":false,"stateMutability":"view","type":"function"},{"constant":false,"inputs":[{"name":"spender","type":"address"},{"name":"tokens","type":"uint256"}],"name":"approve","outputs":[{"name":"success","type":"bool"}],"payable":false,"stateMutability":"nonpayable","type":"function"},{"constant":true,"inputs":[],"name":"totalSupply","outputs":[{"name":"","type":"uint256"}],"payable":false,"stateMutability":"view","type":"function"},{"constant":false,"inputs":[{"name":"from","type":"address"},{"name":"to","type":"address"},{"name":"tokens","type":"uint256"}],"name":"transferFrom","outputs":[{"name":"success","type":"bool"}],"payable":false,"stateMutability":"nonpayable","type":"function"},{"constant":true,"inputs":[],"name":"decimals","outputs":[{"name":"","type":"uint8"}],"payable":false,"stateMutability":"view","type":"function"},{"constant":true,"inputs":[{"name":"tokenOwner","type":"address"}],"name":"balanceOf","outputs":[{"name":"balance","type":"uint256"}],"payable":false,"stateMutability":"view","type":"function"},{"constant":true,"inputs":[],"name":"symbol","outputs":[{"name":"","type":"string"}],"payable":false,"stateMutability":"view","type":"function"},{"constant":false,"inputs":[{"name":"to","type":"address"},{"name":"tokens","type":"uint256"}],"name":"transfer","outputs":[{"name":"success","type":"bool"}],"payable":false,"stateMutability":"nonpayable","type":"function"},{"constant":true,"inputs":[{"name":"tokenOwner","type":"address"},{"name":"spender","type":"address"}],"name":"allowance","outputs":[{"name":"remaining","type":"uint256"}],"payable":false,"stateMutability":"view","type":"function"},{"anonymous":false,"inputs":[{"indexed":true,"name":"from","type":"address"},{"indexed":true,"name":"to","type":"address"},{"indexed":false,"name":"tokens","type":"uint256"}],"name":"Transfer","type":"event"},{"anonymous":false,"inputs":[{"indexed":true,"name":"tokenOwner","type":"address"},{"indexed":true,"name":"spender","type":"address"},{"indexed":false,"name":"tokens","type":"uint256"}],"name":"Approval","type":"event"}]')
        
        return Config(chain_config)

    def _get_connection(self):
        """Get Web3 connection with RPC fallback"""
        all_rpcs = [self.config.RPC_URL] + self.config.ALTERNATIVE_RPCS
        
        # Try the current RPC first
        rpc_url = all_rpcs[self.current_rpc_index % len(all_rpcs)]
        w3 = get_web3_connection(rpc_url, self.config.CHAIN_ID)
        
        # Test connection
        try:
            w3.eth.block_number
            logger.info(f"Connected to RPC: {rpc_url}")
            return w3
        except Exception as e:
            logger.warning(f"Failed to connect to RPC {rpc_url}: {e}")
        
        # Try alternative RPCs
        for i, rpc in enumerate(all_rpcs):
            if i == self.current_rpc_index % len(all_rpcs):
                continue
                
            try:
                w3 = get_web3_connection(rpc, self.config.CHAIN_ID)
                w3.eth.block_number
                self.current_rpc_index = i
                logger.info(f"Connected to alternative RPC: {rpc}")
                return w3
            except Exception as e:
                logger.warning(f"Failed to connect to RPC {rpc}: {e}")
                continue
                
        raise ConnectionError("Failed to connect to any RPC endpoint")

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

    def _generate_wallet(self):
        """Generate a new EVM wallet."""
        acct = Account.create()
        wallet = {
            "address": acct.address,
            "private_key": acct._private_key.hex()
        }
        self.wallets.append(wallet)
        self._save_wallets()
        logger.info(f"Generated new wallet: {wallet['address']}")
        return wallet

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

    def _switch_rpc(self):
        """Switch to the next RPC endpoint with improved error handling."""
        try:
            original_rpc_index = self.current_rpc_index
            max_attempts = len(self.config.ALTERNATIVE_RPCS) + 1
            
            for _ in range(max_attempts):
                self.current_rpc_index = (self.current_rpc_index + 1) % max_attempts
                
                # Skip if we've tried all RPCs and are back to the original
                if self.current_rpc_index == original_rpc_index:
                    logger.error("Tried all available RPCs without success")
                    return False
                    
                try:
                    all_rpcs = [self.config.RPC_URL] + self.config.ALTERNATIVE_RPCS
                    rpc_url = all_rpcs[self.current_rpc_index]
                    
                    logger.info(f"Attempting to switch to RPC: {rpc_url}")
                    
                    # Test connection before switching
                    new_w3 = Web3(Web3.HTTPProvider(rpc_url))
                    new_w3.middleware_onion.inject(geth_poa_middleware, layer=0)
                    
                    if new_w3.is_connected():
                        self.w3 = new_w3
                        
                        # Update sniper's web3 instance
                        sniper.web3 = new_w3
                        sniper.rpc = rpc_url
                        
                        logger.info(f"Successfully switched to RPC: {rpc_url}")
                        return True
                        
                except Exception as e:
                    logger.warning(f"Failed to connect to RPC {rpc_url}: {e}")
                    continue
                    
            return False
            
        except Exception as e:
            logger.error(f"Error in RPC switching: {e}")
            return False

    def buy_tokens(self):
        """Buy tokens using the current wallet with a random amount between min and max."""
        try:
            current_wallet = self.wallets[self.index]
            gas_price = self._get_current_gas_price()
            
            logger.info(f"Buying tokens with wallet {current_wallet['address']}")
            
            # Initialize sniper with the current chain configuration
            sniper.init_globals(self.chain_name)
            
            # Determine buy amount: random between min and max, or exact amount if they're equal
            if self.config.MIN_BUY_AMOUNT == self.config.MAX_BUY_AMOUNT:
                buy_amount = self.config.MIN_BUY_AMOUNT
            else:
                buy_amount = random.uniform(self.config.MIN_BUY_AMOUNT, self.config.MAX_BUY_AMOUNT)
            
            logger.info(f"Selected buy amount: {buy_amount} {self.config.NATIVE_TOKEN}")
            
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

    def sell_tokens(self):
        """Sell tokens using the current wallet with improved error handling."""
        try:
            current_wallet = self.wallets[self.index]
            gas_price = self._get_current_gas_price()
            
            logger.info(f"Selling tokens with wallet {current_wallet['address']}")
            
            # Initialize sniper with the current chain configuration
            sniper.init_globals(self.chain_name)
            
            # Check token balance first
            token_contract = Web3(Web3.HTTPProvider(sniper.rpc)).eth.contract(
                address=self.config.TOKEN_CONTRACT, 
                abi=self.config.token_abi
            )
            
            # Add balance check with proper decimal handling
            token_balance = token_contract.functions.balanceOf(current_wallet["address"]).call()
            decimals = token_contract.functions.decimals().call()
            human_readable_balance = token_balance / (10 ** decimals)
            
            logger.info(f"Current token balance: {human_readable_balance} tokens")
            
            if token_balance == 0:
                logger.error("No tokens to sell in wallet")
                return False
            
            # Get potential ETH value before selling
            potential_value = sniper.getProfit(self.config.TOKEN_CONTRACT, current_wallet["address"])
            if potential_value:
                logger.info(f"Potential ETH value if sold: {potential_value[1]} ETH")
            
            # Verify approval status before selling
            current_allowance = sniper.check_token_allowance(
                self.config.TOKEN_CONTRACT,
                current_wallet["address"],
                sniper.contract.address
            )
            
            if current_allowance < token_balance:
                logger.warning("Insufficient allowance, requesting approval...")
                approval_success = sniper.approve_tokens(
                    self.config.TOKEN_CONTRACT,
                    current_wallet["address"],
                    current_wallet["private_key"],
                    gas_price
                )
                if not approval_success:
                    logger.error("Failed to approve tokens")
                    return False
                
            # Attempt to sell with retries
            for attempt in range(3):  # Try up to 3 times
                try:
                    sell_success = sniper.sellTokens(
                        self.config.TOKEN_CONTRACT,
                        current_wallet["address"],
                        current_wallet["private_key"],
                        gas_price
                    )
                    if sell_success:
                        logger.info("Successfully sold tokens")
                        return True
                    else:
                        logger.warning(f"Sell attempt {attempt + 1} failed, {'retrying' if attempt < 2 else 'giving up'}")
                        time.sleep(2 * (attempt + 1))  # Exponential backoff
                except Exception as e:
                    logger.error(f"Error during sell attempt {attempt + 1}: {str(e)}")
                    if attempt < 2:
                        time.sleep(2 * (attempt + 1))
                        continue
                    return False
                
            return False
            
        except Exception as e:
            logger.error(f"Critical error in sell_tokens: {str(e)}")
            return False

    def transfer_funds(self, from_index, to_index):
        """Transfer maximum funds from one wallet to another with robust error handling and RPC switching."""
        try:
            if from_index >= len(self.wallets) or to_index >= len(self.wallets):
                logger.error(f"Invalid wallet indices: {from_index}, {to_index}")
                return False
            
            from_wallet = self.wallets[from_index]
            to_wallet = self.wallets[to_index]
            
            # Store initial balances
            initial_from_balance = self._check_wallet_balance(from_wallet['address'])[0]
            initial_to_balance = self._check_wallet_balance(to_wallet['address'])[0]
            
            logger.info(f"Transferring funds from {from_wallet['address']} to {to_wallet['address']}")
            
            # Track last transaction hash
            last_tx_hash = None
            max_transfer_retries = 3
            
            for attempt in range(max_transfer_retries):
                try:
                    # Important: Check if funds already moved
                    current_from_balance = self._check_wallet_balance(from_wallet['address'])[0]
                    current_to_balance = self._check_wallet_balance(to_wallet['address'])[0]
                    
                    # If balance moved from source to destination, consider it successful
                    if (current_from_balance < initial_from_balance and 
                        current_to_balance > initial_to_balance):
                        logger.info("Transfer detected as successful through balance check")
                        return True
                    
                    # If source has no balance but had before, check last tx
                    if current_from_balance == 0 and initial_from_balance > 0:
                        if last_tx_hash:
                            try:
                                receipt = self.w3.eth.get_transaction_receipt(last_tx_hash)
                                if receipt and receipt['status'] == 1:
                                    logger.info(f"Previous transaction {last_tx_hash} was successful")
                                    return True
                            except Exception as e:
                                logger.warning(f"Error checking previous transaction: {e}")
                                # Don't return False here - might need to check balances again

                    # Attempt transfer with current RPC
                    transfer_result = transfer_max_native(self, from_wallet, to_wallet['address'])
                    
                    # Fix: Handle both tuple and boolean return types
                    if isinstance(transfer_result, tuple):
                        transfer_success, tx_hash = transfer_result
                        last_tx_hash = tx_hash
                    else:
                        # If just a boolean was returned
                        transfer_success = transfer_result
                    
                    if transfer_success:
                        return True
                        
                    # If transfer failed, try switching RPC
                    if self._switch_rpc():
                        logger.info("Switched to alternative RPC endpoint")
                    else:
                        logger.error("Failed to switch to alternative RPC")
                        
                except Exception as e:
                    logger.error(f"Transfer attempt {attempt + 1} failed: {e}")
                    
                    # IMPORTANT: Check balances before giving up
                    try:
                        final_from_balance = self._check_wallet_balance(from_wallet['address'])[0]
                        final_to_balance = self._check_wallet_balance(to_wallet['address'])[0]
                        
                        if (final_from_balance < initial_from_balance and 
                            final_to_balance > initial_to_balance):
                            logger.info("Transfer detected as successful through final balance check")
                            return True
                    except Exception as check_e:
                        logger.error(f"Error in final balance check: {check_e}")
                    
                    # Try switching RPC on error
                    if self._switch_rpc():
                        logger.info("Switched to alternative RPC after error")
                    
                    if attempt < max_transfer_retries - 1:
                        wait_time = 5 * (attempt + 1)  # Exponential backoff
                        logger.warning(f"Waiting {wait_time} seconds before retry...")
                        time.sleep(wait_time)
                        
            logger.error("All transfer attempts failed")
            return False
            
        except Exception as e:
            logger.error(f"Critical error in transfer_funds: {e}")
            return False

    def start_cycle(self):
        """Start a volume making cycle with improved safety measures."""
        try:
            # Check if current wallet has funds
            current_wallet = self.wallets[self.index]
            balance, balance_in_eth = self._check_wallet_balance(current_wallet['address'])
            
            if balance <= self.w3.to_wei(self.config.MIN_BALANCE_THRESHOLD, 'ether'):
                logger.warning(f"Current wallet {current_wallet['address']} has insufficient funds: {balance_in_eth} {self.config.NATIVE_TOKEN}")
                
                # Try to find any wallet with sufficient balance
                wallet_index = self._find_wallet_with_balance()
                
                if wallet_index >= 0:
                    logger.info(f"Switching to wallet at index {wallet_index} which has sufficient funds")
                    self.index = wallet_index
                    return CycleResult.CONTINUE  # Continue with the found wallet
                else:
                    logger.error("No wallet with sufficient funds found. Stopping operations")
                    return CycleResult.STOP  # Stop operations
            
            # Initialize sniper with current chain configuration
            logger.info("Initializing sniper module...")
            sniper.init_globals(self.chain_name)
            
            # Check if the token pair exists on the DEX
            logger.info("Checking if token pair exists...")
            if not sniper.check_pair_exists(self.config.TOKEN_CONTRACT):
                logger.error(f"Token pair does not exist on the DEX. Please create liquidity first.")
                return CycleResult.STOP  # Stop if pair doesn't exist
            
            # Perform operations based on mode
            if self.mode == 'buy':
                # Buy tokens with current wallet
                operation_success = self.buy_tokens()
                if not operation_success:
                    logger.error("Buy operation failed")
                    return CycleResult.STOP
            elif self.mode == 'sell':
                # Sell tokens with current wallet
                operation_success = self.sell_tokens()
                if not operation_success:
                    logger.error("Sell operation failed")
                    return CycleResult.STOP
            elif self.mode == 'trade':
                # First buy tokens
                buy_success = self.buy_tokens()
                if not buy_success:
                    logger.error("Buy operation failed in trade mode")
                    return CycleResult.STOP
                
                # Wait between buy and sell (using the custom parameter)
                trade_wait_time = self.config.TRADE_WAIT_TIME
                logger.info(f"Waiting {trade_wait_time} seconds between buy and sell operations")
                time.sleep(trade_wait_time)
                
                # Then sell tokens
                sell_success = self.sell_tokens()
                if not sell_success:
                    logger.error("Sell operation failed in trade mode")
                    return CycleResult.STOP
            else:
                logger.error(f"Unknown operation mode: {self.mode}")
                return CycleResult.STOP
            
            # Skip wallet creation and fund transfer in single wallet mode
            if not self.single_wallet:
                # 2. Generate new wallet
                new_wallet = self._generate_wallet()
                logger.info("New wallet generated and saved to config")
                
                # 3. Wait for transactions to be mined
                wait_time = self.config.WAIT_TIME
                logger.info(f"Waiting {wait_time} seconds for transactions to be mined")
                time.sleep(wait_time)
                
                # 4. Transfer funds to the new wallet
                transfer_success = self.transfer_funds(self.index, len(self.wallets) - 1)
                if not transfer_success:
                    logger.error("Transfer failed - stopping cycle")
                    return CycleResult.STOP  # Stop if transfer fails
            else:
                # In single wallet mode, just wait for transactions to be mined
                wait_time = self.config.WAIT_TIME
                logger.info(f"Single wallet mode: Waiting {wait_time} seconds for transactions to be mined")
                time.sleep(wait_time)
            
            # 5. Move to the next wallet (will stay at index 0 in single wallet mode due to _increment_index logic)
            self._increment_index()
            
            logger.info(f"Completed cycle {self.index}.")
            return CycleResult.CONTINUE
            
        except Exception as e:
            logger.error(f"Error in volume making cycle: {e}")
            return CycleResult.STOP  # Stop on any unexpected error

    def run(self):
        """Main volume making cycle with improved error handling"""
        try:
            last_save_time = time.time()
            save_interval = 300  # Save every 5 minutes
            
            while True:
                try:
                    # Skip failed wallets
                    current_wallet = self.wallets[self.index]
                    if self.is_wallet_failed(current_wallet['address']):
                        logger.info(f"Skipping failed wallet {current_wallet['address']}")
                        self._increment_index()
                        continue
                    
                    # Periodic save of wallets
                    if time.time() - last_save_time > save_interval:
                        self._save_wallets()
                        last_save_time = time.time()
                    
                    # Start the cycle
                    cycle_result = self.start_cycle()
                    if not cycle_result:  # If CycleResult.STOP (False)
                        self.mark_wallet_failed(current_wallet['address'])
                        logger.error("Cycle failed - marking wallet as failed and stopping operations")
                        self._save_wallets()  # Save state after failure
                        return False  # Exit the run method
                    
                    # Wait between cycles
                    time.sleep(self.config.WAIT_TIME)
                    
                except Exception as cycle_error:
                    logger.error(f"Error in cycle execution: {cycle_error}")
                    self._save_wallets()
                    return False  # Exit on any error
            
        except Exception as e:
            logger.error(f"Critical error in volume maker: {e}")
            self._save_wallets()
            return False 

    def _load_wallets(self):
        """Load EVM wallets from config file if it exists."""
        if os.path.exists(self.config.CONFIG_FILE):
            try:
                with open(self.config.CONFIG_FILE, 'r') as f:
                    data = json.load(f)
                    self.wallets = data.get("wallets", [])
                logger.info(f"Loaded {len(self.wallets)} EVM wallets from config file")
            except Exception as e:
                logger.error(f"Error loading EVM wallets from config: {e}")
        else:
            logger.info("No config file found, will create new EVM wallets")

    def _save_wallets(self):
        """Save EVM wallets to config file with improved error handling."""
        max_retries = 3
        for attempt in range(max_retries):
            try:
                # Create a backup of the existing config first
                if os.path.exists(self.config.CONFIG_FILE):
                    backup_file = f"{self.config.CONFIG_FILE}.backup"
                    with open(self.config.CONFIG_FILE, 'r') as src:
                        with open(backup_file, 'w') as dst:
                            dst.write(src.read())
                
                # Load existing config to preserve other settings
                with open(self.config.CONFIG_FILE, 'r') as f:
                    config_data = json.load(f)
                
                # Update wallets
                config_data["wallets"] = self.wallets
                
                # Write to temporary file first
                temp_file = f"{self.config.CONFIG_FILE}.tmp"
                with open(temp_file, 'w') as f:
                    json.dump(config_data, f, indent=4)
                
                # Rename temporary file to actual config file
                os.replace(temp_file, self.config.CONFIG_FILE)
                
                logger.info(f"Successfully saved {len(self.wallets)} EVM wallets to config file")
                return True
                
            except Exception as e:
                logger.error(f"Error saving EVM wallets to config (attempt {attempt + 1}/{max_retries}): {e}")
                time.sleep(1)
        
        logger.critical("Failed to save EVM wallets after multiple attempts!")
        return False 