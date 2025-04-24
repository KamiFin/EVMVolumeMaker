#!/usr/bin/env python3
"""
Test script to verify wallet recovery system is working
"""
import logging
import json
import time
import os
import shutil
import sys
import base58
from solana_volume_maker import SolanaVolumeMaker
from solders.keypair import Keypair
from solders.pubkey import Pubkey as SoldersPubkey
from solana.rpc.types import TxOpts
from solders.instruction import Instruction
from solders.message import MessageV0
from solders.transaction import VersionedTransaction
from solders.system_program import transfer, TransferParams

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("RecoveryTest")

def test_failed_wallet_recovery():
    """Test the failed wallet recovery system"""
    logger.info("Starting recovery system test")
    
    # Clean up any existing recovery files for test
    recovery_file = "failed_batch_wallets.json"
    
    try:
        # Backup existing recovery file if it exists
        if os.path.exists(recovery_file):
            backup_file = f"{recovery_file}.{time.strftime('%Y%m%d-%H%M%S')}.bak"
            shutil.copy2(recovery_file, backup_file)
            logger.info(f"Backed up existing recovery file to {backup_file}")
            os.remove(recovery_file)
    except Exception as e:
        logger.error(f"Error backing up existing recovery file: {e}")
        
    # Initialize volume maker
    maker = SolanaVolumeMaker("solana", "buy", False)
    
    # Manually add some test wallets to the recovery list
    # Generate a test wallet that we'll add to the recovery list
    test_keypair = Keypair()
    # Convert keypair to base58 format
    keypair_bytes = bytes(test_keypair.to_bytes_array())
    private_key_base58 = base58.b58encode(keypair_bytes).decode('utf-8')
    
    test_wallet = {
        "address": str(test_keypair.pubkey()),
        "private_key": private_key_base58
    }
    
    # Add wallet to recovery list
    if not hasattr(maker, 'failed_confirmation_wallets'):
        maker.failed_confirmation_wallets = []
    
    failed_wallet = {
        "wallet_index": 0,
        "address": test_wallet['address'],
        "private_key": test_wallet['private_key'],
        "amount": 0.00001,
        "txn_sig": "test_recovery_txn",
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "failed_after_retries": True
    }
    
    maker.failed_confirmation_wallets.append(failed_wallet)
    logger.info(f"Added test wallet to recovery list: {test_wallet['address']}")
    
    # Make sure batch_wallets has the test wallet
    maker.batch_wallets = [test_wallet]
    
    # Save failed wallets to recovery file
    maker._save_failed_wallets_for_recovery(maker.failed_confirmation_wallets)
    logger.info("Saved wallet to recovery file")
    
    # Verify recovery file was created successfully
    if os.path.exists(recovery_file):
        size = os.path.getsize(recovery_file)
        logger.info(f"Recovery file created successfully with size {size} bytes")
        
        # Read the recovery file to verify contents
        with open(recovery_file, 'r') as f:
            recovery_data = json.load(f)
            
        if len(recovery_data.get("batch_wallets", [])) > 0:
            logger.info(f"RECOVERY SYSTEM TEST PASSED: File created with wallet data")
            return True
        else:
            logger.error("RECOVERY SYSTEM TEST FAILED: File created but contains no wallet data")
            return False
    else:
        logger.error("RECOVERY SYSTEM TEST FAILED: File not created")
        return False

