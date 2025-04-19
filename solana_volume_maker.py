import json
import time
import logging
import random
import os
import signal
import base58
import base64
import concurrent.futures
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
from raydium.amm_v4 import buy, sell, sol_for_tokens
from solders.pubkey import Pubkey as SoldersPubkey
from utils.pool_utils import fetch_amm_v4_pool_keys, fetch_cpmm_pool_keys, get_amm_v4_reserves
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
from solana.rpc.types import TxOpts, TokenAccountOpts
from raydium.constants import ACCOUNT_LAYOUT_LEN, SOL_DECIMAL, TOKEN_PROGRAM_ID, WSOL
from spl.token.client import Token
from spl.token.instructions import (
    CloseAccountParams,
    InitializeAccountParams,
    close_account,
    create_associated_token_account,
    get_associated_token_address,
    initialize_account,
)
from typing import Optional
from solana.rpc.commitment import Processed
from solders.system_program import (
    CreateAccountWithSeedParams,
    create_account_with_seed,
)
from solders.transaction import VersionedTransaction  # type: ignore
from spl.token.client import Token
from utils.pool_utils import (
    AmmV4PoolKeys,
    fetch_amm_v4_pool_keys,
    get_amm_v4_reserves,
    make_amm_v4_swap_instruction
)

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
        
        # For batch mode
        self.batch_wallets = []

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

    def _generate_batch_wallets(self, count=5):
        """
        Generate multiple wallets for batch operations.
        
        Args:
            count (int): Number of wallets to generate
            
        Returns:
            list: List of newly generated wallet dictionaries
        """
        logger.info(f"Generating {count} wallets for batch operations")
        
        # Clear previous batch wallets
        self.batch_wallets = []
        
        # Generate new wallets
        for i in range(count):
            # Generate a new random Solana keypair
            keypair = Keypair()
            
            # Get the full keypair as bytes and encode to base58
            keypair_bytes = bytes(keypair.to_bytes_array())
            private_key_base58 = base58.b58encode(keypair_bytes).decode('utf-8')
            
            wallet = {
                "private_key": private_key_base58,
                "address": str(keypair.pubkey())
            }
            
            self.batch_wallets.append(wallet)
            logger.info(f"Generated batch wallet {i+1}/{count}: {wallet['address']}")
        
        # Save batch wallets to a temporary recovery file
        self._save_batch_wallets_for_recovery()
            
        return self.batch_wallets
    
    def _save_batch_wallets_for_recovery(self):
        """
        Save batch wallets to a temporary file for recovery in case of program crash.
        """
        try:
            batch_recovery_file = "batch_wallets_recovery.json"
            
            # Get timestamp
            timestamp = time.strftime("%Y-%m-%d_%H-%M-%S")
            
            recovery_data = {
                "timestamp": timestamp,
                "main_wallet": self.wallets[0]['address'],
                "batch_wallets": self.batch_wallets
            }
            
            with open(batch_recovery_file, 'w') as f:
                json.dump(recovery_data, f, indent=4)
                
            logger.info(f"Saved {len(self.batch_wallets)} batch wallets to recovery file: {batch_recovery_file}")
            
        except Exception as e:
            logger.error(f"Error saving batch wallets for recovery: {e}")
    
    def recover_batch_wallets(self):
        """
        Recover funds from batch wallets saved in the recovery file.
        This can be used if the program stopped unexpectedly during batch mode.
        
        Returns:
            bool: True if recovery was successful, False otherwise
        """
        try:
            batch_recovery_file = "batch_wallets_recovery.json"
            
            if not os.path.exists(batch_recovery_file):
                logger.error("No batch wallet recovery file found")
                return False
                
            with open(batch_recovery_file, 'r') as f:
                recovery_data = json.load(f)
                
            if 'batch_wallets' not in recovery_data or not recovery_data['batch_wallets']:
                logger.error("No batch wallets found in recovery file")
                return False
                
            recovered_wallets = recovery_data['batch_wallets']
            logger.info(f"Found {len(recovered_wallets)} batch wallets in recovery file")
            
            # Set the batch wallets from the recovery file
            self.batch_wallets = recovered_wallets
            
            # Check balance of each wallet
            total_recovered = 0
            for i, wallet in enumerate(self.batch_wallets):
                try:
                    address = wallet['address']
                    balance_lamports, balance_sol = self._check_wallet_balance(address)
                    
                    if balance_lamports > 5000:  # If it has more than 5000 lamports (0.000005 SOL)
                        logger.info(f"Batch wallet {i}: {address} has {balance_sol} SOL")
                        total_recovered += 1
                    else:
                        logger.info(f"Batch wallet {i}: {address} has insufficient balance ({balance_sol} SOL)")
                except Exception as e:
                    logger.error(f"Error checking wallet {i} balance: {e}")
            
            if total_recovered == 0:
                logger.warning("No batch wallets with balance found")
                return False
                
            logger.info(f"Found {total_recovered} batch wallets with balance to recover")
            
            # Ask for confirmation
            print(f"\nFound {total_recovered} batch wallets with balance to recover.")
            confirmation = input("Do you want to proceed with recovery? (yes/no): ")
            
            if confirmation.lower() not in ["yes", "y"]:
                logger.info("Recovery cancelled by user")
                return False
            
            # Proceed with recovery - first try to sell tokens
            logger.info("Attempting to sell tokens from batch wallets...")
            sell_success = 0
            
            for i, wallet in enumerate(self.batch_wallets):
                try:
                    balance_lamports, balance_sol = self._check_wallet_balance(wallet['address'])
                    
                    if balance_lamports > 5000:
                        logger.info(f"Attempting to sell tokens from batch wallet {i}: {wallet['address']}")
                        
                        # Try to sell tokens
                        sell_result = sell(
                            private_key=wallet['private_key'],
                            pair_address=self.config.POOL_ADDRESS,
                            percentage=100,
                            slippage=float(SELL_SLIPPAGE * 100),
                            pool_keys=self.pool_keys
                        )
                        
                        if sell_result:
                            logger.info(f"Successfully sold tokens from batch wallet {i}")
                            sell_success += 1
                        else:
                            logger.warning(f"Failed to sell tokens from batch wallet {i}")
                        
                        # Wait a bit between operations
                        time.sleep(2)
                except Exception as e:
                    logger.error(f"Error selling tokens from batch wallet {i}: {e}")
            
            logger.info(f"Sold tokens from {sell_success} batch wallets")
            
            # Now transfer any remaining SOL back to main wallet
            logger.info("Transferring remaining SOL back to main wallet...")
            transfer_success = 0
            transfer_total = 0.0
            
            main_wallet = self.wallets[0]
            main_pubkey = SoldersPubkey.from_string(main_wallet['address'])
            
            for i, wallet in enumerate(self.batch_wallets):
                try:
                    from_keypair = Keypair.from_base58_string(wallet['private_key'])
                    balance = self.client.get_balance(from_keypair.pubkey()).value
                    
                    if balance > 5000:  # More than 0.000005 SOL
                        # Calculate fees
                        tx_fee = 5000  # Estimate 5000 lamports (0.000005 SOL) for fees
                        
                        # Transfer amount (leave some for fees)
                        transfer_amount = max(0, balance - tx_fee - 5000)  # Additional 5000 lamports buffer
                        
                        if transfer_amount <= 0:
                            logger.warning(f"Insufficient balance to transfer from batch wallet {i}")
                            continue
                        
                        # Create transfer transaction
                        transfer_ix = transfer(
                            TransferParams(
                                from_pubkey=from_keypair.pubkey(),
                                to_pubkey=main_pubkey,
                                lamports=transfer_amount
                            )
                        )
                        
                        # Get fresh blockhash
                        blockhash_response = self.client.get_latest_blockhash()
                        blockhash = blockhash_response.value.blockhash
                        
                        # Create and sign transaction
                        compiled_message = MessageV0.try_compile(
                            from_keypair.pubkey(),
                            [transfer_ix],
                            [],
                            blockhash,
                        )
                        
                        transaction = VersionedTransaction(compiled_message, [from_keypair])
                        
                        # Send transaction
                        txn_sig = self.client.send_transaction(
                            txn=transaction,
                            opts=TxOpts(skip_preflight=False),
                        ).value
                        
                        # Wait for confirmation
                        confirmed = confirm_txn(txn_sig, max_retries=self.config.MAX_RETRIES, retry_interval=self.config.BACKOFF_FACTOR)
                        
                        if confirmed:
                            logger.info(f"Successfully transferred {transfer_amount/1e9} SOL from batch wallet {i} to main wallet")
                            transfer_success += 1
                            transfer_total += transfer_amount/1e9
                        else:
                            logger.warning(f"Failed to confirm SOL transfer from batch wallet {i}")
                        
                        # Wait a bit between operations
                        time.sleep(2)
                except Exception as e:
                    logger.error(f"Error transferring from batch wallet {i}: {e}")
            
            logger.info(f"Transferred SOL from {transfer_success} batch wallets, total: {transfer_total} SOL")
            
            # Rename the recovery file after successful recovery
            try:
                # Get current timestamp if not already defined
                if 'timestamp' not in locals():
                    timestamp = time.strftime("%Y-%m-%d_%H-%M-%S")
                
                # Add error handling and check if file exists before renaming
                if os.path.exists(batch_recovery_file):
                    os.rename(batch_recovery_file, f"batch_wallets_recovery_{timestamp}_recovered.json")
                else:
                    logger.warning(f"Recovery file {batch_recovery_file} not found")
                logger.info("Renamed recovery file to indicate it has been processed")
            except Exception as e:
                logger.error(f"Error renaming recovery file: {e}")
            
            return True
            
        except Exception as e:
            logger.error(f"Error in batch wallet recovery: {e}")
            return False

    def _create_multi_transfer_transaction(self, from_keypair, recipient_pubkeys, amount_per_wallet):
        """
        Create a transaction with multiple transfer instructions.
        
        Args:
            from_keypair (Keypair): The source wallet keypair
            recipient_pubkeys (list): List of recipient public keys
            amount_per_wallet (int): Amount of lamports to send to each wallet
            
        Returns:
            VersionedTransaction: The compiled transaction
        """
        try:
            # Get recent blockhash
            blockhash_response = self.client.get_latest_blockhash()
            blockhash = blockhash_response.value.blockhash
            
            # Create transfer instructions
            transfer_instructions = []
            
            # Add compute budget instructions to accommodate multiple transfers
            compute_unit_price_ix = set_compute_unit_price(get_optimal_compute_unit_price())
            compute_unit_limit_ix = set_compute_unit_limit(2500)  # Increase limit for multiple transfers
            
            transfer_instructions.append(compute_unit_price_ix)
            transfer_instructions.append(compute_unit_limit_ix)
            
            # For multi-signature mode to work, we need to ensure each wallet receives enough SOL
            # Ensure each wallet gets at least 0.001 SOL to cover fees
            min_required = 1000000  # 0.001 SOL in lamports
            if amount_per_wallet < min_required:
                logger.warning(f"Amount per wallet ({amount_per_wallet/1e9} SOL) is below minimum required for multi-sig mode")
                logger.warning(f"Increasing amount to {min_required/1e9} SOL per wallet")
                amount_per_wallet = min_required
            
            # Create individual transfers to handle fees better
            for i, pubkey in enumerate(recipient_pubkeys):
                # Create instructions for each transfer separately for better fee handling
                transfer_ix = transfer(
                    TransferParams(
                        from_pubkey=from_keypair.pubkey(),
                        to_pubkey=pubkey,
                        lamports=amount_per_wallet
                    )
                )
                transfer_instructions.append(transfer_ix)
                logger.info(f"Added transfer instruction for recipient {i}: {pubkey} ({amount_per_wallet/1e9} SOL)")
            
            # Compile the message with all instructions
            try:
                compiled_message = MessageV0.try_compile(
                    from_keypair.pubkey(),
                    transfer_instructions,
                    [],  # Address lookup tables
                    blockhash,
                )
            except Exception as e:
                logger.error(f"Error compiling message: {e}")
                # Try with legacy transaction format if versioned fails
                logger.info("Attempting to create legacy transaction format")
                message = Message.new_with_blockhash(
                    transfer_instructions,
                    from_keypair.pubkey(),
                    blockhash
                )
                transaction = Transaction.populate(message, [from_keypair.to_bytes_array()])
                return transaction
            
            # Create and sign the transaction
            transaction = VersionedTransaction(compiled_message, [from_keypair])
            
            return transaction
            
        except Exception as e:
            logger.error(f"Error creating multi-transfer transaction: {e}")
            raise
    
    def _batch_buy_tokens(self, wallet_index, amount):
        """
        Buy tokens using the specified wallet with a fixed amount.
        
        Args:
            wallet_index (int): Index of the wallet in batch_wallets to use
            amount (float): Amount of SOL to use for buying tokens
            
        Returns:
            bool: True if the buy operation succeeded, False otherwise
        """
        try:
            if wallet_index >= len(self.batch_wallets):
                logger.error(f"Wallet index {wallet_index} is out of range")
                return False
                
            current_wallet = self.batch_wallets[wallet_index]
            
            logger.info(f"Buying tokens with batch wallet {wallet_index}: {current_wallet['address']}")
            
            # Execute the buy transaction using cached pool information
            try:
                # Use maximum allowed slippage (49%) since we're dealing with tiny amounts
                max_slippage = 49.0  # 49% maximum slippage
                logger.info(f"Using maximum slippage of {max_slippage}% for batch buy transaction")
                
                success = buy(
                    private_key=current_wallet['private_key'],
                    pair_address=self.config.POOL_ADDRESS,
                    sol_in=amount,
                    slippage=max_slippage,  # Use 49% slippage instead of default
                    pool_keys=self.pool_keys
                )
                
                if success is True:
                    logger.info(f"Successfully bought tokens with batch wallet {wallet_index}")
                    return True
                else:
                    logger.warning(f"Failed to buy tokens with batch wallet {wallet_index}")
                    return False
                    
            except Exception as e:
                logger.error(f"Error executing buy transaction for batch wallet {wallet_index}: {str(e)}")
                return False
                
        except Exception as e:
            logger.error(f"Error in batch_buy_tokens: {str(e)}")
            return False

    def _batch_multi_sig_buy(self, wallet_index, amount):
        """
        Perform a multi-signature buy where the main wallet creates accounts and
        the batch wallet signs as a co-signer. This mimics the pattern observed
        in the analyzed transaction.
        
        Args:
            wallet_index (int): Index of the batch wallet to use as co-signer
            amount (float): Amount of SOL to use for buying tokens
            
        Returns:
            bool: True if the operation succeeded, False otherwise
        """
        try:
            if wallet_index >= len(self.batch_wallets):
                logger.error(f"Wallet index {wallet_index} is out of range")
                return False
                
            # Get the main wallet and batch wallet
            main_wallet = self.wallets[0]
            batch_wallet = self.batch_wallets[wallet_index]
            
            main_keypair = Keypair.from_base58_string(main_wallet['private_key'])
            batch_keypair = Keypair.from_base58_string(batch_wallet['private_key'])
            
            logger.info(f"Performing multi-signature buy with main wallet and batch wallet {wallet_index}: {batch_wallet['address']}")
            
            # Check balances to ensure sufficient funds
            main_balance = self.client.get_balance(main_keypair.pubkey()).value / 1e9
            logger.info(f"Main wallet balance: {main_balance} SOL")
            
            # Try to fetch batch wallet balance using multiple strategies
            batch_balance = 0.0
            
            # Strategy 1: Check balance with current RPC client
            try:
                balance_lamports = self.client.get_balance(batch_keypair.pubkey()).value
                batch_balance = balance_lamports / 1e9
                logger.info(f"Batch wallet balance: {batch_balance} SOL")
            except Exception as e:
                logger.warning(f"Failed to get balance with primary RPC: {e}")
            
            # Strategy 2: If balance is zero, try with alternative RPCs
            if batch_balance == 0.0 and self.config.ALTERNATIVE_RPCS:
                for alt_rpc in self.config.ALTERNATIVE_RPCS:
                    try:
                        alt_client = Client(alt_rpc)
                        alt_balance = alt_client.get_balance(batch_keypair.pubkey()).value / 1e9
                        logger.info(f"Batch wallet balance from alternative RPC: {alt_balance} SOL")
                        if alt_balance > 0:
                            batch_balance = alt_balance
                            break
                    except Exception as e:
                        logger.warning(f"Failed to get balance with alternative RPC: {e}")
            
            # Strategy 3: If still zero and wallet is marked as funded, trust the flag
            if batch_balance == 0.0 and batch_wallet.get('funded', False):
                logger.info("Wallet appears to have zero balance but is marked as funded - will proceed anyway")
                batch_balance = 0.001  # Assume minimum balance
            
            # Minimum SOL needed for rent exemption and operations
            min_required_main = 0.005  # For rent exemption and fees
            min_required_batch = 0.001  # For fees as co-signer
            
            if main_balance < min_required_main:
                logger.error(f"Main wallet has insufficient funds: {main_balance} SOL (need {min_required_main} SOL)")
                return False
            
            # If batch wallet appears to have no balance but the transaction was confirmed earlier,
            # we will attempt to proceed anyway as this is likely an RPC synchronization issue
            if batch_balance < min_required_batch:
                logger.warning(f"Batch wallet appears to have insufficient funds: {batch_balance} SOL (need {min_required_batch} SOL)")
                logger.warning("Will attempt operation anyway, as this may be due to RPC synchronization issues")
                # Continue execution - don't return False here
            
            # Use cached pool keys
            pool_keys = self.pool_keys
            if pool_keys is None:
                logger.error("No pool keys available")
                return False
                
            # Get the token mint
            mint = (pool_keys.base_mint if pool_keys.base_mint != WSOL else pool_keys.quote_mint)
            
            # Calculate amount in lamports and expected output
            amount_in = int(amount * 1e9)
            
            base_reserve, quote_reserve, token_decimal = get_amm_v4_reserves(pool_keys)
            logger.info(f"Base Reserve: {base_reserve} | Quote Reserve: {quote_reserve} | Token Decimal: {token_decimal}")
            
            amount_out = sol_for_tokens(amount, base_reserve, quote_reserve)
            logger.info(f"Estimated Amount Out: {amount_out}")
            
            # Apply slippage
            # Using maximum allowed slippage (49%) since we're dealing with tiny amounts
            max_slippage = 49.0  # 49% maximum slippage allowed
            logger.info(f"Using maximum slippage of {max_slippage}% for tiny transaction")
            slippage_adjustment = 1 - (max_slippage / 100)
            amount_out_with_slippage = amount_out * slippage_adjustment
            minimum_amount_out = int(amount_out_with_slippage * (10 ** token_decimal))
            
            logger.info(f"Amount In: {amount_in} | Minimum Amount Out: {minimum_amount_out}")
            
            # Check for existing token account for the main wallet
            logger.info("Checking for existing token account...")
            token_account_check = self.client.get_token_accounts_by_owner(main_keypair.pubkey(), TokenAccountOpts(mint), Processed)
            if token_account_check.value:
                token_account = token_account_check.value[0].pubkey
                create_token_account_instruction = None
                logger.info("Token account found.")
            else:
                token_account = get_associated_token_address(main_keypair.pubkey(), mint)
                create_token_account_instruction = create_associated_token_account(main_keypair.pubkey(), main_keypair.pubkey(), mint)
                logger.info("No existing token account found; creating associated token account.")
            
            # Generate seed for WSOL account
            logger.info("Generating seed for WSOL account...")
            seed = base64.urlsafe_b64encode(os.urandom(24)).decode("utf-8")
            wsol_token_account = SoldersPubkey.create_with_seed(main_keypair.pubkey(), seed, TOKEN_PROGRAM_ID)
            
            # Get the minimum rent exemption amount
            balance_needed = Token.get_min_balance_rent_for_exempt_for_account(self.client)
            logger.info(f"Minimum rent exemption amount: {balance_needed/1e9} SOL")
            
            # Create instructions
            logger.info("Creating account instructions...")
            create_wsol_account_instruction = create_account_with_seed(
                CreateAccountWithSeedParams(
                    from_pubkey=main_keypair.pubkey(),
                    to_pubkey=wsol_token_account,
                    base=main_keypair.pubkey(),
                    seed=seed,
                    lamports=int(balance_needed + amount_in),  # Include both rent exemption and swap amount
                    space=ACCOUNT_LAYOUT_LEN,
                    owner=TOKEN_PROGRAM_ID,
                )
            )
            
            init_wsol_account_instruction = initialize_account(
                InitializeAccountParams(
                    program_id=TOKEN_PROGRAM_ID,
                    account=wsol_token_account,
                    mint=WSOL,
                    owner=main_keypair.pubkey(),
                )
            )
            
            # Create swap instruction
            logger.info("Creating swap instruction...")
            swap_instruction = make_amm_v4_swap_instruction(
                amount_in=amount_in,
                minimum_amount_out=minimum_amount_out,
                token_account_in=wsol_token_account,
                token_account_out=token_account,
                accounts=pool_keys,
                owner=main_keypair.pubkey(),  # Main wallet is the owner
            )
            
            # Add a close account instruction to return the rent exemption to the main wallet
            # This matches the CloseAccount instruction in the transaction example
            logger.info("Creating close account instruction...")
            close_wsol_account_instruction = close_account(
                CloseAccountParams(
                    program_id=TOKEN_PROGRAM_ID,
                    account=wsol_token_account,
                    dest=main_keypair.pubkey(),  # Rent exemption returns to main wallet
                    owner=main_keypair.pubkey(),
                )
            )
            
            # Use a small transfer amount for the batch wallet (adjust if wallet has low balance)
            logger.info("Creating transfer instruction from batch wallet to main wallet...")
            transfer_amount = int(0.00099 * 1e9)  # 0.00099 SOL
            
            # If batch wallet balance appears low, use a smaller transfer amount
            if batch_balance < 0.001:
                # Use a tiny amount that should be viable even with low balance
                transfer_amount = int(0.000001 * 1e9)  # 0.000001 SOL (1000 lamports)
                logger.info(f"Using reduced transfer amount due to low balance: {transfer_amount} lamports")
            
            transfer_ix = transfer(
                TransferParams(
                    from_pubkey=batch_keypair.pubkey(),
                    to_pubkey=main_keypair.pubkey(),
                    lamports=transfer_amount
                )
            )
            
            # Assemble all instructions - WITHOUT compute budget instructions
            logger.info("Assembling instructions (without compute budget instructions)...")
            instructions = [
                create_wsol_account_instruction,
                init_wsol_account_instruction,
            ]
            
            if create_token_account_instruction:
                instructions.append(create_token_account_instruction)
                
            instructions.append(swap_instruction)
            instructions.append(close_wsol_account_instruction)  # Include the close account instruction
            instructions.append(transfer_ix)  # Add the transfer as the last instruction
            
            # Get latest blockhash
            blockhash_response = self.client.get_latest_blockhash()
            blockhash = blockhash_response.value.blockhash
            
            # Compile transaction message with both signers
            logger.info("Compiling transaction message...")
            
            # Try different fee payers if needed
            tx_creation_success = False
            transaction = None
            
            # First try: Use batch wallet as fee payer
            try:
                compiled_message = MessageV0.try_compile(
                    batch_keypair.pubkey(),  # Use batch wallet as fee payer
                    instructions,
                    [],
                    blockhash,
                )
                
                # Create and sign transaction with both wallets
                logger.info("Creating transaction with batch wallet as fee payer...")
                transaction = VersionedTransaction(compiled_message, [batch_keypair, main_keypair])
                tx_creation_success = True
            except Exception as e:
                logger.warning(f"Failed to create transaction with batch wallet as fee payer: {e}")
                
            # Second try: Use main wallet as fee payer if first attempt failed
            if not tx_creation_success:
                try:
                    logger.info("Attempting to create transaction with main wallet as fee payer...")
                    compiled_message = MessageV0.try_compile(
                        main_keypair.pubkey(),  # Use main wallet as fee payer
                        instructions,
                        [],
                        blockhash,
                    )
                    
                    # Create and sign transaction with both wallets
                    transaction = VersionedTransaction(compiled_message, [main_keypair, batch_keypair])
                    tx_creation_success = True
                except Exception as e:
                    logger.error(f"Failed to create transaction with main wallet as fee payer: {e}")
                    return False
            
            if not tx_creation_success or transaction is None:
                logger.error("Failed to create transaction with either fee payer")
                return False
                
            # Send transaction
            logger.info("Sending transaction...")
            for attempt in range(self.config.MAX_RETRIES):
                try:
                    txn_sig = self.client.send_transaction(
                        txn=transaction,
                        opts=TxOpts(skip_preflight=False),
                    ).value
                    
                    logger.info(f"Transaction signature: {txn_sig}")
                    break
                except Exception as e:
                    logger.warning(f"Attempt {attempt+1} failed: {e}")
                    
                    # Check for specific error messages
                    error_str = str(e).lower()
                    
                    # If it's a "no recent blockhash" error, try to get a new one
                    if "blockhash" in error_str:
                        logger.info("Getting fresh blockhash and retrying...")
                        blockhash_response = self.client.get_latest_blockhash()
                        blockhash = blockhash_response.value.blockhash
                        
                        # Recompile with new blockhash
                        try:
                            compiled_message = MessageV0.try_compile(
                                batch_keypair.pubkey(),
                                instructions,
                                [],
                                blockhash,
                            )
                            transaction = VersionedTransaction(compiled_message, [batch_keypair, main_keypair])
                        except Exception as compile_err:
                            logger.warning(f"Failed to recompile with new blockhash: {compile_err}")
                    
                    # If we've reached max retries, return failure
                    if attempt == self.config.MAX_RETRIES - 1:
                        logger.error(f"Failed to send transaction after {self.config.MAX_RETRIES} attempts")
                        return False
                        
                    # Wait before retrying
                    time.sleep(self.config.BACKOFF_FACTOR)
            
            # Confirm transaction
            logger.info("Confirming transaction...")
            confirmed = confirm_txn(txn_sig, max_retries=self.config.MAX_RETRIES, retry_interval=self.config.BACKOFF_FACTOR)
            
            if confirmed:
                logger.info(f"Multi-signature transaction for batch wallet {wallet_index} confirmed successfully")
                return True
            else:
                logger.error(f"Failed to confirm transaction for batch wallet {wallet_index}")
                return False
                
        except Exception as e:
            logger.error(f"Error in batch_multi_sig_buy: {str(e)}")
            return False

    def _batch_transfer_tokens_to_main(self):
        """
        Transfer tokens from all batch wallets back to the main wallet.
        
        Returns:
            bool: True if all transfers succeeded, False otherwise
        """
        try:
            if not self.batch_wallets:
                logger.error("No batch wallets found")
                return False
                
            main_wallet = self.wallets[0]
            main_pubkey = SoldersPubkey.from_string(main_wallet['address'])
            
            success_count = 0
            
            # Process each batch wallet
            for i, wallet in enumerate(self.batch_wallets):
                try:
                    logger.info(f"Transferring tokens from batch wallet {i}: {wallet['address']} to main wallet")
                    
                    # Use the simple sell operation to convert tokens back to SOL
                    success = sell(
                        private_key=wallet['private_key'],
                        pair_address=self.config.POOL_ADDRESS,
                        percentage=100,  # Sell 100% of tokens
                        slippage=float(SELL_SLIPPAGE * 100), 
                        pool_keys=self.pool_keys
                    )
                    
                    if success:
                        # Now transfer the SOL back to main wallet
                        from_keypair = Keypair.from_base58_string(wallet['private_key'])
                        
                        # Get the balance
                        balance = self.client.get_balance(from_keypair.pubkey()).value
                        
                        # Calculate transaction fee
                        blockhash_response = self.client.get_latest_blockhash()
                        blockhash = blockhash_response.value.blockhash
                        
                        # Create a dummy transaction to estimate fees
                        dummy_tx = transfer(
                            TransferParams(
                                from_pubkey=from_keypair.pubkey(),
                                to_pubkey=main_pubkey,
                                lamports=1
                            )
                        )
                        
                        dummy_message = MessageV0.try_compile(
                            from_keypair.pubkey(),
                            [dummy_tx],
                            [],
                            blockhash,
                        )
                        
                        # Get fee for message
                        fee_response = self.client.get_fee_for_message(Message.from_bytes(bytes(dummy_message)))
                        tx_fee = fee_response.value or 5000
                        
                        # Transfer amount (leave some for fees)
                        transfer_amount = max(0, balance - tx_fee - 5000)  # Additional 5000 lamports buffer
                        
                        if transfer_amount <= 0:
                            logger.warning(f"Insufficient balance to transfer from batch wallet {i}")
                            continue
                            
                        # Create transfer transaction
                        transfer_ix = transfer(
                            TransferParams(
                                from_pubkey=from_keypair.pubkey(),
                                to_pubkey=main_pubkey,
                                lamports=transfer_amount
                            )
                        )
                        
                        # Get fresh blockhash
                        blockhash_response = self.client.get_latest_blockhash()
                        blockhash = blockhash_response.value.blockhash
                        
                        # Create and sign transaction
                        compiled_message = MessageV0.try_compile(
                            from_keypair.pubkey(),
                            [transfer_ix],
                            [],
                            blockhash,
                        )
                        
                        transaction = VersionedTransaction(compiled_message, [from_keypair])
                        
                        # Send transaction
                        txn_sig = self.client.send_transaction(
                            txn=transaction,
                            opts=TxOpts(skip_preflight=False),
                        ).value
                        
                        # Wait for confirmation
                        confirmed = confirm_txn(txn_sig, max_retries=self.config.MAX_RETRIES, retry_interval=self.config.BACKOFF_FACTOR)
                        
                        if confirmed:
                            logger.info(f"Successfully transferred SOL from batch wallet {i} to main wallet")
                            success_count += 1
                        else:
                            logger.warning(f"Failed to confirm SOL transfer from batch wallet {i}")
                            
                    else:
                        logger.warning(f"Failed to sell tokens from batch wallet {i}")
                        
                except Exception as e:
                    logger.error(f"Error processing batch wallet {i}: {e}")
                    
            return success_count == len(self.batch_wallets)
                
        except Exception as e:
            logger.error(f"Error in batch_transfer_tokens_to_main: {e}")
            return False
            
    def _async_batch_buy(self, wallet_index, buy_amount, use_multi_sig):
        """
        Perform a buy transaction for a single batch wallet asynchronously.
        
        Args:
            wallet_index (int): Index of the batch wallet to use
            buy_amount (float): Amount of SOL to use for buying tokens
            use_multi_sig (bool): Whether to use multi-signature mode
            
        Returns:
            dict: Result dictionary with success status and wallet info
        """
        try:
            if wallet_index >= len(self.batch_wallets):
                logger.error(f"Wallet index {wallet_index} is out of range")
                return {
                    "wallet_index": wallet_index,
                    "address": "unknown",
                    "success": False,
                    "error": "Wallet index out of range"
                }
                
            logger.info(f"Starting async buy for batch wallet {wallet_index} with {buy_amount} SOL")
            
            # Get the wallet info
            batch_wallet = self.batch_wallets[wallet_index]
            
            # Always try to perform the transaction regardless of balance check results
            # This is because balance checks can sometimes fail due to RPC issues
            try:
                # Force wallet to be considered funded - we'll let the transaction attempt decide
                batch_wallet['funded'] = True
                
                # Perform the buy - either using standard method or multi-sig method
                if use_multi_sig:
                    success = self._batch_multi_sig_buy(wallet_index, buy_amount)
                else:
                    success = self._batch_buy_tokens(wallet_index, buy_amount)
                
                result = {
                    "wallet_index": wallet_index,
                    "address": batch_wallet['address'],
                    "success": success,
                    "amount": buy_amount
                }
                
                if success:
                    logger.info(f"Async buy successful for batch wallet {wallet_index}")
                else:
                    logger.warning(f"Async buy failed for batch wallet {wallet_index}")
                    
                return result
                
            except Exception as e:
                logger.error(f"Error in transaction attempt for wallet {wallet_index}: {e}")
                return {
                    "wallet_index": wallet_index,
                    "address": batch_wallet['address'],
                    "success": False,
                    "error": str(e)
                }
            
        except Exception as e:
            logger.error(f"Error in async batch buy for wallet {wallet_index}: {e}")
            return {
                "wallet_index": wallet_index,
                "address": self.batch_wallets[wallet_index]['address'] if wallet_index < len(self.batch_wallets) else "unknown",
                "success": False,
                "error": str(e)
            }

    def batch_mode(self, wallet_count=5, amount_per_wallet=0.001, use_multi_sig=True, swap_amount=None):
        """
        Run in batch mode - create multiple wallets, fund them, perform buys, and return funds.
        
        Args:
            wallet_count (int): Number of wallets to create
            amount_per_wallet (float): Amount of SOL to send to each wallet (default: 0.001)
            use_multi_sig (bool): Whether to use multi-signature mode that mimics the pattern
                                  observed in the analyzed transaction
            swap_amount (float): Optional specific amount to swap in the transaction
                                (defaults to 0.00001 SOL if not specified)
            
        Returns:
            bool: True if the batch operation succeeded, False otherwise
        """
        try:
            logger.info(f"Starting batch mode with {wallet_count} wallets and {amount_per_wallet} SOL per wallet")
            logger.info(f"Multi-signature mode: {use_multi_sig}")
            
            if swap_amount:
                logger.info(f"Using specific swap amount: {swap_amount} SOL")
            
            # Step 1: Check if main wallet has enough balance
            main_wallet = self.wallets[0]
            main_keypair = Keypair.from_base58_string(main_wallet['private_key'])
            
            # Calculate required balance
            total_amount_needed = amount_per_wallet * wallet_count
            buffer = 0.005 * wallet_count  # Add buffer for fees
            
            # If using multi-sig, we need more in the main wallet
            if use_multi_sig:
                # For multi-sig, main wallet needs funds for account creation, token swaps, etc.
                buffer = 0.01 * wallet_count
            
            total_required = total_amount_needed + buffer
            
            balance = self.client.get_balance(main_keypair.pubkey()).value / 1e9  # Convert to SOL
            
            if balance < total_required:
                logger.error(f"Insufficient balance in main wallet. Need at least {total_required} SOL, but have {balance} SOL")
                return False
                
            # Step 2: Generate batch wallets
            batch_wallets = self._generate_batch_wallets(wallet_count)
            
            # Step 3: Create multi-transfer transaction to fund all wallets at once
            try:
                logger.info("Creating multi-transfer transaction to fund batch wallets")
                
                # Convert amount to lamports
                amount_in_lamports = int(amount_per_wallet * 1e9)
                
                # Collect recipient pubkeys
                recipient_pubkeys = [SoldersPubkey.from_string(wallet['address']) for wallet in batch_wallets]
                
                # Create the transaction
                transaction = self._create_multi_transfer_transaction(
                    main_keypair,
                    recipient_pubkeys,
                    amount_in_lamports
                )
                
                # Send the transaction
                logger.info("Sending multi-transfer transaction")
                txn_sig = self.client.send_transaction(
                    txn=transaction,
                    opts=TxOpts(skip_preflight=False),
                ).value
                
                logger.info(f"Multi-transfer transaction sent with signature: {txn_sig}")
                
                # Wait for confirmation
                confirmed = confirm_txn(txn_sig, max_retries=self.config.MAX_RETRIES, retry_interval=self.config.BACKOFF_FACTOR)
                
                if not confirmed:
                    logger.error("Failed to confirm multi-transfer transaction")
                    return False
                
                logger.info("Multi-transfer transaction confirmed! All batch wallets funded successfully")
                
                # Wait longer for RPC nodes to update - some RPCs can take a while to reflect balances
                wait_time = 3  # Wait 8 seconds to ensure balances update
                logger.info(f"Waiting {wait_time} seconds for RPC nodes to update wallet balances...")
                time.sleep(wait_time)
                
                # Verify that wallets were actually funded - use a different RPC if possible to ensure we get updated data
                logger.info("Verifying batch wallet funding...")
                
                # Try to get the transaction details to confirm the funds were actually transferred
                try:
                    txn_details = self.client.get_transaction(
                        txn_sig,
                        commitment="confirmed",
                        max_supported_transaction_version=0
                    )
                    
                    if txn_details and txn_details.value and txn_details.value.meta:
                        logger.info("Transaction details retrieved successfully")
                        
                        # Check if transaction was successful
                        if txn_details.value.meta.err:
                            logger.error(f"Transaction had errors: {txn_details.value.meta.err}")
                        else:
                            logger.info("Transaction appears to have executed successfully")
                            
                            # Check post balances
                            if hasattr(txn_details.value.meta, 'post_balances') and txn_details.value.meta.post_balances:
                                post_balances = txn_details.value.meta.post_balances
                                logger.info(f"Transaction post balances: {post_balances}")
                                
                                # Assume wallets were funded if transaction was successful
                                logger.info("Transaction successful, assuming wallets were funded")
                                
                                # Continue with batch operations
                                for i, wallet in enumerate(batch_wallets):
                                    logger.info(f"Setting batch wallet {i} as funded: {wallet['address']}")
                                    # Flag wallet as funded - we'll still check before using it
                                    wallet['funded'] = True
                                
                                # Success - continue with operations
                                for i, wallet in enumerate(batch_wallets):
                                    # Just verify with our balance check function
                                    balance_lamports, balance_sol = self._check_wallet_balance(wallet['address'])
                                    logger.info(f"Batch wallet {i}: {wallet['address']} has {balance_sol} SOL")
                                
                                # Proceed with operations regardless of balance check
                                logger.info("Proceeding with batch operations based on transaction success")
                                # Skip the underfunding check below
                                unfunded_wallets = 0
                                
                    else:
                        logger.warning("Could not retrieve detailed transaction information")
                        # Continue with the regular balance check
                except Exception as e:
                    logger.error(f"Error getting transaction details: {e}")
                    # Continue with the regular balance check
                
                # Only do this check if we couldn't verify via transaction details
                if 'unfunded_wallets' not in locals():
                    unfunded_wallets = 0
                    for i, wallet in enumerate(batch_wallets):
                        # Try connecting to the wallet directly to check balance
                        try:
                            # Alternate between RPC endpoints for better results
                            if self.config.ALTERNATIVE_RPCS and len(self.config.ALTERNATIVE_RPCS) > 0:
                                alt_rpc = self.config.ALTERNATIVE_RPCS[i % len(self.config.ALTERNATIVE_RPCS)]
                                alt_client = Client(alt_rpc)
                                balance_lamports = alt_client.get_balance(SoldersPubkey.from_string(wallet['address'])).value
                                balance_sol = balance_lamports / 1e9
                            else:
                                balance_lamports, balance_sol = self._check_wallet_balance(wallet['address'])
                                
                            logger.info(f"Batch wallet {i}: {wallet['address']} has {balance_sol} SOL")
                            
                            if balance_lamports < 1000000:  # Less than 0.001 SOL
                                unfunded_wallets += 1
                                logger.warning(f"Batch wallet {i} appears to be underfunded: {balance_sol} SOL")
                                
                                # Try checking once more with a different RPC
                                if self._switch_rpc():
                                    logger.info("Switched RPC, checking balance again...")
                                    balance_lamports, balance_sol = self._check_wallet_balance(wallet['address'])
                                    logger.info(f"Batch wallet {i} after RPC switch: {balance_sol} SOL")
                                    
                                    if balance_lamports >= 1000000:
                                        logger.info(f"Batch wallet {i} is actually funded: {balance_sol} SOL")
                                        unfunded_wallets -= 1
                                        wallet['funded'] = True
                            else:
                                wallet['funded'] = True
                        except Exception as e:
                            logger.error(f"Error checking balance for batch wallet {i}: {e}")
                    
                    if unfunded_wallets > 0:
                        if unfunded_wallets == wallet_count:
                            logger.warning("All batch wallets appear to be underfunded according to RPC balance check.")
                            logger.warning("However, this could be due to RPC synchronization delays.")
                            logger.warning("Will attempt to proceed anyway as the transaction was confirmed.")
                            # Don't return False here, as the transaction was confirmed
                        else:
                            logger.warning(f"{unfunded_wallets}/{wallet_count} batch wallets appear to be underfunded.")
                            logger.warning("Will attempt to use only the funded wallets.")
                
                # Mark the batch process as started - important for recovery
                with open("batch_mode_in_progress.flag", "w") as f:
                    f.write(f"Transaction: {txn_sig}\nTimestamp: {time.strftime('%Y-%m-%d %H:%M:%S')}")
                
            except Exception as e:
                logger.error(f"Error creating or sending multi-transfer transaction: {e}")
                return False
                
            # Step 4: Perform buy transactions with each wallet ASYNCHRONOUSLY
            buy_success_count = 0
            buy_tasks = []
            
            # Prepare tasks for concurrent execution
            for i in range(wallet_count):
                # Determine the amount to use for the buy operation
                if use_multi_sig:
                    # Use the specified swap amount or default to a very small amount
                    buy_amount = swap_amount if swap_amount is not None else 0.00001
                else:
                    # For standard mode, use a slightly randomized amount
                    buy_amount = amount_per_wallet * random.uniform(0.9, 0.99)
                
                logger.info(f"Preparing async buy task for wallet {i} with {buy_amount} SOL")
                buy_tasks.append(i)
            
            # Execute all buy transactions concurrently
            logger.info(f"Starting concurrent execution of {len(buy_tasks)} buy transactions")
            results = []
            
            # Use ThreadPoolExecutor for concurrent execution - limit to 5 concurrent jobs max
            # to avoid overwhelming the RPC but ensure all transactions get processed
            max_workers = min(5, wallet_count)
            logger.info(f"Using {max_workers} concurrent workers for transaction processing")
            
            with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
                # Submit all tasks with clear tracking of each wallet
                futures = []
                for i in buy_tasks:
                    # Calculate buy amount for this specific wallet
                    wallet_buy_amount = (swap_amount if swap_amount is not None else 0.00001) if use_multi_sig else amount_per_wallet * random.uniform(0.9, 0.99)
                    logger.info(f"Submitting transaction task for wallet {i} with {wallet_buy_amount} SOL")
                    
                    # Submit task to executor
                    future = executor.submit(self._async_batch_buy, i, wallet_buy_amount, use_multi_sig)
                    futures.append((future, i, wallet_buy_amount))
                
                # Process results as they complete
                failed_wallet_tasks = []  # Store failed wallets for retry
                for future, wallet_index, wallet_buy_amount in futures:
                    try:
                        # Wait for this specific future to complete
                        result = future.result()
                        results.append(result)
                        
                        if result.get('success', False):
                            buy_success_count += 1
                            logger.info(f"Wallet {wallet_index} transaction completed successfully")
                        else:
                            error_msg = result.get('error', 'Unknown error')
                            logger.warning(f"Wallet {wallet_index} transaction failed: {error_msg}")
                            # Add to failed list for retry
                            failed_wallet_tasks.append((wallet_index, wallet_buy_amount))
                    except Exception as exc:
                        logger.error(f"Wallet {wallet_index} generated an exception: {exc}")
                        results.append({
                            "wallet_index": wallet_index,
                            "address": self.batch_wallets[wallet_index]['address'] if wallet_index < len(self.batch_wallets) else "unknown",
                            "success": False,
                            "error": str(exc)
                        })
                        # Add to failed list for retry
                        failed_wallet_tasks.append((wallet_index, wallet_buy_amount))
            
            # First attempt results
            logger.info(f"Completed first attempt with {buy_success_count}/{wallet_count} successes")
            
            # Retry failed transactions
            if failed_wallet_tasks:
                logger.info(f"Attempting to retry {len(failed_wallet_tasks)} failed transactions")
                
                # Wait a bit before retrying to allow system to stabilize
                retry_wait = 5
                logger.info(f"Waiting {retry_wait} seconds before retrying...")
                time.sleep(retry_wait)
                
                retry_success_count = 0
                for wallet_index, wallet_buy_amount in failed_wallet_tasks:
                    try:
                        logger.info(f"Retrying wallet {wallet_index} with {wallet_buy_amount} SOL")
                        # Direct retry without threading
                        retry_result = self._async_batch_buy(wallet_index, wallet_buy_amount, use_multi_sig)
                        
                        if retry_result.get('success', False):
                            retry_success_count += 1
                            buy_success_count += 1  # Update overall success count
                            logger.info(f"Retry successful for wallet {wallet_index}")
                        else:
                            error_msg = retry_result.get('error', 'Unknown error')
                            logger.warning(f"Retry failed for wallet {wallet_index}: {error_msg}")
                    except Exception as e:
                        logger.error(f"Exception during retry for wallet {wallet_index}: {e}")
                    
                    # Small delay between retries
                    time.sleep(1)
                
                logger.info(f"Retry completed: {retry_success_count}/{len(failed_wallet_tasks)} succeeded")
            
            logger.info(f"Completed {len(results)} concurrent buy operations with {buy_success_count} final successes")
            
            # Step 5: Transfer tokens back to main wallet (only needed if not using multi-sig)
            if not use_multi_sig:
                success = self._batch_transfer_tokens_to_main()
                
                if success:
                    logger.info("Successfully completed batch mode operations")
                else:
                    logger.warning("Batch mode completed with some failures")
            else:
                logger.info("Multi-signature mode complete - tokens already in main wallet")
            
            # Remove the in-progress flag
            try:
                os.remove("batch_mode_in_progress.flag")
            except:
                pass
                
            return True
            
        except Exception as e:
            logger.error(f"Error in batch mode: {e}")
            return False

    def cyclic_batch_mode(self, total_wallet_count=20, wallets_per_cycle=5, amount_per_wallet=0.001, use_multi_sig=True, swap_amount=None, max_cycle_retries=1):
        """
        Run batch mode in cycles, with each cycle handling a limited number of wallets.
        This helps avoid rate limiting and reduces the chance of transaction failures.
        
        Args:
            total_wallet_count (int): Total number of wallets to process
            wallets_per_cycle (int): Maximum number of wallets to process in each cycle (max 10)
            amount_per_wallet (float): Amount of SOL to send to each wallet (default: 0.001)
            use_multi_sig (bool): Whether to use multi-signature mode
            swap_amount (float): Optional specific amount to swap
            max_cycle_retries (int): Maximum number of times to retry a failed cycle
            
        Returns:
            dict: Statistics about the completed cycles and success rate
        """
        # Limit wallets per cycle to a reasonable number to avoid RPC issues
        wallets_per_cycle = min(wallets_per_cycle, 10)
        
        # Calculate how many cycles we need
        cycles_needed = (total_wallet_count + wallets_per_cycle - 1) // wallets_per_cycle
        
        logger.info(f"Starting cyclic batch mode with {total_wallet_count} total wallets")
        logger.info(f"Processing in {cycles_needed} cycles with max {wallets_per_cycle} wallets per cycle")
        logger.info(f"Will retry failed cycles up to {max_cycle_retries} times")
        
        # Statistics to track
        stats = {
            "total_wallets": total_wallet_count,
            "wallets_per_cycle": wallets_per_cycle,
            "cycles_planned": cycles_needed,
            "cycles_completed": 0,
            "cycles_retried": 0,
            "successful_wallets": 0,
            "failed_wallets": 0,
            "cycles": []
        }
        
        # Save a copy of the original batch wallets in case we need to restore them
        original_batch_wallets = self.batch_wallets.copy() if hasattr(self, 'batch_wallets') else []
        
        # Loop through each cycle
        for cycle in range(cycles_needed):
            # Calculate wallets for this cycle
            wallets_remaining = total_wallet_count - (cycle * wallets_per_cycle)
            wallets_this_cycle = min(wallets_per_cycle, wallets_remaining)
            
            logger.info(f"Starting cycle {cycle+1}/{cycles_needed} with {wallets_this_cycle} wallets")
            
            # Attempt the cycle with retries if it fails
            cycle_attempt = 0
            cycle_success = False
            while not cycle_success and cycle_attempt <= max_cycle_retries:
                if cycle_attempt > 0:
                    logger.info(f"Retry attempt {cycle_attempt} for cycle {cycle+1}")
                    # Wait longer between retry attempts
                    retry_wait = 15 + (cycle_attempt * 5)  # Increasing wait time for subsequent retries
                    logger.info(f"Waiting {retry_wait} seconds before retry...")
                    time.sleep(retry_wait)
                
                # Clear batch wallets before each attempt
                self.batch_wallets = []
                
                # Run the batch mode for this cycle
                cycle_start_time = time.time()
                cycle_success = self.batch_mode(
                    wallet_count=wallets_this_cycle,
                    amount_per_wallet=amount_per_wallet,
                    use_multi_sig=use_multi_sig,
                    swap_amount=swap_amount
                )
                cycle_end_time = time.time()
                
                if cycle_success:
                    logger.info(f"Cycle {cycle+1} completed successfully on attempt {cycle_attempt+1}")
                    if cycle_attempt > 0:
                        stats["cycles_retried"] += 1
                else:
                    logger.error(f"Cycle {cycle+1} failed on attempt {cycle_attempt+1}")
                    cycle_attempt += 1
            
            # Record cycle statistics
            cycle_stats = {
                "cycle_num": cycle + 1,
                "wallets_processed": wallets_this_cycle,
                "success": cycle_success,
                "attempts": cycle_attempt + 1,
                "duration_seconds": round(cycle_end_time - cycle_start_time, 2)
            }
            
            stats["cycles"].append(cycle_stats)
            
            if cycle_success:
                stats["cycles_completed"] += 1
                stats["successful_wallets"] += wallets_this_cycle
                logger.info(f"Cycle {cycle+1} completed successfully after {cycle_attempt+1} attempts")
            else:
                stats["failed_wallets"] += wallets_this_cycle
                logger.error(f"Cycle {cycle+1} failed after {max_cycle_retries+1} attempts")
            
            # Wait between cycles to avoid rate limiting
            if cycle < cycles_needed - 1:  # Don't wait after the last cycle
                wait_time = random.uniform(10, 15)
                logger.info(f"Waiting {wait_time:.2f} seconds before starting next cycle...")
                time.sleep(wait_time)
        
        # Calculate success rate
        stats["success_rate"] = f"{(stats['successful_wallets'] / total_wallet_count) * 100:.2f}%" if total_wallet_count > 0 else "0%"
        stats["retry_rate"] = f"{(stats['cycles_retried'] / stats['cycles_completed']) * 100:.2f}%" if stats['cycles_completed'] > 0 else "0%"
        
        # Log summary
        logger.info(f"Cyclic batch mode completed: {stats['cycles_completed']}/{cycles_needed} cycles successful")
        logger.info(f"Processed {stats['successful_wallets']}/{total_wallet_count} wallets successfully ({stats['success_rate']})")
        logger.info(f"Needed to retry {stats['cycles_retried']} cycles ({stats['retry_rate']})")
        
        # Restore original batch wallets
        self.batch_wallets = original_batch_wallets
        
        return stats

    def infinite_batch_mode(self, wallets_per_cycle=5, amount_per_wallet=0.001, use_multi_sig=True, swap_amount=None, 
                        max_cycle_retries=1, max_runtime_hours=None, target_tx_count=None, pause_between_cycles=3):
        """
        Run batch mode in an infinite loop until stopped by the user, max runtime reached, or target transactions completed.
        
        Args:
            wallets_per_cycle (int): Number of wallets to process in each cycle (max 10)
            amount_per_wallet (float): Amount of SOL to send to each wallet (default: 0.001)
            use_multi_sig (bool): Whether to use multi-signature mode
            swap_amount (float): Optional specific amount to swap
            max_cycle_retries (int): Maximum number of times to retry a failed cycle
            max_runtime_hours (float): Optional maximum runtime in hours
            target_tx_count (int): Optional target number of successful transactions to reach
            pause_between_cycles (int): Seconds to pause between cycles (default: 15)
            
        Returns:
            dict: Statistics about all cycles run
        """
        # Limit wallets per cycle to a reasonable number to avoid RPC issues
        wallets_per_cycle = min(wallets_per_cycle, 10)
        
        logger.info(f"Starting INFINITE batch mode with {wallets_per_cycle} wallets per cycle")
        logger.info(f"Will retry failed cycles up to {max_cycle_retries} times")
        
        if max_runtime_hours:
            end_time = time.time() + (max_runtime_hours * 3600)
            logger.info(f"Will run for maximum of {max_runtime_hours} hours (until {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(end_time))})")
        else:
            end_time = None
            
        if target_tx_count:
            logger.info(f"Will run until {target_tx_count} successful transactions are completed")
        
        # Statistics to track
        stats = {
            "wallets_per_cycle": wallets_per_cycle,
            "cycles_completed": 0,
            "cycles_attempted": 0,
            "cycles_failed": 0,
            "cycles_retried": 0,
            "successful_transactions": 0,
            "failed_transactions": 0,
            "start_time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "cycles": []
        }
        
        try:
            # Create a flag file to indicate the infinite mode is running
            with open("infinite_mode_running.flag", "w") as f:
                f.write(f"Started: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
                f.write(f"Wallets per cycle: {wallets_per_cycle}\n")
                if max_runtime_hours:
                    f.write(f"Max runtime: {max_runtime_hours} hours\n")
                if target_tx_count:
                    f.write(f"Target tx count: {target_tx_count}\n")
            
            cycle_num = 0
            
            # Start infinite loop
            while True:
                # Check if we should stop based on runtime
                if end_time and time.time() > end_time:
                    logger.info(f"Maximum runtime of {max_runtime_hours} hours reached. Stopping.")
                    break
                    
                # Check if we've reached target transaction count
                if target_tx_count and stats["successful_transactions"] >= target_tx_count:
                    logger.info(f"Target of {target_tx_count} successful transactions reached. Stopping.")
                    break
                
                # Starting a new cycle
                cycle_num += 1
                cycle_start_time = time.time()
                stats["cycles_attempted"] += 1
                
                logger.info(f"Starting cycle {cycle_num} with {wallets_per_cycle} wallets")
                logger.info(f"Current stats: {stats['successful_transactions']} successful transactions, {stats['cycles_completed']} cycles completed")
                
                # Update the status file
                with open("infinite_mode_running.flag", "w") as f:
                    f.write(f"Started: {stats['start_time']}\n")
                    f.write(f"Current cycle: {cycle_num}\n")
                    f.write(f"Completed cycles: {stats['cycles_completed']}\n")
                    f.write(f"Successful transactions: {stats['successful_transactions']}\n")
                    f.write(f"Last update: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
                
                # Attempt the cycle with retries if it fails
                cycle_attempt = 0
                cycle_success = False
                while not cycle_success and cycle_attempt <= max_cycle_retries:
                    if cycle_attempt > 0:
                        logger.info(f"Retry attempt {cycle_attempt} for cycle {cycle_num}")
                        # Wait longer between retry attempts
                        retry_wait = 15 + (cycle_attempt * 5)  # Increasing wait time for subsequent retries
                        logger.info(f"Waiting {retry_wait} seconds before retry...")
                        time.sleep(retry_wait)
                    
                    # Clear batch wallets before each attempt
                    self.batch_wallets = []
                    
                    # Run the batch mode for this cycle
                    try:
                        cycle_success = self.batch_mode(
                            wallet_count=wallets_per_cycle,
                            amount_per_wallet=amount_per_wallet,
                            use_multi_sig=use_multi_sig,
                            swap_amount=swap_amount
                        )
                    except Exception as e:
                        logger.error(f"Error in cycle {cycle_num}: {e}")
                        cycle_success = False
                    
                    if cycle_success:
                        logger.info(f"Cycle {cycle_num} completed successfully on attempt {cycle_attempt+1}")
                        if cycle_attempt > 0:
                            stats["cycles_retried"] += 1
                    else:
                        logger.error(f"Cycle {cycle_num} failed on attempt {cycle_attempt+1}")
                        cycle_attempt += 1
                
                # Record cycle statistics
                cycle_end_time = time.time()
                cycle_stats = {
                    "cycle_num": cycle_num,
                    "wallets_processed": wallets_per_cycle,
                    "success": cycle_success,
                    "attempts": cycle_attempt + 1,
                    "duration_seconds": round(cycle_end_time - cycle_start_time, 2),
                    "timestamp": time.strftime("%Y-%m-%d %H:%M:%S")
                }
                
                stats["cycles"].append(cycle_stats)
                
                if cycle_success:
                    stats["cycles_completed"] += 1
                    stats["successful_transactions"] += wallets_per_cycle
                    logger.info(f"Cycle {cycle_num} added {wallets_per_cycle} successful transactions")
                else:
                    stats["cycles_failed"] += 1
                    stats["failed_transactions"] += wallets_per_cycle
                    logger.error(f"Cycle {cycle_num} failed after {max_cycle_retries+1} attempts")
                
                # Calculate and log current transaction rate
                elapsed_time = time.time() - time.mktime(time.strptime(stats["start_time"], "%Y-%m-%d %H:%M:%S"))
                tx_per_hour = (stats["successful_transactions"] / elapsed_time) * 3600 if elapsed_time > 0 else 0
                logger.info(f"Current transaction rate: {tx_per_hour:.2f} transactions per hour")
                
                # Wait between cycles
                random_wait = random.uniform(pause_between_cycles * 0.8, pause_between_cycles * 1.2)
                logger.info(f"Waiting {random_wait:.2f} seconds before next cycle...")
                time.sleep(random_wait)
                
                # Check if stop flag exists
                if os.path.exists("stop_infinite_mode.flag"):
                    logger.info("Stop flag detected. Gracefully stopping infinite mode.")
                    try:
                        os.remove("stop_infinite_mode.flag")
                    except:
                        pass
                    break
            
            # Calculate final statistics
            stats["end_time"] = time.strftime("%Y-%m-%d %H:%M:%S")
            elapsed_seconds = time.mktime(time.strptime(stats["end_time"], "%Y-%m-%d %H:%M:%S")) - time.mktime(time.strptime(stats["start_time"], "%Y-%m-%d %H:%M:%S"))
            stats["runtime_hours"] = round(elapsed_seconds / 3600, 2)
            stats["tx_per_hour"] = round((stats["successful_transactions"] / elapsed_seconds) * 3600 if elapsed_seconds > 0 else 0, 2)
            stats["success_rate"] = f"{(stats['successful_transactions'] / (stats['successful_transactions'] + stats['failed_transactions'])) * 100:.2f}%" if (stats['successful_transactions'] + stats['failed_transactions']) > 0 else "0%"
            
            # Log summary
            logger.info(f"Infinite batch mode completed after {stats['runtime_hours']} hours")
            logger.info(f"Completed {stats['cycles_completed']}/{stats['cycles_attempted']} cycles successfully")
            logger.info(f"Processed {stats['successful_transactions']} successful transactions ({stats['tx_per_hour']} tx/hour)")
            logger.info(f"Overall success rate: {stats['success_rate']}")
            
            # Remove running flag
            try:
                os.remove("infinite_mode_running.flag")
            except:
                pass
            
            # Create a results summary file
            with open(f"infinite_mode_results_{time.strftime('%Y%m%d_%H%M%S')}.json", "w") as f:
                json.dump(stats, f, indent=4)
            
            return stats
            
        except KeyboardInterrupt:
            logger.info("Keyboard interrupt detected. Gracefully stopping infinite mode.")
            # Calculate final statistics even on interrupt
            stats["end_time"] = time.strftime("%Y-%m-%d %H:%M:%S")
            elapsed_seconds = time.mktime(time.strptime(stats["end_time"], "%Y-%m-%d %H:%M:%S")) - time.mktime(time.strptime(stats["start_time"], "%Y-%m-%d %H:%M:%S"))
            stats["runtime_hours"] = round(elapsed_seconds / 3600, 2)
            stats["tx_per_hour"] = round((stats["successful_transactions"] / elapsed_seconds) * 3600 if elapsed_seconds > 0 else 0, 2)
            
            # Log summary
            logger.info(f"Infinite batch mode interrupted after {stats['runtime_hours']} hours")
            logger.info(f"Completed {stats['cycles_completed']}/{stats['cycles_attempted']} cycles successfully")
            logger.info(f"Processed {stats['successful_transactions']} successful transactions ({stats['tx_per_hour']} tx/hour)")
            
            # Remove running flag
            try:
                os.remove("infinite_mode_running.flag")
            except:
                pass
            
            # Create a results summary file
            with open(f"infinite_mode_results_{time.strftime('%Y%m%d_%H%M%S')}.json", "w") as f:
                json.dump(stats, f, indent=4)
            
            return stats
        
        except Exception as e:
            logger.error(f"Error in infinite batch mode: {e}")
            # Remove running flag
            try:
                os.remove("infinite_mode_running.flag")
            except:
                pass
            raise
