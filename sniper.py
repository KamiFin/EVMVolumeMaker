import argparse
import json
import logging
from web3 import Web3
from hexbytes import HexBytes
import time
import asyncio
import random
from utils.gas_manager import GasManager
from utils.web3_utils import get_web3_connection

# Configure logging
logger = logging.getLogger(__name__)

class Config:
    def __init__(self, chain_name):
        """Initialize configuration for specified chain"""
        with open('config.json', 'r') as f:
            config = json.load(f)
            
        if chain_name not in config['chains']:
            raise ValueError(f"Chain '{chain_name}' not found in config.json")
            
        self.chain_config = config['chains'][chain_name]
        
        self.rpc_url = self.chain_config['rpc_url']
        self.chain_id = self.chain_config['chain_id']
        self.router_address = self.chain_config['dex']['router_address']
        self.router_abi = self.chain_config['dex']['router_abi']
        self.wrapped_native_token = self.chain_config['dex']['wrapped_native_token']
        self.token_contract = next(iter(self.chain_config['token'].values()))['contract_address']
        self.dex_type = self.chain_config.get('dex_type', 'uniswap')  # Default to uniswap for backward compatibility
        
        # Slippage settings
        self.buy_slippage = self.chain_config['transaction'].get('buy_slippage', 0.005)  # Default 0.5%
        self.sell_slippage = self.chain_config['transaction'].get('sell_slippage', 0.005)  # Default 0.5%
        
        self.gas_manager = None  # Will be initialized after web3 connection

# Global variables that will be initialized based on config
config = None
web3 = None
contract = None
eth = None  # Will hold wrapped native token address
uniSwap = None  # Will hold router address
rpc = None # Will hold rpc url
dex_type = None  # Will hold DEX type

def init_globals(chain_name):
    """Initialize global variables based on chain configuration"""
    global config, web3, contract, eth, uniSwap, rpc, dex_type
    
    try:
        config = Config(chain_name)
        rpc = config.rpc_url
        
        # Initialize web3 with proper middleware
        web3 = get_web3_connection(config.rpc_url, config.chain_id)
        
        if not web3.is_connected():
            raise ConnectionError(f"Failed to connect to RPC: {config.rpc_url}")
            
        # Initialize gas manager after web3 is properly configured
        config.gas_manager = GasManager(web3, config.chain_id)
        
        uniSwap = config.router_address
        eth = config.wrapped_native_token
        
        # Determine DEX type - default to 'uniswap' if not specified
        dex_type = config.dex_type
        
        # Initialize the contract with the router address and ABI
        contract = web3.eth.contract(
            address=web3.to_checksum_address(uniSwap), 
            abi=config.router_abi
        )
        
        logger.info(f"Initialized for chain: {chain_name}")
        logger.info(f"Connected to RPC: {config.rpc_url}")
        logger.info(f"Router address: {uniSwap}")
        logger.info(f"Wrapped token: {eth}")
        logger.info(f"DEX type: {dex_type}")
        
        return True
        
    except Exception as e:
        logger.error(f"Error initializing globals: {e}")
        raise