def test_full_recovery_process():
    """Test the full recovery process by creating a funded wallet and recovering it"""
    logger.info("Starting full recovery system test")
    
    # Clean up any existing recovery files for test
    recovery_file = "failed_batch_wallets.json"
    
    try:
        # Backup existing recovery file if it exists
        if os.path.exists(recovery_file):
            backup_file = f"{recovery_file}.{time.strftime('%Y%m%d-%H%M%S')}.bak"
            shutil.copy2(recovery_file, backup_file)
            logger.info(f"Backed up existing recovery file to {backup_file}")
            os.remove(recovery_file)
    except Exception as e:
        logger.error(f"Error backing up existing recovery file: {e}")
        
    # Initialize volume maker
    maker = SolanaVolumeMaker("solana", "buy", False)
    
    # Generate a test wallet that we'll add to the recovery list
    test_keypair = Keypair()
    # Convert keypair to base58 format
    keypair_bytes = bytes(test_keypair.to_bytes_array())
    private_key_base58 = base58.b58encode(keypair_bytes).decode('utf-8')
    
    test_wallet = {
        "address": str(test_keypair.pubkey()),
        "private_key": private_key_base58
    }
    
    logger.info(f"Generated test wallet: {test_wallet['address']}")
    
    # Try to fund the test wallet with a small amount of SOL from the main wallet
    try:
        # Get main wallet
        main_wallet = maker.wallets[0]
        main_keypair = Keypair.from_base58_string(main_wallet['private_key'])
        main_address = main_wallet['address']
        
        # Create transfer instruction
        transfer_amount = 10000  # 0.00001 SOL in lamports (minimum needed for recovery)
        test_pubkey = SoldersPubkey.from_string(test_wallet['address'])
        
        logger.info(f"Transferring {transfer_amount/1e9} SOL from main wallet to test wallet")
        
        # Get fresh blockhash
        blockhash_response = maker.client.get_latest_blockhash()
        blockhash = blockhash_response.value.blockhash
        
        # Create transfer transaction
        transfer_ix = transfer(
            TransferParams(
                from_pubkey=main_keypair.pubkey(),
                to_pubkey=test_pubkey,
                lamports=transfer_amount
            )
        )
        
        # Create and sign versioned transaction
        compiled_message = MessageV0.try_compile(
            main_keypair.pubkey(),
            [transfer_ix],
            [],
            blockhash,
        )
        
        transaction = VersionedTransaction(compiled_message, [main_keypair])
        
        # Send transaction
        txn_sig = maker.client.send_transaction(
            txn=transaction,
            opts=TxOpts(skip_preflight=False),
        ).value
        
        logger.info(f"Transfer transaction sent: {txn_sig}")
        
        # Wait for confirmation
        for i in range(30):  # Wait up to 30 seconds
            resp = maker.client.get_signature_statuses([txn_sig]).value[0]
            if resp is not None and resp.confirmation_status == "confirmed":
                logger.info("Transfer confirmed!")
                break
            elif i == 29:
                logger.warning("Transfer not confirmed after 30 seconds")
            time.sleep(1)
            
        # Verify test wallet has received funds
        balance = maker.client.get_balance(test_pubkey).value
        logger.info(f"Test wallet balance: {balance/1e9} SOL")
        
        if balance >= transfer_amount:
            logger.info("Successfully funded test wallet")
        else:
            logger.warning(f"Test wallet not properly funded: {balance/1e9} SOL")
            
    except Exception as e:
        logger.error(f"Error funding test wallet: {e}")
        # Continue with the test anyway
    
    # Add the test wallet to batch_wallets
    maker.batch_wallets = [test_wallet]
    
    # Add wallet to recovery list
    if not hasattr(maker, 'failed_confirmation_wallets'):
        maker.failed_confirmation_wallets = []
        
    failed_wallet = {
        "wallet_index": 0,
        "address": test_wallet['address'],
        "private_key": test_wallet['private_key'],
        "amount": 0.00001,
        "txn_sig": "test_recovery_txn",
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "failed_after_retries": True
    }
    
    maker.failed_confirmation_wallets.append(failed_wallet)
    logger.info(f"Added test wallet to recovery list: {test_wallet['address']}")
    
    # Save failed wallets to recovery file
    maker._save_failed_wallets_for_recovery(maker.failed_confirmation_wallets)
    logger.info("Saved wallet to recovery file")
    
    # Verify recovery file was created successfully
    if os.path.exists(recovery_file):
        size = os.path.getsize(recovery_file)
        logger.info(f"Recovery file created successfully with size {size} bytes")
        
        # Read the recovery file to verify contents
        with open(recovery_file, 'r') as f:
            recovery_data = json.load(f)
            
        if len(recovery_data.get("batch_wallets", [])) > 0:
            logger.info(f"Part 1 PASSED: Recovery file created with wallet data")
            
            # Now test the recovery process
            try:
                # Create the processed_wallets structure expected by _recover_failed_batch_wallets
                processed_wallets = [{
                    "wallet_index": 0,
                    "address": test_wallet['address']
                }]
                
                # Run recovery
                logger.info("Starting recovery process...")
                recovery_result = maker._recover_failed_batch_wallets(
                    processed_wallets,
                    use_multi_sig=True,
                    swap_amount=0.000005
                )
                
                if recovery_result:
                    logger.info("FULL RECOVERY TEST PASSED: Successfully recovered funds")
                    return True
                else:
                    logger.warning("Recovery process completed but reported failure")
                    return False
                    
            except Exception as e:
                logger.error(f"Error during recovery process: {e}")
                return False
        else:
            logger.error("RECOVERY TEST FAILED: File created but contains no wallet data")
            return False
    else:
        logger.error("RECOVERY TEST FAILED: File not created")
        return False

if __name__ == "__main__":
    # Run basic recovery file test
    basic_result = test_failed_wallet_recovery()
    
    if basic_result:
        # If basic test passes, try full recovery test
        print("\nBasic recovery file creation test PASSED")
        print("Now testing full recovery process with funded wallet...\n")
        
        full_result = test_full_recovery_process()
        
        if full_result:
            print("\nFULL RECOVERY TEST PASSED")
            sys.exit(0)
        else:
            print("\nFULL RECOVERY TEST FAILED")
            sys.exit(1)
    else:
        print("\nBASIC RECOVERY TEST FAILED")
        sys.exit(1) 