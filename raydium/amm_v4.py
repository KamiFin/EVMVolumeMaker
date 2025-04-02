import base64
import os
from typing import Optional
from solana.rpc.commitment import Processed
from solana.rpc.types import TokenAccountOpts, TxOpts
from solders.compute_budget import set_compute_unit_limit, set_compute_unit_price  # type: ignore
from solders.message import MessageV0  # type: ignore
from solders.pubkey import Pubkey  # type: ignore
from solders.system_program import (
    CreateAccountWithSeedParams,
    create_account_with_seed,
)
from solders.transaction import VersionedTransaction  # type: ignore
from spl.token.client import Token
from spl.token.instructions import (
    CloseAccountParams,
    InitializeAccountParams,
    close_account,
    create_associated_token_account,
    get_associated_token_address,
    initialize_account,
)
from utils.common_utils import confirm_txn, get_token_balance
from utils.solana_utils import get_optimal_compute_unit_price, handle_compute_unit_failure
from utils.pool_utils import (
    AmmV4PoolKeys,
    fetch_amm_v4_pool_keys,
    get_amm_v4_reserves,
    make_amm_v4_swap_instruction
)
from solana_config import client, payer_keypair, UNIT_BUDGET, UNIT_PRICE, MAX_RETRIES, BACKOFF_FACTOR
from raydium.constants import ACCOUNT_LAYOUT_LEN, SOL_DECIMAL, TOKEN_PROGRAM_ID, WSOL
import logging

logger = logging.getLogger(__name__)

