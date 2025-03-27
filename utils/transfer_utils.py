import logging
import time
from web3 import Web3

logger = logging.getLogger(__name__)

def transfer_max_native(wallet_manager, from_wallet, to_address):
    """
    Transfer maximum native tokens with minimal buffer strategy.
    Added special handling for Sonic and BSC chains with RPC switching and buffer fallbacks.
    
    Args:
        wallet_manager: Instance of VolumeMaker or WalletRecovery class
        from_wallet: Wallet dictionary with 'address' and 'private_key' keys
        to_address: Destination address
        
    Returns:
        bool: True if transfer succeeded, False otherwise
    """
    w3 = wallet_manager.w3
    
    try:
        # Get wallet addresses
        from_address = w3.to_checksum_address(from_wallet['address'])
        to_address = w3.to_checksum_address(to_address)
        
        # Check sender balance (handle both class implementations)
        if hasattr(wallet_manager, '_check_wallet_balance'):
            # For maker.py
            balance, balance_in_eth = wallet_manager._check_wallet_balance(from_address)
            native_token = wallet_manager.config.NATIVE_TOKEN
            chain_id = wallet_manager.config.CHAIN_ID
        else:
            # For recovery.py
            balance, balance_in_eth = wallet_manager.check_native_balance(from_address)
            native_token = wallet_manager.chain_config['native_token']
            chain_id = wallet_manager.chain_config['chain_id']
        
        if balance <= w3.to_wei(0.00001, 'ether'):
            logger.info(f"Insufficient native balance to transfer from {from_address}")
            return False, None
            
        # Calculate gas estimate for a simple transfer
        try:
            gas_estimate = w3.eth.estimate_gas({
                "from": from_address,
                "to": to_address,
                "value": balance // 2  # Use half balance for estimation
            })
            logger.info(f"Gas estimate for native transfer: {gas_estimate}")
        except Exception as e:
            logger.error(f"Error estimating gas: {e}")
            gas_estimate = 21000  # Standard gas for ETH transfers
        
        # Get gas price (handle both class implementations)
        try:
            if hasattr(wallet_manager, '_get_current_gas_price'):
                # For maker.py
                gas_price_gwei = wallet_manager._get_current_gas_price()
                gas_price = w3.to_wei(gas_price_gwei, 'gwei')
            else:
                # For recovery.py 
                gas_price = wallet_manager.get_optimal_gas_price()
            
            logger.info(f"Gas price: {w3.from_wei(gas_price, 'gwei')} Gwei")
        except Exception as e:
            logger.error(f"Error getting gas price: {e}")
            gas_price = w3.to_wei(5, 'gwei')  # Conservative fallback
        
        # Calculate base gas cost
        gas_cost = gas_estimate * gas_price
        logger.info(f"Base gas cost: {w3.from_wei(gas_cost, 'ether')} {native_token}")
        
        # Modify the buffer strategy based on the chain
        if chain_id == 8453:  # Base Chain
            # More aggressive buffers for Base Chain
            buffer_multipliers = [1.0, 1.01, 1.02, 1.05, 1.1, 1.15, 1.2]  # Up to 20% buffer
        else:
            # Standard buffers for other chains
            buffer_multipliers = [1.0, 1.005, 1.01, 1.015, 1.02, 1.05, 1.1]
        
        for attempt, buffer_multiplier in enumerate(buffer_multipliers):
            # Calculate buffer based on buffer_multiplier
            gas_cost_with_buffer = int(gas_cost * buffer_multiplier)

            # Chain-specific buffer handling
            if chain_id == 8453:  # Base Chain - ultra minimal approach
                if attempt == 0:  # First attempt
                    # Try with absolutely no minimum buffer on first try
                    pass  # Skip adding any minimum buffer
                elif attempt == 1:  # Second attempt
                    # Tiny buffer for second attempt
                    min_buffer_wei = w3.to_wei(0.0000005, 'ether')  # 0.5 gwei buffer
                    gas_cost_with_buffer = max(gas_cost_with_buffer, gas_cost + min_buffer_wei)
                else:
                    # Slightly larger but still minimal buffer for later attempts
                    min_buffer_wei = w3.to_wei(0.000002, 'ether')
                    gas_cost_with_buffer = max(gas_cost_with_buffer, gas_cost + min_buffer_wei)
            elif chain_id in [146, 56]:  # Sonic Chain and BSC - No buffer for L1 chains
                # No minimum buffer for L1 chains - let them use exact gas estimation
                # This restores the previous behavior that was working correctly
                pass  # Let the percentage-based buffer from buffer_multiplier be the only buffer
            else:  # For all other chains
                # Apply standard buffer to ensure transactions go through
                min_buffer_percentage = 0.03  # 3% minimum buffer for other chains
                min_buffer = int(gas_cost * min_buffer_percentage)
                gas_cost_with_buffer = max(gas_cost_with_buffer, gas_cost + min_buffer)
            
            # Calculate maximum transferable amount
            transfer_amount = balance - gas_cost_with_buffer
            
            if transfer_amount <= 0:
                logger.warning(f"Insufficient balance to cover gas costs with {(buffer_multiplier-1)*100:.1f}% buffer")
                return False, None

            # Log attempt details
            if buffer_multiplier == 1.0:
                logger.info(f"Attempt {attempt+1}: Using exact gas calculation (no buffer)")
            else:
                buffer_percent = (buffer_multiplier - 1) * 100
                logger.info(f"Attempt {attempt+1}: Using {buffer_percent:.1f}% gas buffer")
                
            logger.info(f"Transferring: {w3.from_wei(transfer_amount, 'ether')} {native_token}")
            logger.info(f"Reserved for gas: {w3.from_wei(gas_cost_with_buffer, 'ether')} {native_token}")

            # Get a fresh nonce for each attempt
            current_nonce = w3.eth.get_transaction_count(from_address)
            
            # Build base transaction with current buffer
            base_tx = {
                "from": from_address,
                "to": to_address,
                "value": transfer_amount,
                "gas": gas_estimate,
                "nonce": current_nonce,
                "chainId": chain_id
            }
            
            # Prepare transaction (handle both class implementations)
            if hasattr(wallet_manager, 'gas_manager'):
                # For maker.py
                if chain_id in [146, 56]:  # L1 chains - Sonic and BSC
                    # Override and use legacy transaction format for L1 chains
                    tx_params = base_tx.copy()
                    tx_params["gasPrice"] = gas_price
                    logger.info(f"Using legacy transaction format for L1 chain (ID: {chain_id}) with gas price: {w3.from_wei(gas_price, 'gwei')} Gwei")
                else:
                    # For other chains, use the gas manager
                    tx_params = wallet_manager.gas_manager.prepare_transaction_params(base_tx)
            else:
                # For recovery.py
                tx_params = base_tx.copy()
                tx_params["gasPrice"] = gas_price
            
            # Sign transaction
            signed_txn = w3.eth.account.sign_transaction(tx_params, from_wallet['private_key'])
            
            try:
                # Send transaction
                tx_hash = w3.eth.send_raw_transaction(signed_txn.rawTransaction)
                logger.info(f"Transfer transaction sent: {tx_hash.hex()}")
                
                # Wait for receipt
                receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=90)
                
                if receipt['status'] == 1:
                    logger.info(f"Transfer successful with {(buffer_multiplier-1)*100:.1f}% buffer")
                    
                    # Verify the receiving wallet's balance for maker.py
                    if hasattr(wallet_manager, '_check_wallet_balance'):
                        time.sleep(2)
                        _, to_balance = wallet_manager._check_wallet_balance(to_address)
                        logger.info(f"Receiving wallet balance: {to_balance} {native_token}")
                    
                    return True, tx_hash
                else:
                    logger.error(f"Transfer failed with status: {receipt['status']}")
                    # Continue to next buffer size if not the last attempt
                    if attempt < len(buffer_multipliers) - 1:
                        logger.info(f"Trying with larger buffer...")
                        time.sleep(2)
                    else:
                        return False, None
            
            except Exception as e:
                error_msg = str(e)
                logger.warning(f"Attempt {attempt+1} failed: {error_msg}")
                
                # Check if it's a gas-related error
                gas_errors = ["insufficient funds", "gas required exceeds", "intrinsic gas too low", "overshot"]
                is_gas_error = any(err in error_msg.lower() for err in gas_errors)
                
                if is_gas_error:
                    # Try to extract exact shortfall from error
                    if "overshot" in error_msg:
                        try:
                            # Extract the exact shortfall amount
                            overshot = int(error_msg.split("overshot")[1].strip().split("}")[0])
                            logger.info(f"Transaction was short by: {w3.from_wei(overshot, 'ether')} {native_token}")
                            
                            # For Base Chain, use an extremely precise buffer
                            if chain_id == 8453:
                                # Add just 10% more than the exact amount needed
                                custom_buffer = int(overshot * 1.1)
                            else:
                                # For other chains, use a larger safety margin
                                custom_buffer = int(overshot * 1.5)
                                
                            custom_gas_cost = gas_cost + custom_buffer
                            custom_amount = balance - custom_gas_cost
                            
                            if custom_amount > 0:
                                logger.info(f"Trying with exact shortfall + minimal margin")
                                # Update transaction with custom amount
                                tx_params["value"] = custom_amount
                                tx_params["nonce"] = w3.eth.get_transaction_count(from_address)
                                
                                # Sign and send with custom amount
                                signed_txn = w3.eth.account.sign_transaction(tx_params, from_wallet['private_key'])
                                tx_hash = w3.eth.send_raw_transaction(signed_txn.rawTransaction)
                                
                                # Wait for receipt
                                receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=90)
                                if receipt['status'] == 1:
                                    logger.info(f"Transfer successful with custom shortfall compensation")
                                    return True, tx_hash
                        except:
                            pass
                    
                    # Continue to next buffer size if not the last attempt
                    if attempt < len(buffer_multipliers) - 1:
                        logger.info(f"Gas estimation was too low. Trying with larger buffer...")
                        time.sleep(2)
                    else:
                        logger.error("All buffer sizes failed")
                        return False, None
                elif "429" in error_msg and hasattr(wallet_manager, '_switch_rpc') and wallet_manager._switch_rpc():
                    # RPC rate limit error, try with a different RPC
                    logger.info("Switching RPC endpoint due to rate limiting")
                    return transfer_max_native(wallet_manager, from_wallet, to_address)
                else:
                    # If it's not a gas-related error, don't retry
                    logger.error(f"Error not related to gas estimation: {error_msg}")
                    return False, None

        # If we reach here, all normal attempts failed for Sonic/BSC
        if chain_id in [146, 56]:  # Sonic Chain and BSC
            logger.info("All standard attempts failed. Trying fallback strategy for Sonic/BSC...")
            
            # First try switching RPC if available
            if hasattr(wallet_manager, '_switch_rpc') and wallet_manager._switch_rpc():
                logger.info("Switched RPC endpoint. Retrying transfer...")
                return transfer_max_native(wallet_manager, from_wallet, to_address)
            
            # If RPC switch didn't work or wasn't available, try with small buffers
            small_buffers = [0.001, 0.002, 0.005, 0.01]  # 0.1% to 1% buffers
            
            for buffer_percentage in small_buffers:
                logger.info(f"Trying with {buffer_percentage*100}% buffer for Sonic/BSC...")
                
                # Calculate new gas cost with small buffer
                gas_cost_with_buffer = int(gas_cost * (1 + buffer_percentage))
                transfer_amount = balance - gas_cost_with_buffer
                
                if transfer_amount <= 0:
                    continue
                
                # Get fresh nonce
                current_nonce = w3.eth.get_transaction_count(from_address)
                
                # Build transaction
                tx_params = {
                    "from": from_address,
                    "to": to_address,
                    "value": transfer_amount,
                    "gas": gas_estimate,
                    "gasPrice": gas_price,
                    "nonce": current_nonce,
                    "chainId": chain_id
                }
                
                try:
                    signed_txn = w3.eth.account.sign_transaction(tx_params, from_wallet['private_key'])
                    tx_hash = w3.eth.send_raw_transaction(signed_txn.rawTransaction)
                    
                    logger.info(f"Fallback transfer attempt sent: {tx_hash.hex()}")
                    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=90)
                    
                    if receipt['status'] == 1:
                        logger.info(f"Transfer successful with {buffer_percentage*100}% fallback buffer")
                        return True, tx_hash
                        
                except Exception as e:
                    logger.warning(f"Fallback attempt failed with {buffer_percentage*100}% buffer: {str(e)}")
                    time.sleep(2)
                    continue
            
            logger.error("All fallback attempts failed for Sonic/BSC")
            return False, None

        return False, None  # All attempts failed
        
    except Exception as e:
        logger.error(f"Error in transfer_max_native: {e}")
        return False, None 