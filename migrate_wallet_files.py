#!/usr/bin/env python3
"""
Migration Script for Wallet Files

This script migrates all wallet-related files from the root directory
to the wallet_recovery directory for better organization.

Files that will be migrated:
- batch_wallets_recovery.json
- failed_batch_wallets*.json
- recovery_attempt*.json
- permanent_failed_wallets*.json
- failed_wallet_stats.json
- skipped_zero_balance_wallets*.json
- recovery_failed_wallets*.json

The script will:
1. Create the wallet_recovery directory if it doesn't exist
2. Find all matching files in the root directory
3. Move each file to the wallet_recovery directory
4. Report the migration status
"""

import os
import shutil
import glob
import logging
import time

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("migration.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Define the wallet recovery directory
WALLET_RECOVERY_DIR = "wallet_recovery"

def migrate_wallet_files():
    """
    Migrate wallet files from root directory to wallet_recovery directory.
    
    Returns:
        tuple: (success_count, failed_count, skipped_count)
    """
    # Ensure wallet recovery directory exists
    os.makedirs(WALLET_RECOVERY_DIR, exist_ok=True)
    logger.info(f"Ensuring wallet recovery directory exists: {WALLET_RECOVERY_DIR}")
    
    # Define patterns to match wallet-related files
    patterns = [
        "batch_wallets_recovery*.json",
        "failed_batch_wallets*.json",
        "recovery_attempt*.json",
        "permanent_failed_wallets*.json",
        "failed_wallet_stats.json",
        "skipped_zero_balance_wallets*.json",
        "recovery_failed_wallets*.json"
    ]
    
    # Count tracking
    migrated_files = []
    failed_files = []
    skipped_files = []
    
    # Process each pattern
    for pattern in patterns:
        matching_files = glob.glob(pattern)
        logger.info(f"Found {len(matching_files)} files matching pattern: {pattern}")
        
        for file_path in matching_files:
            target_path = os.path.join(WALLET_RECOVERY_DIR, os.path.basename(file_path))
            
            # Skip if destination file already exists
            if os.path.exists(target_path):
                logger.warning(f"Skipping {file_path} - already exists in destination")
                skipped_files.append(file_path)
                continue
            
            try:
                # Move the file
                shutil.move(file_path, target_path)
                logger.info(f"Successfully migrated: {file_path} -> {target_path}")
                migrated_files.append(file_path)
            except Exception as e:
                logger.error(f"Failed to migrate {file_path}: {str(e)}")
                failed_files.append(file_path)
    
    # Summarize results
    return len(migrated_files), len(failed_files), len(skipped_files)

def main():
    """Main function that runs the migration process"""
    print("\n" + "="*60)
    print("  WALLET FILES MIGRATION TOOL")
    print("="*60)
    print("\nThis tool will migrate wallet-related files from the root directory")
    print(f"to the {WALLET_RECOVERY_DIR}/ directory for better organization.\n")
    
    # Ask for confirmation
    confirmation = input("Do you want to proceed with migration? (yes/no): ")
    if confirmation.lower() not in ["yes", "y"]:
        print("\nMigration cancelled.")
        return
    
    print("\nStarting migration process...\n")
    start_time = time.time()
    
    # Run migration
    migrated, failed, skipped = migrate_wallet_files()
    
    # Report results
    execution_time = time.time() - start_time
    print("\n" + "="*60)
    print("  MIGRATION COMPLETED")
    print("="*60)
    print(f"\nSuccessfully migrated: {migrated} files")
    print(f"Failed to migrate:    {failed} files")
    print(f"Skipped (duplicates): {skipped} files")
    print(f"\nExecution time: {execution_time:.2f} seconds")
    print("\nCheck migration.log for detailed information.")
    
    if failed > 0:
        print("\nWARNING: Some files couldn't be migrated. See log for details.")
    elif migrated == 0 and skipped == 0:
        print("\nNo wallet files found in the root directory to migrate.")
    else:
        print(f"\nAll wallet files have been organized into the {WALLET_RECOVERY_DIR}/ directory.")

if __name__ == "__main__":
    main() 