def buy(pair_address: str, sol_in: float = 0.1, slippage: int = 1, pool_keys: Optional[AmmV4PoolKeys] = None) -> bool:
    try:
        logger.info(f"Starting buy transaction for pair address: {pair_address}")

        # Use cached pool keys if provided, otherwise fetch them
        if pool_keys is None:
            logger.info("Fetching pool keys...")
            pool_keys = fetch_amm_v4_pool_keys(pair_address)
            if pool_keys is None:
                logger.error("No pool keys found...")
                return False
            logger.info("Pool keys fetched successfully.")
        else:
            logger.info("Using cached pool keys.")

        mint = (pool_keys.base_mint if pool_keys.base_mint != WSOL else pool_keys.quote_mint)

        logger.info("Calculating transaction amounts...")
        amount_in = int(sol_in * SOL_DECIMAL)

        base_reserve, quote_reserve, token_decimal = get_amm_v4_reserves(pool_keys)
        amount_out = sol_for_tokens(sol_in, base_reserve, quote_reserve)
        logger.info(f"Estimated Amount Out: {amount_out}")

        slippage_adjustment = 1 - (slippage / 100)
        amount_out_with_slippage = amount_out * slippage_adjustment
        minimum_amount_out = int(amount_out_with_slippage * 10**token_decimal)
        logger.info(f"Amount In: {amount_in} | Minimum Amount Out: {minimum_amount_out}")

        logger.info("Checking for existing token account...")
        token_account_check = client.get_token_accounts_by_owner(payer_keypair.pubkey(), TokenAccountOpts(mint), Processed)
        if token_account_check.value:
            token_account = token_account_check.value[0].pubkey
            create_token_account_instruction = None
            logger.info("Token account found.")
        else:
            token_account = get_associated_token_address(payer_keypair.pubkey(), mint)
            create_token_account_instruction = create_associated_token_account(payer_keypair.pubkey(), payer_keypair.pubkey(), mint)
            logger.info("No existing token account found; creating associated token account.")

        logger.info("Generating seed for WSOL account...")
        seed = base64.urlsafe_b64encode(os.urandom(24)).decode("utf-8")
        wsol_token_account = Pubkey.create_with_seed(payer_keypair.pubkey(), seed, TOKEN_PROGRAM_ID)
        balance_needed = Token.get_min_balance_rent_for_exempt_for_account(client)

        logger.info("Creating and initializing WSOL account...")
        create_wsol_account_instruction = create_account_with_seed(
            CreateAccountWithSeedParams(
                from_pubkey=payer_keypair.pubkey(),
                to_pubkey=wsol_token_account,
                base=payer_keypair.pubkey(),
                seed=seed,
                lamports=int(balance_needed + amount_in),
                space=ACCOUNT_LAYOUT_LEN,
                owner=TOKEN_PROGRAM_ID,
            )
        )

        init_wsol_account_instruction = initialize_account(
            InitializeAccountParams(
                program_id=TOKEN_PROGRAM_ID,
                account=wsol_token_account,
                mint=WSOL,
                owner=payer_keypair.pubkey(),
            )
        )

        logger.info("Creating swap instructions...")
        swap_instruction = make_amm_v4_swap_instruction(
            amount_in=amount_in,
            minimum_amount_out=minimum_amount_out,
            token_account_in=wsol_token_account,
            token_account_out=token_account,
            accounts=pool_keys,
            owner=payer_keypair.pubkey(),
        )

        logger.info("Preparing to close WSOL account after swap...")
        close_wsol_account_instruction = close_account(
            CloseAccountParams(
                program_id=TOKEN_PROGRAM_ID,
                account=wsol_token_account,
                dest=payer_keypair.pubkey(),
                owner=payer_keypair.pubkey(),
            )
        )

        # Get optimal compute unit price based on network conditions
        optimal_price = get_optimal_compute_unit_price()
        
        instructions = [
            set_compute_unit_limit(UNIT_BUDGET),
            set_compute_unit_price(optimal_price),
            create_wsol_account_instruction,
            init_wsol_account_instruction,
        ]

        if create_token_account_instruction:
            instructions.append(create_token_account_instruction)

        instructions.append(swap_instruction)
        instructions.append(close_wsol_account_instruction)

        logger.info("Compiling transaction message...")
        compiled_message = MessageV0.try_compile(
            payer_keypair.pubkey(),
            instructions,
            [],
            client.get_latest_blockhash().value.blockhash,
        )

        logger.info("Sending transaction...")
        attempt = 1
        max_attempts = 3  # Maximum number of retry attempts for compute unit failures
        
        while attempt <= max_attempts:
            try:
                txn_sig = client.send_transaction(
                    txn=VersionedTransaction(compiled_message, [payer_keypair]),
                    opts=TxOpts(skip_preflight=True),
                ).value
                logger.info(f"Transaction Signature: {txn_sig}")
                break  # If successful, exit the retry loop
                
            except Exception as e:
                error_str = str(e).lower()
                if "compute budget exceeded" in error_str or "insufficient funds for compute" in error_str:
                    logger.warning(f"Compute unit failure on attempt {attempt}: {e}")
                    
                    # Get new compute unit price for retry
                    optimal_price = handle_compute_unit_failure(e, attempt, UNIT_PRICE)
                    
                    # Update instructions with new price
                    instructions[1] = set_compute_unit_price(optimal_price)
                    
                    # Recompile message with new instructions
                    compiled_message = MessageV0.try_compile(
                        payer_keypair.pubkey(),
                        instructions,
                        [],
                        client.get_latest_blockhash().value.blockhash,
                    )
                    
                    attempt += 1
                    if attempt <= max_attempts:
                        logger.info(f"Retrying transaction with adjusted compute unit price...")
                        continue
                    else:
                        logger.error("Max retry attempts reached for compute unit failures")
                        return False
                else:
                    # For other errors, don't retry
                    logger.error(f"Transaction error: {e}")
                    return False

        logger.info("Confirming transaction...")
        confirmed = confirm_txn(txn_sig, max_retries=MAX_RETRIES, retry_interval=BACKOFF_FACTOR)

        if confirmed is True:
            logger.info("Transaction confirmed successfully")
            return True
        elif confirmed is False:
            logger.error("Transaction failed")
            return False
        else:
            logger.error("Transaction confirmation timed out")
            return False

    except Exception as e:
        logger.error(f"Error occurred during transaction: {e}")
        return False

