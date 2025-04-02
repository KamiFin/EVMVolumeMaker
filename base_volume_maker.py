import json
import time
import logging
import os
import signal
import sys
from enum import Enum
from abc import ABC, abstractmethod

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

class CycleResult(Enum):
    CONTINUE = True   # Continue to next cycle
    STOP = False     # Stop and mark wallet as failed

class BaseVolumeMaker(ABC):
    """Base class for volume makers with common functionality"""
    
    def __init__(self, chain_name, mode='buy', single_wallet=False):
        """
        Initialize volume maker for a specific chain
        
        Args:
            chain_name (str): Name of the chain to operate on
            mode (str): Operation mode ('buy', 'sell', or 'trade')
            single_wallet (bool): If True, only use the first wallet without creating new ones
        """
        self.chain_name = chain_name
        self.mode = mode  # 'buy' or 'sell' or 'trade'
        self.single_wallet = single_wallet
        
        # Load configuration
        try:
            self.config = self._load_config(chain_name)
        except ValueError as e:
            logger.error(f"Configuration error: {e}")
            sys.exit(1)
        
        self.wallets = []
        self.index = 0
        self.failed_wallets = set()  # Track failed wallets
        self.should_stop = False
        
        # Load existing wallets if available
        self._load_wallets()
        
        # Create initial wallet if none exists
        if not self.wallets:
            self._initialize()
        
        # Register signal handlers for graceful shutdown
        signal.signal(signal.SIGINT, lambda s, f: self._signal_handler(s, f))
        signal.signal(signal.SIGTERM, lambda s, f: self._signal_handler(s, f))

    @abstractmethod
    def _load_config(self, chain_name):
        """Load configuration for the specific chain"""
        pass

    @abstractmethod
    def _get_connection(self):
        """Get connection to the blockchain"""
        pass

    @abstractmethod
    def _check_wallet_balance(self, address):
        """Check the balance of a wallet"""
        pass

    @abstractmethod
    def _generate_wallet(self):
        """Generate a new wallet"""
        pass

    @abstractmethod
    def buy_tokens(self):
        """Buy tokens using the current wallet"""
        pass

    @abstractmethod
    def sell_tokens(self):
        """Sell tokens using the current wallet"""
        pass

    @abstractmethod
    def transfer_funds(self, from_index, to_index):
        """Transfer funds between wallets"""
        pass

    @abstractmethod
    def _load_wallets(self):
        """Load wallets from configuration file. Must be implemented by derived classes."""
        pass

    @abstractmethod
    def _save_wallets(self):
        """Save wallets to configuration file. Must be implemented by derived classes."""
        pass

    def _initialize(self):
        """Create the first wallet to start the process."""
        self._generate_wallet()
        logger.info(f"Initialized with wallet: {self.wallets[0]['address']}")

    def _increment_index(self):
        """Increment wallet index or reset to 0 if using single wallet mode"""
        if self.single_wallet:
            self.index = 0  # Always stay on first wallet
        else:
            self.index = (self.index + 1) % len(self.wallets)

    def _find_wallet_with_balance(self):
        """Find a wallet with sufficient balance in the wallet list."""
        for idx, wallet in enumerate(self.wallets):
            balance, balance_eth = self._check_wallet_balance(wallet['address'])
            if balance > self.config.MIN_BALANCE_THRESHOLD:
                logger.info(f"Found wallet with sufficient balance: {wallet['address']} ({balance_eth} {self.config.NATIVE_TOKEN})")
                return idx
        return -1  # No wallet with sufficient balance found

    def mark_wallet_failed(self, wallet_address):
        """Mark a wallet as failed without removing it."""
        self.failed_wallets.add(wallet_address)
        logger.warning(f"Marked wallet {wallet_address} as failed - will skip in future cycles")
        
    def is_wallet_failed(self, wallet_address):
        """Check if a wallet is marked as failed."""
        return wallet_address in self.failed_wallets

    def _signal_handler(self, signum, frame):
        """Handle program interruption by saving wallets before exit."""
        logger.info("Received interrupt signal. Saving wallets before exit...")
        self._save_wallets()
        sys.exit(0)

    @abstractmethod
    def start_cycle(self):
        """Start a volume making cycle with improved safety measures."""
        pass

    @abstractmethod
    def run(self):
        """Main volume making cycle with improved error handling"""
        pass 