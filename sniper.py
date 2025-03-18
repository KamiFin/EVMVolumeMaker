import argparse
import json
import logging
from web3 import Web3
from hexbytes import HexBytes
import time
import asyncio
import random

# Configure logging
logger = logging.getLogger(__name__)

class Config:
    def __init__(self, chain_name):
        """Initialize configuration for specified chain"""
        with open('config.json', 'r') as f:
            config = json.load(f)
            
        if chain_name not in config['chains']:
            raise ValueError(f"Chain '{chain_name}' not found in config.json")
            
        chain_config = config['chains'][chain_name]
        
        self.rpc_url = chain_config['rpc_url']
        self.chain_id = chain_config['chain_id']
        self.router_address = chain_config['dex']['router_address']
        self.router_abi = chain_config['dex']['router_abi']
        self.wrapped_native_token = chain_config['dex']['wrapped_native_token']
        self.token_contract = next(iter(chain_config['token'].values()))['contract_address']

# Global variables that will be initialized based on config
config = None
web3 = None
contract = None
eth = None  # Will hold wrapped native token address
uniSwap = None  # Will hold router address
rpc = None # Will hold rpc url

def init_globals(chain_name):
    """Initialize global variables based on chain configuration"""
    global config, web3, contract, eth, uniSwap, rpc
    
    try:
        config = Config(chain_name)
        rpc = config.rpc_url
        web3 = Web3(Web3.HTTPProvider(config.rpc_url))
        
        if not web3.is_connected():
            raise ConnectionError(f"Failed to connect to RPC: {config.rpc_url}")
            
        uniSwap = config.router_address
        eth = config.wrapped_native_token
        
        # Initialize the contract with the router address and ABI
        contract = web3.eth.contract(
            address=web3.to_checksum_address(uniSwap), 
            abi=config.router_abi
        )
        
        logger.info(f"Initialized for chain: {chain_name}")
        logger.info(f"Connected to RPC: {config.rpc_url}")
        logger.info(f"Router address: {uniSwap}")
        logger.info(f"Wrapped token: {eth}")
        
        return True
        
    except Exception as e:
        logger.error(f"Error initializing globals: {e}")
        raise

# Token ABI remains unchanged
tokenAbi = [
    {"constant":True,"inputs":[],"name":"decimals","outputs":[{"name":"","type":"uint8"}],"payable":False,"stateMutability":"view","type":"function"},
    {"inputs":[{"internalType":"address","name":"spender","type":"address"},{"internalType":"uint256","name":"amount","type":"uint256"}],"name":"approve","outputs":[{"internalType":"bool","name":"","type":"bool"}],"stateMutability":"nonpayable","type":"function"},
    {"inputs":[{"internalType":"address","name":"account","type":"address"}],"name":"balanceOf","outputs":[{"internalType":"uint256","name":"","type":"uint256"}],"stateMutability":"view","type":"function"}
]

tempHashes = []

def ExactTokensSwap(_ethAmount, _amountOut, _tokenContract, _autoDecimals, _sender, _pk, _gas):
    """
    Swap exact ETH for tokens with specified output amount
    
    Args:
        _ethAmount: Amount of ETH to spend
        _amountOut: Minimum amount of tokens to receive
        _tokenContract: Token contract address
        _autoDecimals: Whether to automatically adjust decimals
        _sender: Sender wallet address
        _pk: Private key
        _gas: Gas price in Gwei
    """
    logger.info(f"Starting ExactTokensSwap: {_ethAmount} ETH for token {_tokenContract}")
    
    try:
        nonce = web3.eth.get_transaction_count(_sender)
        _tokenContract = web3.to_checksum_address(_tokenContract)
        
        if _autoDecimals:
            Tkcontract = web3.eth.contract(address=_tokenContract, abi=tokenAbi)
            decimals = Tkcontract.functions.decimals().call()
            logger.info(f"Token decimals: {decimals}")
            _amountOut = _amountOut.ljust(decimals + len(_amountOut), '0')
            logger.info(f"Adjusted amount out: {_amountOut}")
            
            tx = contract.functions.swapETHForExactTokens(
                int(_amountOut),
                [eth, _tokenContract],
                _sender,
                (int(time.time()) + 10000)
            ).build_transaction({
                'from': _sender,
                'value': web3.to_wei(float(_ethAmount), 'ether'),
                'gas': 1000000,
                'gasPrice': web3.to_wei(_gas, 'gwei'),
                'nonce': nonce,
            })
        else:
            tx = contract.functions.swapETHForExactTokens(
                int(_amountOut),
                [eth, _tokenContract],
                _sender,
                (int(time.time()) + 10000)
            ).build_transaction({
                'from': _sender,
                'value': web3.to_wei(float(_ethAmount), 'ether'),
                'gas': 1000000,
                'gasPrice': web3.to_wei(_gas, 'gwei'),
                'nonce': nonce,
            })
        
        logger.info("Signing transaction...")
        signed_txn = web3.eth.account.sign_transaction(tx, private_key=_pk)
        
        logger.info("Sending transaction...")
        tx_token = web3.eth.send_raw_transaction(signed_txn.rawTransaction)
        
        logger.info(f"Transaction sent: {tx_token.hex()}")
        receipt = web3.eth.wait_for_transaction_receipt(tx_token)
        
        if receipt['status'] == 1:
            logger.info("CONFIRMED: Transaction successful")
            return True
        else:
            logger.error(f"Transaction failed with status: {receipt['status']}")
            return False
    except Exception as e:
        logger.error(f"Error in ExactTokensSwap: {e}")
        raise

