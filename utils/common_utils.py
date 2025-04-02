import json
import time
from solana.rpc.commitment import Confirmed, Processed
from solana.rpc.types import TokenAccountOpts
from solders.signature import Signature #type: ignore
from solders.pubkey import Pubkey  # type: ignore
from solana_config import client, payer_keypair, MAX_RETRIES, BACKOFF_FACTOR
import logging

logger = logging.getLogger(__name__)

def get_token_balance(mint_str: str) -> float | None:
    mint = Pubkey.from_string(mint_str)
    response = client.get_token_accounts_by_owner_json_parsed(
        payer_keypair.pubkey(),
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

def confirm_txn(txn_sig: Signature, max_retries: int = None, retry_interval: int = None) -> bool:
    """
    Confirm a transaction with exponential backoff and configurable retries
    
    Args:
        txn_sig: Transaction signature to confirm
        max_retries: Maximum number of retries (defaults to MAX_RETRIES from config)
        retry_interval: Base interval between retries in seconds (defaults to BACKOFF_FACTOR from config)
    
    Returns:
        bool: True if transaction confirmed successfully, False if failed, None if max retries reached
    """
    # Use config values if not provided
    max_retries = max_retries or MAX_RETRIES
    retry_interval = retry_interval or BACKOFF_FACTOR
    
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
