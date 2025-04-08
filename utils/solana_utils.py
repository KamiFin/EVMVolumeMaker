import logging
from solana.rpc.api import Client
from solana_config import client, UNIT_PRICE

logger = logging.getLogger(__name__)

def get_optimal_compute_unit_price() -> int:
    """
    Calculate optimal compute unit price based on network conditions.
    Returns price in micro-lamports.
    
    This function helps optimize transaction success rate by adjusting
    compute unit price based on network congestion.
    """
    try:
        # Get recent performance samples
        performance_samples = client.get_recent_performance_samples(limit=4)
        
        if not performance_samples.value:
            logger.warning("No performance samples available, using default price")
            return UNIT_PRICE
            
        # Calculate average slot time
        avg_slot_time = sum(sample.sample_period_secs / sample.num_slots 
                          for sample in performance_samples.value) / len(performance_samples.value)
        
        # Adjust price based on slot time
        if avg_slot_time > 0.8:  # Network is congested
            adjusted_price = int(UNIT_PRICE * 1.5)  # Increase price by 50%
            logger.info(f"Network congested (avg slot time: {avg_slot_time:.2f}s), increasing compute unit price to {adjusted_price}")
        elif avg_slot_time < 0.4:  # Network is fast
            adjusted_price = int(UNIT_PRICE * 0.8)  # Decrease price by 20%
            logger.info(f"Network fast (avg slot time: {avg_slot_time:.2f}s), decreasing compute unit price to {adjusted_price}")
        else:
            adjusted_price = UNIT_PRICE
            logger.info(f"Network normal (avg slot time: {avg_slot_time:.2f}s), using default compute unit price {adjusted_price}")
            
        return adjusted_price
            
    except Exception as e:
        logger.error(f"Error calculating optimal compute unit price: {e}")
        logger.info("Falling back to default compute unit price")
        return UNIT_PRICE

def get_retry_compute_unit_price(attempt: int, base_price: int) -> int:
    """
    Calculate compute unit price for retry attempts.
    Increases price exponentially with each retry.
    
    Args:
        attempt (int): Current retry attempt number (1-based)
        base_price (int): Base compute unit price to adjust from
        
    Returns:
        int: Adjusted compute unit price for retry
    """
    # Exponential backoff with a maximum multiplier
    max_multiplier = 3.0  # Maximum 3x the base price
    multiplier = min(1.5 ** attempt, max_multiplier)  # 1.5x increase per attempt, capped at 3x
    
    adjusted_price = int(base_price * multiplier)
    logger.info(f"Retry attempt {attempt}: Increasing compute unit price to {adjusted_price} (multiplier: {multiplier:.2f}x)")
    return adjusted_price

def handle_compute_unit_failure(error: Exception, attempt: int, base_price: int) -> int:
    """
    Handle compute unit failures by adjusting the price for retry.
    
    Args:
        error (Exception): The error that occurred
        attempt (int): Current retry attempt number
        base_price (int): Base compute unit price
        
    Returns:
        int: New compute unit price to use for retry
    """
    error_str = str(error).lower()
    
    # Check if error is related to compute units
    if "compute budget exceeded" in error_str or "insufficient funds for compute" in error_str:
        return get_retry_compute_unit_price(attempt, base_price)
    
    # For other errors, use normal network-based price
    return get_optimal_compute_unit_price() 