def sell(pair_address: str, percentage: int = 100, slippage: int = 1, pool_keys: Optional[AmmV4PoolKeys] = None) -> bool:
    try:
        logger.info(f"Starting sell transaction for pair address: {pair_address}")
        if not (1 <= percentage <= 100):
            logger.error("Percentage must be between 1 and 100.")
            return False

        # Use cached pool keys if provided, otherwise fetch them
        if pool_keys is None:
            logger.info("Fetching pool keys...")
            pool_keys = fetch_amm_v4_pool_keys(pair_address)
            if pool_keys is None:
                logger.error("No pool keys found...")
                return False
            logger.info("Pool keys fetched successfully.")
        else:
            logger.info("Using cached pool keys.")

        mint = (pool_keys.base_mint if pool_keys.base_mint != WSOL else pool_keys.quote_mint)

        logger.info("Retrieving token balance...")
        token_balance = get_token_balance(str(mint))
        logger.info(f"Token Balance: {token_balance}")

        if token_balance == 0 or token_balance is None:
            logger.error("No token balance available to sell.")
            return False

        token_balance = token_balance * (percentage / 100)
        logger.info(f"Selling {percentage}% of the token balance, adjusted balance: {token_balance}")

        logger.info("Calculating transaction amounts...")
        base_reserve, quote_reserve, token_decimal = get_amm_v4_reserves(pool_keys)
        amount_out = tokens_for_sol(token_balance, base_reserve, quote_reserve)
        logger.info(f"Estimated Amount Out: {amount_out}")

        slippage_adjustment = 1 - (slippage / 100)
        amount_out_with_slippage = amount_out * slippage_adjustment
        minimum_amount_out = int(amount_out_with_slippage * SOL_DECIMAL)

        amount_in = int(token_balance * 10**token_decimal)
        logger.info(f"Amount In: {amount_in} | Minimum Amount Out: {minimum_amount_out}")
        token_account = get_associated_token_address(payer_keypair.pubkey(), mint)

        logger.info("Generating seed and creating WSOL account...")
        seed = base64.urlsafe_b64encode(os.urandom(24)).decode("utf-8")
        wsol_token_account = Pubkey.create_with_seed(payer_keypair.pubkey(), seed, TOKEN_PROGRAM_ID)
        balance_needed = Token.get_min_balance_rent_for_exempt_for_account(client)

        create_wsol_account_instruction = create_account_with_seed(
            CreateAccountWithSeedParams(
                from_pubkey=payer_keypair.pubkey(),
                to_pubkey=wsol_token_account,
                base=payer_keypair.pubkey(),
                seed=seed,
                lamports=int(balance_needed),
                space=ACCOUNT_LAYOUT_LEN,
                owner=TOKEN_PROGRAM_ID,
            )
        )

        init_wsol_account_instruction = initialize_account(
            InitializeAccountParams(
                program_id=TOKEN_PROGRAM_ID,
                account=wsol_token_account,
                mint=WSOL,
                owner=payer_keypair.pubkey(),
            )
        )

        logger.info("Creating swap instructions...")
        swap_instructions = make_amm_v4_swap_instruction(
            amount_in=amount_in,
            minimum_amount_out=minimum_amount_out,
            token_account_in=token_account,
            token_account_out=wsol_token_account,
            accounts=pool_keys,
            owner=payer_keypair.pubkey(),
        )

        logger.info("Preparing to close WSOL account after swap...")
        close_wsol_account_instruction = close_account(
            CloseAccountParams(
                program_id=TOKEN_PROGRAM_ID,
                account=wsol_token_account,
                dest=payer_keypair.pubkey(),
                owner=payer_keypair.pubkey(),
            )
        )

        # Get optimal compute unit price based on network conditions
        optimal_price = get_optimal_compute_unit_price()
        
        instructions = [
            set_compute_unit_limit(UNIT_BUDGET),
            set_compute_unit_price(optimal_price),
            create_wsol_account_instruction,
            init_wsol_account_instruction,
            swap_instructions,
            close_wsol_account_instruction,
        ]

        if percentage == 100:
            logger.info("Preparing to close token account after swap...")
            close_token_account_instruction = close_account(
                CloseAccountParams(
                    program_id=TOKEN_PROGRAM_ID,
                    account=token_account,
                    dest=payer_keypair.pubkey(),
                    owner=payer_keypair.pubkey(),
                )
            )
            instructions.append(close_token_account_instruction)

        logger.info("Compiling transaction message...")
        compiled_message = MessageV0.try_compile(
            payer_keypair.pubkey(),
            instructions,
            [],
            client.get_latest_blockhash().value.blockhash,
        )

        logger.info("Sending transaction...")
        attempt = 1
        max_attempts = 3  # Maximum number of retry attempts for compute unit failures
        
        while attempt <= max_attempts:
            try:
                txn_sig = client.send_transaction(
                    txn=VersionedTransaction(compiled_message, [payer_keypair]),
                    opts=TxOpts(skip_preflight=True),
                ).value
                logger.info(f"Transaction Signature: {txn_sig}")
                break  # If successful, exit the retry loop
                
            except Exception as e:
                error_str = str(e).lower()
                if "compute budget exceeded" in error_str or "insufficient funds for compute" in error_str:
                    logger.warning(f"Compute unit failure on attempt {attempt}: {e}")
                    
                    # Get new compute unit price for retry
                    optimal_price = handle_compute_unit_failure(e, attempt, UNIT_PRICE)
                    
                    # Update instructions with new price
                    instructions[1] = set_compute_unit_price(optimal_price)
                    
                    # Recompile message with new instructions
                    compiled_message = MessageV0.try_compile(
                        payer_keypair.pubkey(),
                        instructions,
                        [],
                        client.get_latest_blockhash().value.blockhash,
                    )
                    
                    attempt += 1
                    if attempt <= max_attempts:
                        logger.info(f"Retrying transaction with adjusted compute unit price...")
                        continue
                    else:
                        logger.error("Max retry attempts reached for compute unit failures")
                        return False
                else:
                    # For other errors, don't retry
                    logger.error(f"Transaction error: {e}")
                    return False

        logger.info("Confirming transaction...")
        confirmed = confirm_txn(txn_sig, max_retries=MAX_RETRIES, retry_interval=BACKOFF_FACTOR)

        if confirmed is True:
            logger.info("Transaction confirmed successfully")
            return True
        elif confirmed is False:
            logger.error("Transaction failed")
            return False
        else:
            logger.error("Transaction confirmation timed out")
            return False

    except Exception as e:
        logger.error(f"Error occurred during transaction: {e}")
        return False

def sol_for_tokens(sol_amount, base_vault_balance, quote_vault_balance, swap_fee=0.25):
    effective_sol_used = sol_amount - (sol_amount * (swap_fee / 100))
    constant_product = base_vault_balance * quote_vault_balance
    updated_base_vault_balance = constant_product / (quote_vault_balance + effective_sol_used)
    tokens_received = base_vault_balance - updated_base_vault_balance
    return round(tokens_received, 9)

def tokens_for_sol(token_amount, base_vault_balance, quote_vault_balance, swap_fee=0.25):
    effective_tokens_sold = token_amount * (1 - (swap_fee / 100))
    constant_product = base_vault_balance * quote_vault_balance
    updated_quote_vault_balance = constant_product / (base_vault_balance + effective_tokens_sold)
    sol_received = quote_vault_balance - updated_quote_vault_balance
    return round(sol_received, 9)
