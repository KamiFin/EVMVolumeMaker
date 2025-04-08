import json
import time
from solana.rpc.commitment import Confirmed, Processed
from solana.rpc.types import TokenAccountOpts
from solders.signature import Signature #type: ignore
from solders.pubkey import Pubkey  # type: ignore
from solana_config import client, payer_keypair, MAX_RETRIES, BACKOFF_FACTOR
import logging
from typing import Optional

logger = logging.getLogger(__name__)

def get_token_balance(mint_str: str, owner_pubkey: Optional[Pubkey] = None) -> float | None:
    """
    Get token balance for a specific wallet or the default payer wallet
    
    Args:
        mint_str (str): Token mint address
        owner_pubkey (Optional[Pubkey]): Public key of the wallet to check balance for. If None, uses payer_keypair.
    
    Returns:
        float | None: Token balance or None if no balance found
    """
    mint = Pubkey.from_string(mint_str)
    owner = owner_pubkey if owner_pubkey else payer_keypair.pubkey()
    
    response = client.get_token_accounts_by_owner_json_parsed(
        owner,
        TokenAccountOpts(mint=mint),
        commitment=Processed
    )

    if response.value:
        accounts = response.value
        if accounts:
            token_amount = accounts[0].account.data.parsed['info']['tokenAmount']['uiAmount']
            if token_amount:
                return float(token_amount)
    return None

def confirm_txn(txn_sig, max_retries: int = None, retry_interval: int = None) -> bool:
    """
    Confirm a transaction with exponential backoff and configurable retries
    
    Args:
        txn_sig: Transaction signature to confirm (can be Signature object, string, or response object)
        max_retries: Maximum number of retries (defaults to MAX_RETRIES from config)
        retry_interval: Base interval between retries in seconds (defaults to BACKOFF_FACTOR from config)
    
    Returns:
        bool: True if transaction confirmed successfully, False if failed, None if max retries reached
    """
    # Use config values if not provided
    max_retries = max_retries or MAX_RETRIES
    retry_interval = retry_interval or BACKOFF_FACTOR
    
    # Convert the signature to a proper string or Signature object that client.get_transaction can accept
    try:
        if hasattr(txn_sig, 'value'):
            # Handle response objects that have a value attribute
            txn_sig = txn_sig.value
        elif hasattr(txn_sig, 'Signature'):
            # Handle response objects with Signature attribute
            txn_sig = txn_sig.Signature
        elif isinstance(txn_sig, str) and txn_sig.startswith('SendTransactionResp'):
            # Handle string representation of response object
            # Extract the signature from the string using regex
            import re
            signature_match = re.search(r'Signature\(([a-zA-Z0-9]+)', txn_sig)
            if signature_match:
                txn_sig = signature_match.group(1)
    except Exception as e:
        logger.error(f"Failed to extract signature from object: {e}")
        return False
    
    retries = 1
    current_interval = retry_interval
    
    while retries <= max_retries:
        try:
            txn_res = client.get_transaction(
                txn_sig, 
                encoding="json", 
                commitment=Confirmed, 
                max_supported_transaction_version=0)
            
            if txn_res.value is None:
                logger.debug(f"Transaction not yet confirmed (attempt {retries}/{max_retries})")
                if retries < max_retries:
                    logger.info(f"Waiting {current_interval} seconds before next check...")
                    time.sleep(current_interval)
                    # Exponential backoff
                    current_interval *= 2
                retries += 1
                continue
            
            txn_json = json.loads(txn_res.value.transaction.meta.to_json())
            
            if txn_json['err'] is None:
                logger.info(f"Transaction confirmed successfully after {retries} attempts")
                return True
            
            if txn_json['err']:
                if "InsufficientFundsForRent" in str(txn_json['err']):
                    logger.warning("Transaction failed due to InsufficientFundsForRent, wallet likely drained. Accepting.")
                    return True
                # Handle Raydium-specific error codes
                if isinstance(txn_json['err'], dict) and 'InstructionError' in txn_json['err']:
                    inst_err = txn_json['err']['InstructionError']
                    if isinstance(inst_err, list) and len(inst_err) == 2:
                        error_code = inst_err[1].get('Custom')
                        if error_code == 30:
                            logger.error("Transaction failed: Price impact too high or slippage exceeded")
                        elif error_code == 1:
                            logger.error("Transaction failed: Insufficient funds or liquidity")
                        elif error_code == 2:
                            logger.error("Transaction failed: Invalid pool state")
                        elif error_code == 3:
                            logger.error("Transaction failed: Invalid token account")
                        elif error_code == 4:
                            logger.error("Transaction failed: Invalid market state")
                        else:
                            logger.error(f"Transaction failed with Raydium error code: {error_code}")
                else:
                    logger.error(f"Transaction failed with error: {txn_json['err']}")
                return False
                
        except Exception as e:
            logger.warning(f"Error checking transaction status (attempt {retries}/{max_retries}): {e}")
            
            if retries < max_retries:
                logger.info(f"Waiting {current_interval} seconds before retry...")
                time.sleep(current_interval)
                # Exponential backoff
                current_interval *= 2
            else:
                logger.error(f"Max retries ({max_retries}) reached. Transaction confirmation failed.")
                return None
                
        retries += 1
    
    logger.error(f"Max retries ({max_retries}) reached. Transaction confirmation failed.")
    return None
