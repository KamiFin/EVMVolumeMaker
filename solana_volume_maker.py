import json
import time
import logging
import random
import os
import signal
import base58
import base64
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
    MIN_BALANCE_THRESHOLD,
    client, payer_keypair, BUY_SLIPPAGE, SELL_SLIPPAGE, SOL_DECIMAL
)
from raydium.amm_v4 import buy, sell
from solders.pubkey import Pubkey as SoldersPubkey
from utils.pool_utils import fetch_amm_v4_pool_keys, fetch_cpmm_pool_keys
from utils.api import get_pool_info_by_id
from solders.system_program import transfer, TransferParams
from solders.message import MessageV0
from solders.message import Message
from solders.transaction import VersionedTransaction, Transaction
from solana.transaction import Transaction as SolanaTransaction
from solders.compute_budget import set_compute_unit_limit, set_compute_unit_price
from utils.solana_utils import get_optimal_compute_unit_price
from utils.common_utils import confirm_txn
from solders.commitment_config import CommitmentLevel
from solana.rpc.types import TxOpts

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
        
        # Cache pool information
        self._initialize_pool_info()
        
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
            pubkey = SoldersPubkey.from_string(address)
            
            # Use string commitment level instead of enum to avoid "unhashable type" error
            balance = self.client.get_balance(pubkey, commitment="finalized")
            
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
        # Generate a new random Solana keypair
        keypair = Keypair()
        
        # Get the full keypair as bytes and encode to base58
        # This produces a 64-byte array (32 bytes secret key + 32 bytes public key)
        # The encoded string can be later decoded with from_base58_string()
        # to recreate the identical keypair for signing transactions
        keypair_bytes = bytes(keypair.to_bytes_array())
        private_key_base58 = base58.b58encode(keypair_bytes).decode('utf-8')
        
        wallet = {
            "private_key": private_key_base58,
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

    def _initialize_pool_info(self):
        """Initialize and cache pool information"""
        try:
            logger.info("Initializing pool information...")
            
            # Fetch pool keys based on DEX type
            if self.config.DEX_TYPE == "amm_v4":
                self.pool_keys = fetch_amm_v4_pool_keys(self.config.POOL_ADDRESS)
                if not self.pool_keys:
                    raise ValueError("Failed to fetch AMM v4 pool keys")
            elif self.config.DEX_TYPE == "cpmm":
                self.pool_keys = fetch_cpmm_pool_keys(self.config.POOL_ADDRESS)
                if not self.pool_keys:
                    raise ValueError("Failed to fetch CPMM pool keys")
            else:
                raise ValueError(f"Unsupported DEX type: {self.config.DEX_TYPE}")
            
            # Fetch additional pool information from Raydium API
            pool_info = get_pool_info_by_id(self.config.POOL_ADDRESS)
            if "error" in pool_info:
                logger.warning(f"Failed to fetch pool info from Raydium API: {pool_info['error']}")
            else:
                self.pool_info = pool_info
            
            logger.info("Pool information initialized successfully")
            
        except Exception as e:
            logger.error(f"Failed to initialize pool information: {e}")
            raise

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
            
            # Execute the buy transaction using cached pool information
            try:
                success = buy(
                    private_key=current_wallet['private_key'],  # Pass private key as first parameter
                    pair_address=self.config.POOL_ADDRESS,
                    sol_in=buy_amount,
                    slippage=float(BUY_SLIPPAGE * 100),  # Use float instead of round to preserve exact value
                    pool_keys=self.pool_keys  # Pass cached pool keys
                )
                
                if success is True:
                    logger.info(f"Successfully bought tokens with wallet {current_wallet['address']}")
                    return True
                else:
                    logger.warning(f"Failed to buy tokens with wallet {current_wallet['address']}")
                    # Mark wallet as failed when buy operation fails
                    self.mark_wallet_failed(current_wallet['address'])
                    return False
                    
            except Exception as e:
                logger.error(f"Error executing buy transaction: {str(e)}")
                # Mark wallet as failed on exception
                self.mark_wallet_failed(current_wallet['address'])
                return False
                
        except Exception as e:
            logger.error(f"Error in buy_tokens: {str(e)}")
            return False

    def sell_tokens(self):
        """Sell tokens using the current wallet with improved error handling."""
        try:
            current_wallet = self.wallets[self.index]
            
            logger.info(f"Selling tokens with wallet {current_wallet['address']}")
            
            # Execute the sell transaction using cached pool information
            try:
                success = sell(
                    private_key=current_wallet['private_key'],  # Pass private key as first parameter
                    pair_address=self.config.POOL_ADDRESS,
                    percentage=100,  # Sell 100% of tokens
                    slippage=float(SELL_SLIPPAGE * 100),  # Use float instead of round to preserve exact value
                    pool_keys=self.pool_keys  # Pass cached pool keys
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
        try:
            if from_index >= len(self.wallets) or to_index >= len(self.wallets):
                logger.error("Invalid wallet indices.")
                return False

            from_wallet = self.wallets[from_index]
            to_wallet = self.wallets[to_index]

            if self.is_wallet_failed(to_wallet['address']):
                logger.error(f"Wallet {to_wallet['address']} is marked as failed.")
                return False

            from_keypair = Keypair.from_base58_string(from_wallet['private_key'])
            to_pubkey = SoldersPubkey.from_string(to_wallet['address'])

            current_balance = self.client.get_balance(from_keypair.pubkey()).value
            if current_balance <= 0:
                logger.error("No balance.")
                return False
            logger.info(f"Current balance: {current_balance / 1e9} SOL")

            transfer_ix = transfer(
                TransferParams(
                    from_pubkey=from_keypair.pubkey(),
                    to_pubkey=to_pubkey,
                    lamports=1  # dummy
                )
            )

            blockhash_response = self.client.get_latest_blockhash()
            blockhash = blockhash_response.value.blockhash
            dummy_tx = SolanaTransaction(
                fee_payer=from_keypair.pubkey(),
                recent_blockhash=blockhash,
                instructions=[transfer_ix]
            )
            message = dummy_tx.compile_message()
            fee_response = self.client.get_fee_for_message(message)
            tx_fee = fee_response.value or 5000

            logger.info(f"Transaction fee: {tx_fee / 1e9} SOL")

            rent_resp = self.client.get_minimum_balance_for_rent_exemption(0)
            rent_exempt_balance = rent_resp.value
            max_transfer = current_balance - rent_exempt_balance - tx_fee
            logger.info(f"Leaving rent-exempt balance: {rent_exempt_balance / 1e9} SOL")
            if max_transfer <= 0:
                logger.error("Insufficient balance after fee.")
                return False

            if from_index == 0 and self.config.TRANSFER_PERCENTAGE < 1.0:
                max_transfer = int(max_transfer * self.config.TRANSFER_PERCENTAGE)

            logger.info(f"Transfer amount: {max_transfer / 1e9} SOL")
           

            txn_sig = None
            # Attempt transfer up to MAX_RETRIES times
            for attempt in range(self.config.MAX_RETRIES):
                try:
                    blockhash_response = self.client.get_latest_blockhash()
                    blockhash = blockhash_response.value.blockhash
                    transfer_ix = transfer(TransferParams(
                        from_pubkey=from_keypair.pubkey(),
                        to_pubkey=to_pubkey,
                        lamports=max_transfer
                    ))
                    logger.info("Compiling transaction message...")
                    compiled_message = MessageV0.try_compile(
                        from_keypair.pubkey(),
                        [transfer_ix],
                        [],
                        blockhash,
                    )
                    logger.info("Sending transaction...")
                    transaction = VersionedTransaction(compiled_message, [from_keypair])
                    txn_sig = self.client.send_transaction(
                        txn=transaction,
                        opts=TxOpts(skip_preflight=False),
                    ).value
                
                            
                    logger.info(f"Sent! Signature: {txn_sig}")
                    break

                except Exception as e:
                    logger.warning(f"Attempt {attempt+1} failed: {e}")
                    time.sleep(1)
        except Exception as e:
            logger.error(f"Error in transfer_funds: {e}")
            return False

        # Confirm
        if txn_sig is not None:
            confirmed = confirm_txn(txn_sig, max_retries=MAX_RETRIES, retry_interval=BACKOFF_FACTOR)
            if not confirmed:
                logger.error("Confirmation failed.")
                return False
            logger.info("Transfer completed.")
            return True
        else:
            logger.error("Transaction failed to send, no signature available.")
            return False

    def calculate_min_practical_balance(self):
        """Calculate the minimum practical balance required for operations."""
        try:
            # Fetch the minimum balance required for rent exemption
            rent_resp = self.client.get_minimum_balance_for_rent_exemption(0)
            rent_exempt_balance = rent_resp.value
            
            # Calculate the minimum practical balance
            min_practical_balance = max(
                (self.config.MIN_BUY_AMOUNT * 1e9) + 5000 + rent_exempt_balance,
                self.config.MIN_BALANCE_THRESHOLD
            )
            
            logger.info(f"Calculated minimum practical balance: {min_practical_balance / 1e9} SOL")
            return min_practical_balance
        except Exception as e:
            logger.error(f"Error calculating minimum practical balance: {e}")
            return self.config.MIN_BALANCE_THRESHOLD  # Fallback to a safe default

    def _find_wallet_with_balance(self):
        """Find a wallet with sufficient balance for operations."""
        try:
            logger.info("Searching for wallet with sufficient balance...")
            
            min_practical_balance = self.calculate_min_practical_balance()
            
            logger.info(f"Minimum required balance: {min_practical_balance / 1e9} SOL")
            
            # Start searching from the most recently created wallets (last in the list)
            for i in range(len(self.wallets) - 1, -1, -1):
                wallet = self.wallets[i]
                if self.is_wallet_failed(wallet['address']):
                    logger.info(f"Skipping failed wallet {wallet['address']}")
                    continue
                    
                balance, balance_in_sol = self._check_wallet_balance(wallet['address'])
                logger.info(f"Wallet {wallet['address']} balance: {balance_in_sol} SOL")
                
                if balance > min_practical_balance:
                    logger.info(f"Found wallet with sufficient balance: {wallet['address']}")
                    return i
            
            logger.error("No wallet found with sufficient balance")
            return -1
            
        except Exception as e:
            logger.error(f"Error in _find_wallet_with_balance: {e}")
            return -1

    def start_cycle(self):
        """Start a volume making cycle with improved safety measures."""
        try:
            # We already checked wallet balance in run() method, no need to check again
            current_wallet = self.wallets[self.index]
            logger.info(f"Starting cycle with wallet {current_wallet['address']}")
            
            # Perform operations based on mode
            if self.mode == 'buy':
                # Buy tokens with current wallet
                operation_success = self.buy_tokens()
                if not operation_success:
                    logger.error("Buy operation failed")
                    # Add an additional check for other wallets here
                    wallet_index = self._find_wallet_with_balance()
                    if wallet_index >= 0:
                        logger.info(f"Switching to wallet at index {wallet_index} after buy failure")
                        self.index = wallet_index
                        return CycleResult.CONTINUE
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
                    # Add an additional check for other wallets here
                    wallet_index = self._find_wallet_with_balance()
                    if wallet_index >= 0:
                        logger.info(f"Switching to wallet at index {wallet_index} after buy failure in trade mode")
                        self.index = wallet_index
                        return CycleResult.CONTINUE
                    return CycleResult.STOP
                
                # Wait between buy and sell
                trade_wait_time = self.config.TRADE_WAIT_TIME
                logger.info(f"Waiting {trade_wait_time} seconds between buy and sell operations")
                time.sleep(trade_wait_time)
                
                # Then sell tokens
                sell_success = self.sell_tokens()
                if not sell_success:
                    logger.error("Sell operation failed in trade mode")
                    self.mark_wallet_failed(current_wallet['address'])  # Mark wallet as failed on sell failure
                    return CycleResult.STOP
            else:
                logger.error(f"Unknown operation mode: {self.mode}")
                return CycleResult.STOP
            
            # Skip wallet creation and fund transfer in single wallet mode
            if not self.single_wallet:
                # Check if we already have a newly generated wallet that we failed to transfer to
                # This handles cases where a previous cycle created a wallet but failed to transfer funds
                new_wallet_exists = False
                next_index = -1
                
                if len(self.wallets) > self.index + 1:
                    # There are wallets after the current one
                    for i in range(len(self.wallets) - 1, self.index, -1):
                        next_wallet = self.wallets[i]
                        # Check if this wallet has no funds and is not marked as failed
                        balance, _ = self._check_wallet_balance(next_wallet['address'])
                        if balance == 0 and not self.is_wallet_failed(next_wallet['address']):
                            # This is likely a wallet we generated but failed to transfer to
                            new_wallet_exists = True
                            next_index = i
                            logger.info(f"Found empty wallet at index {next_index} to use for transfer")
                            break
                
                # Generate a new wallet only if we didn't find an existing empty one
                if not new_wallet_exists:
                    logger.info("Generating new wallet for next transfer")
                    self._generate_wallet()
                    next_index = len(self.wallets) - 1  # Index of the newly added wallet
                
                # Transfer funds to the next wallet
                transfer_success = self.transfer_funds(self.index, next_index)
                
                if not transfer_success:
                    logger.error("Failed to transfer funds to next wallet")
                    # DO NOT increment the index if transfer fails - stay with current wallet
                    return CycleResult.CONTINUE
                
                # Add a longer delay after transfers to allow RPC nodes to update state
                transfer_confirmation_delay = max(8, self.config.WAIT_TIME * 2)
                logger.info(f"Transfer successful. Waiting {transfer_confirmation_delay} seconds for RPC nodes to update balances...")
                time.sleep(transfer_confirmation_delay)
                
                # Wait for transactions to be confirmed
                wait_time = self.config.WAIT_TIME
                logger.info(f"Waiting additional {wait_time} seconds for transactions to be confirmed")
                time.sleep(wait_time)
                
                # Move the index to the new wallet with transferred funds
                self.index = next_index
                logger.info(f"Moving to wallet at index {self.index} for next cycle")
            else:
                # In single wallet mode, just wait for transactions to be confirmed
                wait_time = self.config.WAIT_TIME
                logger.info(f"Single wallet mode: Waiting {wait_time} seconds for transactions to be confirmed")
                time.sleep(wait_time)
                
                # In single wallet mode, we always stay on index 0
                self.index = 0
            
            logger.info(f"Completed cycle, next wallet index: {self.index}.")
            return CycleResult.CONTINUE
            
        except Exception as e:
            logger.error(f"Error in volume making cycle: {e}")
            return CycleResult.STOP  # Stop on any unexpected error

    def run(self):
        """Main volume making cycle with improved error handling"""
        try:
            last_save_time = time.time()
            save_interval = 300  # Save every 5 minutes
            failed_wallet_attempts = {}  # Track failed wallet skip attempts
            
            while True:
                try:
                    # Skip failed wallets
                    current_wallet = self.wallets[self.index]
                    if self.is_wallet_failed(current_wallet['address']):
                        # Check if we've already logged this wallet too many times
                        if current_wallet['address'] not in failed_wallet_attempts:
                            failed_wallet_attempts[current_wallet['address']] = 0
                        
                        failed_wallet_attempts[current_wallet['address']] += 1
                        
                        # Only log the first time and every 10th time after that
                        if failed_wallet_attempts[current_wallet['address']] <= 1 or failed_wallet_attempts[current_wallet['address']] % 10 == 0:
                            logger.info(f"Skipping failed wallet {current_wallet['address']}")
                        
                        # If all wallets are failed, stop operations
                        if all(self.is_wallet_failed(wallet['address']) for wallet in self.wallets):
                            logger.error("All wallets are marked as failed. Stopping operations.")
                            self._save_wallets()
                            return False
                        
                        self._increment_index()
                        # Add a small sleep to prevent CPU spinning when cycling through failed wallets
                        time.sleep(0.1)
                        continue
                    
                    # Check wallet balance before starting cycle
                    balance, balance_in_sol = self._check_wallet_balance(current_wallet['address'])
                    
                    min_practical_balance = self.calculate_min_practical_balance()
                    
                    if balance <= min_practical_balance:
                        logger.warning(f"Wallet {current_wallet['address']} has insufficient funds: {balance_in_sol} SOL")
                        logger.info(f"Searching for wallet with sufficient balance for operations...")
                        
                        # Try to find any wallet with enough balance, starting from the most recent ones
                        wallet_index = self._find_wallet_with_balance()
                        
                        if wallet_index >= 0:
                            logger.info(f"Switching to wallet at index {wallet_index} which has sufficient funds")
                            self.index = wallet_index
                            time.sleep(0.1)  # Small delay before continuing
                            continue  # Skip to the next iteration with new index
                        else:
                            logger.error("No wallet with sufficient funds found. Stopping operations")
                            self._save_wallets()
                            return False
                    
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
        try:
            if os.path.exists(self.config.CONFIG_FILE):
                with open(self.config.CONFIG_FILE, 'r') as f:
                    data = json.load(f)
                    self.wallets = data.get("solana_wallets", [])
                    logger.info(f"Loaded {len(self.wallets)} Solana wallets from config file")
                    
                    # Log wallet addresses for verification
                    for i, wallet in enumerate(self.wallets):
                        logger.info(f"Loaded wallet {i}: {wallet['address']}")
                        
                    # Verify we have at least one wallet
                    if not self.wallets:
                        logger.warning("No wallets found in config. Creating initial wallet...")
                        self._generate_wallet()
            else:
                logger.warning("No config file found. Will create new wallet.")
                self.wallets = []
                self._generate_wallet()
        except Exception as e:
            logger.error(f"Error loading Solana wallets from config: {e}")
            self.wallets = []
            self._generate_wallet()

    def _save_wallets(self):
        """Save Solana wallets to config file with improved error handling."""
        max_retries = 3
        for attempt in range(max_retries):
            try:
                # Log wallet addresses before saving
                logger.info("Saving wallets to config:")
                for i, wallet in enumerate(self.wallets):
                    logger.info(f"Wallet {i}: {wallet['address']}")
                
                # Create a backup of the existing config first
                if os.path.exists(self.config.CONFIG_FILE):
                    backup_file = f"{self.config.CONFIG_FILE}.backup"
                    with open(self.config.CONFIG_FILE, 'r') as src:
                        with open(backup_file, 'w') as dst:
                            dst.write(src.read())
                
                # Load existing config to preserve other settings
                config_data = {}
                if os.path.exists(self.config.CONFIG_FILE):
                    with open(self.config.CONFIG_FILE, 'r') as f:
                        config_data = json.load(f)
                
                # Update solana_wallets array
                config_data["solana_wallets"] = self.wallets
                
                # Write to temporary file first
                temp_file = f"{self.config.CONFIG_FILE}.tmp"
                with open(temp_file, 'w') as f:
                    json.dump(config_data, f, indent=4)
                
                # Verify the temporary file was written correctly
                with open(temp_file, 'r') as f:
                    verify_data = json.load(f)
                    if not verify_data.get("solana_wallets"):
                        raise ValueError("Failed to verify wallet data in temporary file")
                
                # Rename temporary file to actual config file
                os.replace(temp_file, self.config.CONFIG_FILE)
                
                logger.info(f"Successfully saved {len(self.wallets)} Solana wallets to config file")
                return True
                
            except Exception as e:
                logger.error(f"Error saving Solana wallets to config (attempt {attempt + 1}/{max_retries}): {e}")
                time.sleep(1)
        
        logger.critical("Failed to save Solana wallets after multiple attempts!")
        return False 