def ExactETHSwap(_ethAmount, _tokenContract, _sender, _pk, _gas, max_retries=3):
    """
    Swap exact ETH for tokens using DEX router
    """
    for attempt in range(1, max_retries + 1):
        try:
            logger.info(f"Starting ExactETHSwap: {_ethAmount} ETH for token {_tokenContract}")
            
            # Get current nonce - fetch fresh each time
            nonce = web3.eth.get_transaction_count(_sender)
            logger.info(f"Using nonce: {nonce}")
            
            _tokenContract = web3.to_checksum_address(_tokenContract)
            
            # Check sender balance
            sender_balance = web3.eth.get_balance(_sender)
            logger.info(f"Sender balance: {web3.from_wei(sender_balance, 'ether')} ETH")
            
            # Check if there's enough balance
            if sender_balance < web3.to_wei(float(_ethAmount), 'ether'):
                logger.error(f"Insufficient balance: {web3.from_wei(sender_balance, 'ether')} ETH, needed: {_ethAmount} ETH")
                return False
            
            # Create the route structure that DEX expects
            routes = [{"from": eth, "to": _tokenContract, "stable": False}]
            
            # Build transaction using the correct function and route structure
            tx = contract.functions.swapExactETHForTokens(
                0,  # amountOutMin - accept any amount of tokens
                routes,
                _sender,
                (int(time.time()) + 10000)
            ).build_transaction({
                'from': _sender,
                'value': web3.to_wei(float(_ethAmount), 'ether'),
                'gas': 1000000,
                'gasPrice': web3.to_wei(_gas, 'gwei'),
                'nonce': nonce,
                'chainId': config.chain_id  # Use chain ID from config
            })
            
            logger.info(f"Transaction built: {tx}")
            logger.info("Signing transaction...")
            signed_txn = web3.eth.account.sign_transaction(tx, private_key=_pk)
            
            logger.info("Sending transaction...")
            tx_token = web3.eth.send_raw_transaction(signed_txn.rawTransaction)
            
            logger.info(f"Transaction sent: {tx_token.hex()}")
            receipt = web3.eth.wait_for_transaction_receipt(tx_token, timeout=60)
            
            if receipt['status'] == 1:
                logger.info("CONFIRMED: Transaction successful")
                return True
            else:
                logger.error(f"Transaction failed with status: {receipt['status']}")
                # Check transaction details on explorer
                logger.info(f"Check transaction on explorer: https://sonicscan.org/tx/{tx_token.hex()}")
                
                if attempt < max_retries:
                    logger.warning(f"Buy transaction failed on attempt {attempt}")
                    time.sleep(2 * attempt)  # Exponential backoff
                else:
                    logger.error(f"All {max_retries} buy attempts failed")
                    return False
                    
        except Exception as e:
            logger.error(f"Error in ExactETHSwap: {e}")
            if attempt < max_retries:
                logger.warning(f"Buy attempt {attempt} failed: {e}. Retrying in {2 * attempt} seconds...")
                time.sleep(2 * attempt)
            else:
                logger.error(f"All {max_retries} buy attempts failed")
                return False
    
    return False

def checkBalance(_tokenContract, _sender):
    """Check token balance for a wallet"""
    _tokenContract = web3.to_checksum_address(_tokenContract)
    Tkcontract = web3.eth.contract(address=_tokenContract, abi=tokenAbi)
    balance = Tkcontract.functions.balanceOf(_sender).call()
    
    if balance != 0:
        return True
    return False

def getProfit(_tokenContract, _sender):
    """Get token balance and potential ETH value if sold"""
    _tokenContract = web3.to_checksum_address(_tokenContract)
    Tkcontract = web3.eth.contract(address=_tokenContract, abi=tokenAbi)
    balance = Tkcontract.functions.balanceOf(_sender).call()
    decimals = Tkcontract.functions.decimals().call()
    
    values = []  # 0 is token balance, 1 is eth conversion if sell
    
    try:
        profit = contract.functions.getAmountsOut(balance, [_tokenContract, eth]).call()
        values.append(int(str(balance)[:-decimals]))
        values.append(round(web3.from_wei(profit[1], 'ether'), 2))
        return values
    except Exception as e:
        logger.error(f"Error getting profit: {e}")
        return None