# Token ABI remains unchanged
tokenAbi = [
    {"constant":True,"inputs":[],"name":"decimals","outputs":[{"name":"","type":"uint8"}],"payable":False,"stateMutability":"view","type":"function"},
    {"inputs":[{"internalType":"address","name":"spender","type":"address"},{"internalType":"uint256","name":"amount","type":"uint256"}],"name":"approve","outputs":[{"internalType":"bool","name":"","type":"bool"}],"stateMutability":"nonpayable","type":"function"},
    {"inputs":[{"internalType":"address","name":"account","type":"address"}],"name":"balanceOf","outputs":[{"internalType":"uint256","name":"","type":"uint256"}],"stateMutability":"view","type":"function"},
    {"constant": True, "inputs": [{"name": "owner", "type": "address"}, {"name": "spender", "type": "address"}], "name": "allowance", "outputs": [{"name": "", "type": "uint256"}], "stateMutability": "view", "type": "function"}
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

def get_optimal_gas_price():
    """
    Determine optimal gas price based on network conditions and chain requirements.
    Returns gas price in Wei with safety margins.
    """
    try:
        # Get base gas price from network
        base_gas_price = web3.eth.gas_price
        chain_id = config.chain_id
        
        # Get latest block for network congestion analysis
        latest_block = web3.eth.get_block('latest')
        
        # Calculate dynamic multiplier based on block utilization
        block_utilization = len(latest_block['transactions']) / 15000000  # Approximate max block size
        dynamic_multiplier = 1 + (block_utilization * 0.2)  # Up to 20% increase based on congestion
        
        # Get max priority fee (for EIP-1559 compatible chains)
        try:
            max_priority_fee = web3.eth.max_priority_fee
        except Exception:
            max_priority_fee = 0
            
        # Calculate suggested gas price with dynamic adjustment
        suggested_gas_price = int(base_gas_price * dynamic_multiplier) + max_priority_fee
        
        logger.info(f"Base gas price: {web3.from_wei(base_gas_price, 'gwei')} Gwei")
        logger.info(f"Network congestion multiplier: {dynamic_multiplier}")
        
        return suggested_gas_price
        
    except Exception as e:
        logger.error(f"Error getting optimal gas price: {e}")
        return web3.eth.gas_price  # Fallback to network gas price

def estimate_gas_limit(tx_params, contract_call=None):
    """
    Estimate optimal gas limit for a transaction with safety margins.
    
    Args:
        tx_params (dict): Transaction parameters
        contract_call: Optional contract function call for gas estimation
    """
    try:
        # Get gas estimate either from contract call or direct transaction
        if contract_call:
            base_estimate = contract_call.estimate_gas(tx_params)
        else:
            base_estimate = web3.eth.estimate_gas(tx_params)
            
        # Get latest block gas limit for reference
        block_gas_limit = web3.eth.get_block('latest')['gasLimit']
        
        # Calculate safe gas limit (add 20% margin)
        safe_gas_limit = int(base_estimate * 1.2)
        
        # Ensure we don't exceed block gas limit
        final_gas_limit = min(safe_gas_limit, block_gas_limit - 100000)  # Leave room for other txs
        
        logger.info(f"Base gas estimate: {base_estimate}")
        logger.info(f"Safe gas limit: {final_gas_limit}")
        
        return final_gas_limit
        
    except Exception as e:
        logger.error(f"Error estimating gas limit: {e}")
        # Get average gas used in recent blocks as fallback
        recent_blocks = [web3.eth.get_block(block) for block in range(
            web3.eth.block_number - 3,
            web3.eth.block_number
        )]
        avg_gas_used = sum(block['gasUsed'] for block in recent_blocks) // len(recent_blocks)
        return min(avg_gas_used // 4, 500000)  # Conservative fallback

def ExactETHSwap(_ethAmount, _tokenContract, _sender, _pk, _gas=None, max_retries=3):
    """
    Swap exact ETH for tokens using DEX router with dynamic gas optimization
    """
    for attempt in range(1, max_retries + 1):
        try:
            logger.info(f"Starting ExactETHSwap: {_ethAmount} ETH for token {_tokenContract}")
            
            # Get current nonce
            nonce = web3.eth.get_transaction_count(_sender)
            
            # Get optimal gas price if not provided
            gas_price = web3.to_wei(_gas, 'gwei') if _gas is not None else get_optimal_gas_price()
            
            # Build base transaction parameters
            tx_params = {
                'from': _sender,
                'value': web3.to_wei(float(_ethAmount), 'ether'),
                'nonce': nonce,
                'chainId': config.chain_id
            }
            
            # Use gas manager to prepare transaction params
            tx_params = config.gas_manager.prepare_transaction_params(tx_params)
            
            # Prepare the swap paths
            if dex_type == 'shadow':
                routes = [{"from": eth, "to": _tokenContract, "stable": False}]
                
                # Get expected output with amounts_out call
                try:
                    expected_output = contract.functions.getAmountsOut(
                        web3.to_wei(float(_ethAmount), 'ether'),
                        routes
                    ).call()[1]
                    
                    # Apply slippage tolerance
                    min_output = int(expected_output * (1 - config.buy_slippage))
                    logger.info(f"Expected output: {expected_output}, Minimum with {config.buy_slippage*100}% slippage: {min_output}")
                except Exception as e:
                    logger.warning(f"Could not calculate minimum output: {e}")
                    min_output = 0  # Fallback to 0 if estimation fails
                
                swap_function = contract.functions.swapExactETHForTokens(
                    min_output, routes, _sender, (int(time.time()) + 10000)
                )
            else:
                path = [eth, _tokenContract]
                
                # Get expected output with amounts_out call
                try:
                    expected_output = contract.functions.getAmountsOut(
                        web3.to_wei(float(_ethAmount), 'ether'),
                        path
                    ).call()[1]
                    
                    # Apply slippage tolerance
                    min_output = int(expected_output * (1 - config.buy_slippage))
                    logger.info(f"Expected output: {expected_output}, Minimum with {config.buy_slippage*100}% slippage: {min_output}")
                except Exception as e:
                    logger.warning(f"Could not calculate minimum output: {e}")
                    min_output = 0  # Fallback to 0 if estimation fails
                
                swap_function = contract.functions.swapExactETHForTokens(
                    min_output, path, _sender, (int(time.time()) + 10000)
                )
            
            # Estimate gas limit for this specific transaction
            gas_limit = config.gas_manager.estimate_gas_limit(tx_params, swap_function)
            tx_params['gas'] = gas_limit
            
            # Build final transaction
            tx = swap_function.build_transaction(tx_params)
            
            logger.info(f"Transaction built with gas limit {gas_limit} and price {web3.from_wei(gas_price, 'gwei')} Gwei")
            
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
        # Use the correct route structure based on DEX type
        if dex_type == 'shadow':
            routes = [{"from": _tokenContract, "to": eth, "stable": False}]
            profit = contract.functions.getAmountsOut(balance, routes).call()
        else:
            # Standard Uniswap V2 path
            profit = contract.functions.getAmountsOut(balance, [_tokenContract, eth]).call()
            
        values.append(int(str(balance)[:-decimals]))
        values.append(round(web3.from_wei(profit[1], 'ether'), 2))
        return values
    except Exception as e:
        logger.error(f"Error getting profit: {e}")
        return None

def sellTokens(_tokenContract, _sender, _pk, _gas, percentage=1, gas_limit=None):
    """
    Sell tokens for ETH with support for both Uniswap and Shadow DEX types
    
    Args:
        _tokenContract (str): Token contract address
        _sender (str): Seller's address
        _pk (str): Private key
        _gas (float): Gas price
        percentage (float): Percentage of tokens to sell (1 = 100%)
        gas_limit (int, optional): Custom gas limit
    """
    try:
        logger.info(f"Starting sell process for {_sender}")
        
        # Get token balance first
        _tokenContract = web3.to_checksum_address(_tokenContract)
        Tkcontract = web3.eth.contract(address=_tokenContract, abi=tokenAbi)
        balance = Tkcontract.functions.balanceOf(_sender).call()
        decimals = Tkcontract.functions.decimals().call()
        
        logger.info(f"Token balance: {balance}")
        
        # Calculate amount to sell based on percentage parameter
        amount_to_sell = int(balance * percentage)
        
        # Calculate expected ETH output with slippage protection
        try:
            if dex_type == 'shadow':
                # Shadow/Sonic DEX specific route structure
                routes = [{"from": _tokenContract, "to": eth, "stable": False}]
                expected_eth = contract.functions.getAmountsOut(
                    amount_to_sell,
                    routes
                ).call()[1]
            else:
                # Standard Uniswap V2 path
                path = [_tokenContract, eth]
                expected_eth = contract.functions.getAmountsOut(
                    amount_to_sell,
                    path
                ).call()[1]
            
            # Apply slippage tolerance
            min_output = int(expected_eth * (1 - config.sell_slippage))
            logger.info(f"Expected ETH output: {web3.from_wei(expected_eth, 'ether')}, " +
                       f"Minimum with {config.sell_slippage*100}% slippage: {web3.from_wei(min_output, 'ether')}")
        except Exception as e:
            logger.warning(f"Could not calculate minimum output: {e}")
            min_output = 0  # Fallback to 0 if estimation fails
            
        # Approve token spending if needed
        try:
            allowance = Tkcontract.functions.allowance(_sender, contract.address).call()
            if allowance < amount_to_sell:
                approve_tx = Tkcontract.functions.approve(
                    contract.address,
                    2**256 - 1  # Max approval
                ).build_transaction({
                    'from': _sender,
                    'nonce': web3.eth.get_transaction_count(_sender),
                    'gasPrice': web3.eth.gas_price
                })
                
                signed_approve = web3.eth.account.sign_transaction(approve_tx, _pk)
                tx_hash = web3.eth.send_raw_transaction(signed_approve.rawTransaction)
                web3.eth.wait_for_transaction_receipt(tx_hash)
                logger.info("Token approval successful")
        except Exception as e:
            logger.error(f"Error in token approval: {e}")
            return False

        # Build the swap transaction based on DEX type
        if dex_type == 'shadow':
            routes = [{"from": _tokenContract, "to": eth, "stable": False}]
            swap_function = contract.functions.swapExactTokensForETH(
                amount_to_sell,
                min_output,
                routes,
                _sender,
                int(time.time()) + 10000
            )
        else:
            # Standard Uniswap V2 interface
            path = [_tokenContract, eth]
            swap_function = contract.functions.swapExactTokensForETHSupportingFeeOnTransferTokens(
                amount_to_sell,
                min_output,
                path,
                _sender,
                int(time.time()) + 10000
            )

        # Build the transaction
        tx = swap_function.build_transaction({
            'from': _sender,
            'value': 0,
            'gas': gas_limit or 1000000,
            'gasPrice': web3.to_wei(_gas, 'gwei'),
            'nonce': web3.eth.get_transaction_count(_sender),
        })
        
        logger.info("Signing transaction...")
        signed_txn = web3.eth.account.sign_transaction(tx, private_key=_pk)
        tx_token = web3.eth.send_raw_transaction(signed_txn.rawTransaction)
        receipt = web3.eth.wait_for_transaction_receipt(tx_token)
        
        if receipt['status'] == 0:
            # Get transaction details for debugging
            tx_details = web3.eth.get_transaction(receipt['transactionHash'].hex())
            logger.error(f"Sell transaction failed. Details: {tx_details}")
            return False
        else:
            logger.info("CONFIRMED: Transaction successful")
            return True
            
    except Exception as e:
        logger.error(f"Error in sellTokens: {str(e)}")
        return False

def check_token_allowance(_tokenContract, _sender, spender):
    """Check current token allowance for spender"""
    try:
        _tokenContract = web3.to_checksum_address(_tokenContract)
        token_contract = web3.eth.contract(address=_tokenContract, abi=tokenAbi)
        current_allowance = token_contract.functions.allowance(_sender, spender).call()
        return current_allowance
    except Exception as e:
        logger.error(f"Error checking allowance: {e}")
        return 0

def approve_tokens(_tokenContract, _sender, _pk, _gas, max_retries=2):
    """Approve tokens for router spending with allowance check"""
    try:
        logger.info(f"Checking current allowance for {_sender}")
        
        _tokenContract = web3.to_checksum_address(_tokenContract)
        Tkcontract = web3.eth.contract(address=_tokenContract, abi=tokenAbi)
        
        # Check current allowance
        current_allowance = check_token_allowance(_tokenContract, _sender, contract.address)
        max_approval = 2**256 - 1
        
        # If allowance is already high enough, skip approval
        if current_allowance > (max_approval // 2):
            logger.info("Token already has sufficient allowance")
            return True
            
        for attempt in range(1, max_retries + 1):
            try:
                nonce = web3.eth.get_transaction_count(_sender)
                
                # Build approval transaction
                tx = Tkcontract.functions.approve(
                    contract.address,  # Router contract
                    max_approval
                ).build_transaction({
                    'from': _sender,
                    'gas': 100000,  # Gas limit for approval
                    'gasPrice': web3.to_wei(_gas, 'gwei'),
                    'nonce': nonce,
                })
                
                # Sign and send transaction
                signed_txn = web3.eth.account.sign_transaction(tx, private_key=_pk)
                tx_hash = web3.eth.send_raw_transaction(signed_txn.rawTransaction)
                
                logger.info(f"Approval transaction sent: {tx_hash.hex()}")
                receipt = web3.eth.wait_for_transaction_receipt(tx_hash)
                
                if receipt['status'] == 1:
                    logger.info("CONFIRMED: Approval transaction successful")
                    return True
                else:
                    logger.error(f"Approval transaction failed with status: {receipt['status']}")
                    if attempt < max_retries:
                        logger.warning(f"Retrying approval ({attempt}/{max_retries})...")
                        time.sleep(2)
                    else:
                        return False
                        
            except Exception as e:
                logger.error(f"Error in token approval (attempt {attempt}): {e}")
                if attempt < max_retries:
                    time.sleep(2)
                else:
                    return False
        
        return False
        
    except Exception as e:
        logger.error(f"Error approving tokens: {e}")
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
        
        # Different DEX types have different function signatures
        try:
            if dex_type == 'shadow':
                # Sonic uses a route structure with from, to, stable properties
                routes = [{"from": eth, "to": _tokenContract, "stable": False}]
                amounts = contract.functions.getAmountsOut(
                    web3.to_wei(0.001, 'ether'),
                    routes
                ).call()
            else:
                # Standard Uniswap V2 interface uses a simple array of addresses
                path = [eth, _tokenContract]
                amounts = contract.functions.getAmountsOut(
                    web3.to_wei(0.001, 'ether'),
                    path
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




