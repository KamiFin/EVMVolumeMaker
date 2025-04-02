import json
import time
import logging
import random
import os
import signal
from solana.rpc.api import Client
from solders.keypair import Keypair
from base_volume_maker import BaseVolumeMaker, CycleResult
from solana_config import (
    CHAIN_NAME,
    RPC_URL,
    ALTERNATIVE_RPCS,
    DEX_TYPE,
    POOL_ADDRESS,
    UNIT_BUDGET,
    UNIT_PRICE,
    MIN_BUY_AMOUNT,
    MAX_BUY_AMOUNT,
    TRANSFER_PERCENTAGE,
    WAIT_TIME,
    TRADE_WAIT_TIME,
    MAX_RETRIES,
    BACKOFF_FACTOR,
    MIN_BALANCE_THRESHOLD
)
from raydium.amm_v4 import buy, sell
from solders.pubkey import Pubkey

logger = logging.getLogger(__name__)

class SolanaVolumeMaker(BaseVolumeMaker):
    """Solana-specific implementation of volume maker"""
    
    def __init__(self, chain_name, mode='buy', single_wallet=False):
        """Initialize the Solana volume maker
        
        Args:
            chain_name (str): Name of the chain to operate on
            mode (str): Operation mode ('buy', 'sell', or 'trade')
            single_wallet (bool): Whether to use only the first wallet
        """
        # Initialize RPC tracking
        self.current_rpc_index = 0
        
        # Load configuration
        self.config = self._load_config(chain_name)
        
        # Initialize base class
        super().__init__(chain_name, mode, single_wallet)
        
        # Initialize Solana client
        self.client = self._get_connection()
        
        # Set up signal handlers
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

    def _load_config(self, chain_name):
        """Load Solana chain configuration"""
        if chain_name.lower() != "solana":
            raise ValueError("Use EVMVolumeMaker for non-Solana chains")
            
        # Create a Config object with all necessary attributes
        class Config:
            def __init__(self):
                # Chain configuration
                self.CHAIN_NAME = CHAIN_NAME
                self.RPC_URL = RPC_URL
                self.ALTERNATIVE_RPCS = ALTERNATIVE_RPCS
                self.DEX_TYPE = DEX_TYPE
                
                # DEX configuration
                self.POOL_ADDRESS = POOL_ADDRESS
                self.UNIT_BUDGET = UNIT_BUDGET
                self.UNIT_PRICE = UNIT_PRICE
                
                # Transaction configuration
                self.MIN_BUY_AMOUNT = MIN_BUY_AMOUNT
                self.MAX_BUY_AMOUNT = MAX_BUY_AMOUNT
                self.TRANSFER_PERCENTAGE = TRANSFER_PERCENTAGE
                self.WAIT_TIME = WAIT_TIME
                self.TRADE_WAIT_TIME = TRADE_WAIT_TIME
                self.MAX_RETRIES = MAX_RETRIES
                self.BACKOFF_FACTOR = BACKOFF_FACTOR
                self.MIN_BALANCE_THRESHOLD = MIN_BALANCE_THRESHOLD
                
                # File paths
                self.CONFIG_FILE = 'config.json'
        
        return Config()

    def _get_connection(self):
        """Get Solana RPC connection with fallback"""
        all_rpcs = [self.config.RPC_URL] + self.config.ALTERNATIVE_RPCS
        
        # Try the current RPC first
        rpc_url = all_rpcs[self.current_rpc_index % len(all_rpcs)]
        client = Client(rpc_url)
        
        # Test connection
        try:
            client.get_version()
            logger.info(f"Connected to Solana RPC: {rpc_url}")
            return client
        except Exception as e:
            logger.warning(f"Failed to connect to RPC {rpc_url}: {e}")
        
        # Try alternative RPCs
        for i, rpc in enumerate(all_rpcs):
            if i == self.current_rpc_index % len(all_rpcs):
                continue
                
            try:
                client = Client(rpc)
                client.get_version()
                self.current_rpc_index = i
                logger.info(f"Connected to alternative Solana RPC: {rpc}")
                return client
            except Exception as e:
                logger.warning(f"Failed to connect to RPC {rpc}: {e}")
                continue
                
        raise ConnectionError("Failed to connect to any Solana RPC endpoint")

    def _check_wallet_balance(self, address):
        """Check the balance of a Solana wallet."""
        try:
            # Convert string address to Pubkey object
            pubkey = Pubkey.from_string(address)
            
            balance = self.client.get_balance(pubkey)
            balance_in_sol = balance.value / 1e9  # Convert lamports to SOL
            logger.info(f"Wallet {address} balance: {balance_in_sol} SOL")
            return balance.value, balance_in_sol
        except Exception as e:
            logger.error(f"Error checking wallet balance: {e}")
            # Try switching RPC
            if self._switch_rpc():
                return self._check_wallet_balance(address)
            return 0, 0

    def _generate_wallet(self):
        """Generate a new Solana wallet."""
        keypair = Keypair()
        wallet = {
            "private_key": keypair.to_base58_string(),
            "address": str(keypair.pubkey())  # Get address from keypair's pubkey
        }
        self.wallets.append(wallet)
        self._save_wallets()
        logger.info(f"Generated new Solana wallet: {wallet['address']}")
        return wallet

    def _switch_rpc(self):
        """Switch to the next Solana RPC endpoint."""
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
                    new_client = Client(rpc_url)
                    new_client.get_version()
                    
                    self.client = new_client
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
            
            logger.info(f"Buying tokens with wallet {current_wallet['address']}")
            
            # Determine buy amount: random between min and max, or exact amount if they're equal
            if self.config.MIN_BUY_AMOUNT == self.config.MAX_BUY_AMOUNT:
                buy_amount = self.config.MIN_BUY_AMOUNT
            else:
                buy_amount = random.uniform(self.config.MIN_BUY_AMOUNT, self.config.MAX_BUY_AMOUNT)
            
            logger.info(f"Selected buy amount: {buy_amount} SOL")
            
            # Execute the buy transaction
            try:
                success = buy(
                    self.config.POOL_ADDRESS,
                    buy_amount,
                    slippage=0.05  # 5% slippage tolerance
                )
                
                if success is True:
                    logger.info(f"Successfully bought tokens with wallet {current_wallet['address']}")
                    return True
                else:
                    logger.warning(f"Failed to buy tokens with wallet {current_wallet['address']}")
                    return False
                    
            except Exception as e:
                logger.error(f"Error executing buy transaction: {str(e)}")
                return False
                
        except Exception as e:
            logger.error(f"Error in buy_tokens: {str(e)}")
            return False

    def sell_tokens(self):
        """Sell tokens using the current wallet with improved error handling."""
        try:
            current_wallet = self.wallets[self.index]
            
            logger.info(f"Selling tokens with wallet {current_wallet['address']}")
            
            # Execute the sell transaction
            try:
                success = sell(
                    self.config.POOL_ADDRESS,
                    percentage=100,  # Sell 100% of tokens
                    slippage=0.05  # 5% slippage tolerance
                )
                
                if success is True:
                    logger.info("Successfully sold tokens")
                    return True
                else:
                    logger.warning("Failed to sell tokens")
                    return False
                    
            except Exception as e:
                logger.error(f"Error executing sell transaction: {str(e)}")
                return False
                
        except Exception as e:
            logger.error(f"Error in sell_tokens: {str(e)}")
            return False

    def transfer_funds(self, from_index, to_index):
        """Transfer maximum funds from one wallet to another.
        
        TODO: Implement actual Solana transfer logic using:
        - Create a SystemProgram transfer instruction
        - Build and sign the transaction with the sender's keypair
        - Send and confirm the transaction
        - Verify the transfer through balance checks
        """
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
            
            # Calculate transfer amount (leave some SOL for fees)
            transfer_amount = int(initial_from_balance * 0.95)  # Transfer 95% of balance
            
            if transfer_amount <= 0:
                logger.error("Insufficient balance for transfer")
                return False
            
            # Create keypairs
            from_keypair = Keypair.from_base58_string(from_wallet['private_key'])
            to_pubkey = to_wallet['address']
            
            # Execute transfer
            try:
                # Note: This is a placeholder. You'll need to implement the actual Solana transfer logic
                # using the appropriate Solana transaction building and signing
                logger.warning("Solana transfer implementation needed")
                return False
                
            except Exception as e:
                logger.error(f"Error executing transfer: {e}")
                return False
                
        except Exception as e:
            logger.error(f"Critical error in transfer_funds: {e}")
            return False

    def start_cycle(self):
        """Start a volume making cycle with improved safety measures."""
        try:
            # Check if current wallet has funds
            current_wallet = self.wallets[self.index]
            balance, balance_in_sol = self._check_wallet_balance(current_wallet['address'])
            
            if balance <= self.config.MIN_BALANCE_THRESHOLD:
                logger.warning(f"Current wallet {current_wallet['address']} has insufficient funds: {balance_in_sol} SOL")
                
                # Try to find any wallet with sufficient balance
                wallet_index = self._find_wallet_with_balance()
                
                if wallet_index >= 0:
                    logger.info(f"Switching to wallet at index {wallet_index} which has sufficient funds")
                    self.index = wallet_index
                    return CycleResult.CONTINUE  # Continue with the found wallet
                else:
                    logger.error("No wallet with sufficient funds found. Stopping operations")
                    return CycleResult.STOP  # Stop operations
            
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
                
                # Wait between buy and sell
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
                # TODO: Multi-wallet mode for Solana is not yet implemented
                # - Need to implement transfer_funds method with proper Solana transaction building
                # - Need to implement proper recovery mechanism for failed transfers
                # - Need to handle token account creation and management
                logger.error("Multi-wallet mode is not yet implemented for Solana. Please use --single-wallet flag.")
                return CycleResult.STOP
            else:
                # In single wallet mode, just wait for transactions to be confirmed
                wait_time = self.config.WAIT_TIME
                logger.info(f"Single wallet mode: Waiting {wait_time} seconds for transactions to be confirmed")
                time.sleep(wait_time)
            
            # Move to the next wallet (will stay at index 0 in single wallet mode)
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
        """Load Solana wallets from solana_wallets array in config file."""
        if os.path.exists(self.config.CONFIG_FILE):
            try:
                with open(self.config.CONFIG_FILE, 'r') as f:
                    data = json.load(f)
                    self.wallets = data.get("solana_wallets", [])
                logger.info(f"Loaded {len(self.wallets)} Solana wallets from config file")
            except Exception as e:
                logger.error(f"Error loading Solana wallets from config: {e}")
        else:
            logger.info("No config file found, will create new Solana wallets")

    def _save_wallets(self):
        """Save Solana wallets to config file with improved error handling."""
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
                
                # Update solana_wallets array
                config_data["solana_wallets"] = self.wallets
                
                # Write to temporary file first
                temp_file = f"{self.config.CONFIG_FILE}.tmp"
                with open(temp_file, 'w') as f:
                    json.dump(config_data, f, indent=4)
                
                # Rename temporary file to actual config file
                os.replace(temp_file, self.config.CONFIG_FILE)
                
                logger.info(f"Successfully saved {len(self.wallets)} Solana wallets to config file")
                return True
                
            except Exception as e:
                logger.error(f"Error saving Solana wallets to config (attempt {attempt + 1}/{max_retries}): {e}")
                time.sleep(1)
        
        logger.critical("Failed to save Solana wallets after multiple attempts!")
        return False 