def sellTokens(_tokenContract, _sender, _pk, _gas, percentage=1):
    """Sell tokens for ETH"""
    try:
        logger.info(f"Selling tokens from {_sender}")
        
        nonce = web3.eth.get_transaction_count(_sender)
        # Token Balance
        _tokenContract = web3.to_checksum_address(_tokenContract)
        Tkcontract = web3.eth.contract(address=_tokenContract, abi=tokenAbi)
        balance = Tkcontract.functions.balanceOf(_sender).call()
        # Get Decimals
        decimals = Tkcontract.functions.decimals().call()
        
        logger.info(f"Token balance: {balance}")
        
        # Create the route structure that Sonic expects
        routes = [{"from": _tokenContract, "to": eth, "stable": False}]
        
        tx = contract.functions.swapExactTokensForETHSupportingFeeOnTransferTokens(
            int(balance / percentage),
            0,
            routes,
            _sender,
            (int(time.time()) + 10000)
        ).build_transaction({
            'from': _sender,
            'value': 0,
            'gas': 1000000,
            'gasPrice': web3.to_wei(_gas, 'gwei'),
            'nonce': nonce,
        })
        
        # Use getAmountsOut with the correct route structure
        profit = contract.functions.getAmountsOut(balance, routes).call()
        logger.info(f"Token amount: {str(balance)[:-decimals]}")
        logger.info(f"Expected ETH: {round(web3.from_wei(profit[1], 'ether'), 2)}")
        
        signed_txn = web3.eth.account.sign_transaction(tx, private_key=_pk)
        tx_token = web3.eth.send_raw_transaction(signed_txn.rawTransaction)
        receipt = web3.eth.wait_for_transaction_receipt(tx_token)
        
        if receipt['status'] == 1:
            logger.info("CONFIRMED: Sell transaction successful")
            return True
        else:
            logger.error(f"Sell transaction failed with status: {receipt['status']}")
            return False
    except Exception as e:
        logger.error(f"Error selling tokens: {e}")
        return False

def approveToken(_tokenContract, _sender, _pk):
    """Approve token for trading on DEX"""
    try:
        nonce = web3.eth.get_transaction_count(_sender)
        _tokenContract = web3.to_checksum_address(_tokenContract)
        Tkcontract = web3.eth.contract(address=_tokenContract, abi=tokenAbi)
        
        # Use the correct router address for approval
        tx = Tkcontract.functions.approve(
            uniSwap,  # Use the router address defined at the top
            115792089237316195423570985008687907853269984665640564039457584007913129639935,  # Max uint256
        ).build_transaction({
            'from': _sender,
            'value': 0,
            'nonce': nonce,
        })
        
        logger.info("Approving token for trading...")
        signed_txn = web3.eth.account.sign_transaction(tx, private_key=_pk)
        tx_token = web3.eth.send_raw_transaction(signed_txn.rawTransaction)
        receipt = web3.eth.wait_for_transaction_receipt(tx_token)
        
        if receipt['status'] == 1:
            logger.info("CONFIRMED: Token approval successful")
            return True
        else:
            logger.error(f"Token approval failed with status: {receipt['status']}")
            return False
    except Exception as e:
        logger.error(f"Error approving token: {e}")
        return False

async def scannerPending():
    while True:
        tx_list_raw = web3.eth.getBlock(block_identifier='pending', full_transactions=True).transactions
        for x in tx_list_raw:
            if any(ele in x['input'] for ele in openTrade) and tokenToBuy in x['input']:
                if x['hash'].hex() not in tempHashes:
                    tempHashes.append(x['hash'].hex())

def check_pair_exists(_tokenContract):
    """Check if a token pair exists on the DEX using specific function signature"""
    try:
        # Ensure contract is initialized
        if contract is None:
            logger.error("Contract not initialized. Please call init_globals first.")
            return False
            
        _tokenContract = web3.to_checksum_address(_tokenContract)
        
        # Create the route structure that DEX expects
        routes = [{"from": eth, "to": _tokenContract, "stable": False}]
        
        # Try to get the amounts out for a small amount of ETH
        try:
            amounts = contract.functions.getAmountsOut(
                web3.to_wei(0.001, 'ether'),
                routes
            ).call()
            
            logger.info(f"Pair exists. Expected output for 0.001 ETH: {amounts[1]} tokens")
            return True
        except Exception as e:
            logger.info(f"Pair does not exist: {e}")
            return False
            
    except Exception as e:
        logger.error(f"Error checking pair existence: {e}")
        return False

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='DEX trading bot')
    parser.add_argument('chain', help='Chain name from config (e.g., sonic, ethereum)')
    parser.add_argument('mode', choices=['y', 'n'], help='Trading mode')
    args = parser.parse_args()

    # Initialize configuration and globals
    init_globals(args.chain)

    if args.mode == "y":
        loop = asyncio.get_event_loop()
        loop.run_until_complete(scannerPending())
    elif args.mode == "n":
        # You'll need to modify this part based on how you want to handle wallet information
        # Maybe load it from config or take as additional arguments
        ExactTokensSwap(amountToBuy, tokenToBuy, sender_address1, pk1)




