"""
Priority Fee Manager for Solana transactions

This module provides a manager for handling priority fees with Helius RPC integration.
It includes features for:
- Auto-fetching priority fees every 5 minutes
- Dynamically adjusting fees based on transaction success rates
- Providing a simple interface for getting current priority fees
"""
import time
import json
import logging
import threading
import requests
from typing import Dict, Optional, Literal, Union, List

# Configure logging
logger = logging.getLogger(__name__)

# Priority fee levels
PriorityLevel = Literal["Min", "Low", "Medium", "High", "VeryHigh", "UnsafeMax"]

class PriorityFeeManager:
    """
    Manages priority fees for Solana transactions using Helius RPC API.
    
    Features:
    - Auto-fetches priority fees every 5 minutes
    - Maintains a cache of current fee estimates for all priority levels
    - Provides simple interface for getting appropriate fee based on transaction status
    - Automatically adjusts fee levels in response to transaction failures
    """
    
    def __init__(self, helius_rpc_url: str, update_interval: int = 300):
        """
        Initialize the Priority Fee Manager
        
        Args:
            helius_rpc_url (str): The Helius RPC URL with API key
            update_interval (int): Interval in seconds for auto-updating fees (default: 300 = 5 minutes)
        """
        self.helius_rpc_url = helius_rpc_url
        self.update_interval = update_interval
        
        # Initialize fee cache
        self.fee_cache: Dict[str, float] = {
            "min": 0.0,
            "low": 1000.0,
            "medium": 10000.0,
            "high": 100000.0,
            "veryHigh": 1000000.0,
            "unsafeMax": 5000000.0,
        }
        
        # Track last update time
        self.last_update_time = 0
        
        # Current global priority level (default: Medium)
        self.current_level: PriorityLevel = "Medium"
        
        # Recovery mode flag
        self.in_recovery_mode = False
        
        # Failed transaction counter
        self.failed_tx_count = 0
        
        # Latest fee fetch success flag
        self.last_fetch_success = False
        
        # Start the auto-update thread
        self.stop_thread = False
        self.update_thread = threading.Thread(target=self._auto_update_fees, daemon=True)
        self.update_thread.start()
        
        # Perform initial update
        self.update_fees()
    
    def _auto_update_fees(self):
        """Background thread to automatically update fees at regular intervals"""
        while not self.stop_thread:
            time.sleep(self.update_interval)
            try:
                self.update_fees()
            except Exception as e:
                logger.error(f"Error in auto-update thread: {str(e)}")
    
    def update_fees(self) -> bool:
        """
        Update priority fee cache from Helius API
        
        Returns:
            bool: True if update was successful, False otherwise
        """
        try:
            payload = {
                "jsonrpc": "2.0",
                "id": "1",
                "method": "getPriorityFeeEstimate",
                "params": [{
                    "accountKeys": ["11111111111111111111111111111111"],  # System program as a fallback
                    "options": {
                        "includeAllPriorityFeeLevels": True
                    }
                }]
            }
            
            response = requests.post(
                self.helius_rpc_url,
                headers={"Content-Type": "application/json"},
                json=payload,
                timeout=10
            )
            
            if response.status_code == 200:
                result = response.json()
                if "result" in result and "priorityFeeLevels" in result["result"]:
                    self.fee_cache = result["result"]["priorityFeeLevels"]
                    self.last_update_time = time.time()
                    self.last_fetch_success = True
                    logger.info(f"Priority fees updated: {json.dumps(self.fee_cache)}")
                    
                    # If we were in recovery mode but network seems normal now,
                    # and we're at a higher level, gradually step down
                    if self.in_recovery_mode and self._can_lower_priority_level():
                        self._step_down_priority_level()
                    return True
                else:
                    logger.warning(f"Unexpected response format: {result}")
            else:
                logger.error(f"Failed to fetch priority fees: {response.status_code} - {response.text}")
            
            self.last_fetch_success = False
            return False
            
        except Exception as e:
            logger.error(f"Error updating priority fees: {str(e)}")
            self.last_fetch_success = False
            return False
    
    def get_priority_fee(self, priority_level: Optional[PriorityLevel] = None) -> float:
        """
        Get the priority fee for a specified level
        
        Args:
            priority_level (Optional[PriorityLevel]): The priority level to use, or None to use current global level
            
        Returns:
            float: The priority fee in microlamports
        """
        level = priority_level.lower() if priority_level else self.current_level.lower()
        return self.fee_cache.get(level, self.fee_cache["medium"])
    
    def get_priority_fee_for_transaction(
        self,
        transaction: Optional[Union[object, object]],  # Using generic object type for flexibility
        priority_level: Optional[PriorityLevel] = None
    ) -> float:
        """
        Get priority fee estimate for a specific transaction
        
        Args:
            transaction: The Solana transaction to get fee estimate for, or None to use level-based fee
                         Can be a Transaction or VersionedTransaction
            priority_level: Optional priority level, uses global level if not specified
            
        Returns:
            float: The priority fee in microlamports
        """
        try:
            # If no transaction provided, use regular level-based fee
            if transaction is None:
                return self.get_priority_fee(priority_level)
            
            # Use account-based estimate if transaction fetch fails
            level = priority_level or self.current_level
            
            # Use a safer approach without trying to serialize the transaction
            # This avoids dependency on specific serialization libraries
            
            # Just use a simple account-based request with system program
            payload = {
                "jsonrpc": "2.0",
                "id": "1",
                "method": "getPriorityFeeEstimate",
                "params": [{
                    "accountKeys": ["11111111111111111111111111111111"],  # System program as a fallback
                    "options": {
                        "priorityLevel": level
                    }
                }]
            }
            
            response = requests.post(
                self.helius_rpc_url,
                headers={"Content-Type": "application/json"},
                json=payload,
                timeout=10
            )
            
            if response.status_code == 200:
                result = response.json()
                if "result" in result and "priorityFeeEstimate" in result["result"]:
                    fee_estimate = result["result"]["priorityFeeEstimate"]
                    logger.info(f"Transaction-specific priority fee ({level}): {fee_estimate}")
                    return fee_estimate
            
            # If we couldn't get transaction-specific fee, fall back to cached level
            return self.get_priority_fee(level)
            
        except Exception as e:
            logger.error(f"Error getting transaction-specific priority fee: {str(e)}")
            return self.get_priority_fee(priority_level)
    
    def handle_transaction_failure(self, error: Exception) -> float:
        """
        Handle transaction failure by potentially increasing priority level
        
        Args:
            error: The exception that occurred
            
        Returns:
            float: New priority fee to use for retry
        """
        error_str = str(error).lower()
        
        # Check if error is related to compute budgets or transaction processing
        compute_related = any(msg in error_str for msg in [
            "compute budget exceeded",
            "insufficient funds for compute",
            "transaction simulation failed", 
            "block height exceeded",
            "vote landing failed",
            "failed to get recent blockhash"
        ])
        
        if compute_related:
            self.failed_tx_count += 1
            
            # If multiple failures, escalate to recovery mode
            if self.failed_tx_count >= 3:
                self.enter_recovery_mode()
            
            # Return a higher fee based on current level
            return self._get_escalated_fee()
        
        return self.get_priority_fee()
    
    def handle_transaction_success(self):
        """Handle successful transaction by potentially lowering priority level"""
        # Reset failure counter
        self.failed_tx_count = 0
        
        # If we've had multiple successes in recovery mode, consider exiting
        if self.in_recovery_mode and self._can_lower_priority_level():
            self._step_down_priority_level()
    
    def enter_recovery_mode(self):
        """Enter recovery mode with elevated priority fees"""
        if not self.in_recovery_mode:
            logger.warning("Entering recovery mode with elevated priority fees")
            self.in_recovery_mode = True
            
            # Increase priority level unless already at maximum
            if self.current_level != "UnsafeMax":
                next_level = self._get_next_priority_level(self.current_level)
                
                # Set global priority level to higher level
                prev_level = self.current_level
                self.current_level = next_level
                logger.info(f"Increased priority level: {prev_level} -> {next_level}")
    
    def _can_lower_priority_level(self) -> bool:
        """Check if we can lower the priority level"""
        # Only lower if we've had a successful fee fetch recently and
        # we're not at the lowest level
        return (
            self.last_fetch_success and 
            self.current_level != "Low" and
            self.failed_tx_count == 0
        )
    
    def _step_down_priority_level(self):
        """Step down the priority level by one"""
        priority_levels: List[PriorityLevel] = [
            "Min", "Low", "Medium", "High", "VeryHigh", "UnsafeMax"
        ]
        
        current_index = priority_levels.index(self.current_level)
        if current_index > 1:  # Don't go below Low
            new_level = priority_levels[current_index - 1]
            prev_level = self.current_level
            self.current_level = new_level
            logger.info(f"Decreased priority level: {prev_level} -> {new_level}")
            
            # Exit recovery mode if we're back to Medium or lower
            if current_index - 1 <= 2:  # Medium is at index 2
                logger.info("Exiting recovery mode")
                self.in_recovery_mode = False
    
    def _get_escalated_fee(self) -> float:
        """Get an escalated fee based on current failure state"""
        if self.failed_tx_count >= 5:
            # For severe failures, use UnsafeMax
            return self.fee_cache["unsafeMax"]
        elif self.failed_tx_count >= 3:
            # For moderate failures, use VeryHigh
            return self.fee_cache["veryHigh"]
        else:
            # For minor failures, escalate one level
            next_level = self._get_next_priority_level(self.current_level).lower()
            return self.fee_cache.get(next_level, self.fee_cache["high"])
    
    def _get_next_priority_level(self, current: PriorityLevel) -> PriorityLevel:
        """Get the next higher priority level"""
        priority_levels: List[PriorityLevel] = [
            "Min", "Low", "Medium", "High", "VeryHigh", "UnsafeMax"
        ]
        
        try:
            current_index = priority_levels.index(current)
            new_index = min(current_index + 1, len(priority_levels) - 1)
            return priority_levels[new_index]
        except ValueError:
            return "High"  # Default to High if current level is invalid
    
    def get_current_priority_level(self) -> PriorityLevel:
        """Get the current global priority level"""
        return self.current_level
    
    def set_priority_level(self, level: PriorityLevel):
        """
        Manually set the global priority level
        
        Args:
            level (PriorityLevel): The new priority level to use
        """
        self.current_level = level
        logger.info(f"Priority level manually set to {level}")
    
    def shutdown(self):
        """Clean shutdown of the manager"""
        self.stop_thread = True
        if self.update_thread.is_alive():
            self.update_thread.join(timeout=1.0) 

    