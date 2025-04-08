import json
from pathlib import Path
from solana.rpc.api import Client
from solders.keypair import Keypair #type: ignore

def load_config():
    """
    Load configuration from config.json file
    Returns:
        dict: Configuration dictionary
    Raises:
        FileNotFoundError: If config.json is not found
        json.JSONDecodeError: If config.json is not valid JSON
    """
    try:
        config_path = Path(__file__).parent / "config.json"
        with open(config_path, "r") as f:
            config = json.load(f)
        return config
    except FileNotFoundError:
        raise FileNotFoundError("config.json not found in the project directory")
    except json.JSONDecodeError:
        raise json.JSONDecodeError("Invalid JSON format in config.json")

def get_solana_config():
    """
    Get Solana specific configuration from the chains section
    Returns:
        dict: Solana configuration dictionary
    Raises:
        KeyError: If chains or Solana configuration is not found in config.json
    """
    config = load_config()
    if "chains" not in config:
        raise KeyError("chains configuration not found in config.json")
    
    chains = config["chains"]
    if "solana" not in chains:
        raise KeyError("Solana configuration not found in chains section of config.json")
    
    return chains["solana"]

def get_first_wallet_key():
    """
    Get the first private key from solana_wallets array
    Returns:
        str: The first private key
    Raises:
        KeyError: If solana_wallets is not found or empty
    """
    config = load_config()
    if "solana_wallets" not in config:
        raise KeyError("solana_wallets not found in config.json")
    
    wallets = config["solana_wallets"]
    if not wallets:
        raise KeyError("solana_wallets array is empty")
    
    return wallets[0]["private_key"]

# Load configuration
solana_config = get_solana_config()

# Chain configuration
CHAIN_NAME = solana_config["name"]
RPC_URL = solana_config["rpc_url"]
ALTERNATIVE_RPCS = solana_config["alternative_rpcs"]
DEX_TYPE = solana_config["dex_type"]

# DEX configuration
dex_config = solana_config["dex"]
POOL_ADDRESS = dex_config["pool_address"]
UNIT_BUDGET = dex_config["unit_budget"]
UNIT_PRICE = dex_config["unit_price"]

# Transaction configuration
tx_config = solana_config["transaction"]
MIN_BUY_AMOUNT = tx_config["min_buy_amount"]
MAX_BUY_AMOUNT = tx_config["max_buy_amount"]
TRANSFER_PERCENTAGE = tx_config["transfer_percentage"]
WAIT_TIME = tx_config["wait_time"]
TRADE_WAIT_TIME = tx_config["trade_wait_time"]
MAX_RETRIES = tx_config["max_retries"]
BACKOFF_FACTOR = tx_config["backoff_factor"]
MIN_BALANCE_THRESHOLD = tx_config["min_balance_threshold"]

# Slippage settings with default of 0.5%
BUY_SLIPPAGE = tx_config.get("buy_slippage", 0.005)
SELL_SLIPPAGE = tx_config.get("sell_slippage", 0.005)

SOL_DECIMAL = 9

# Initialize Solana client with the primary RPC URL
client = Client(RPC_URL)

# Load private key from config.json
try:
    PRIV_KEY = get_first_wallet_key()
    payer_keypair = Keypair.from_base58_string(PRIV_KEY)
except KeyError as e:
    raise KeyError(f"Failed to load wallet key: {str(e)}")
