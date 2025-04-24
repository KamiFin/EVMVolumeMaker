#!/usr/bin/env python3
"""
Test script for Solana Volume Maker priority fee increases
"""
import logging
from utils.solana_utils import handle_compute_unit_failure

# Initialize logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

# Base priority fee
BASE_FEE = 50000  # 50,000 micro-lamports

def test_priority_fee_increases():
    """Test how priority fees increase with each retry attempt"""
    print("\nTesting priority fee increase for each retry attempt:")
    print("-" * 60)
    print(f"Base fee: {BASE_FEE} micro-lamports")
    print("-" * 60)
    
    for attempt in range(1, 6):
        # Test with compute budget error
        compute_error = Exception("Compute budget exceeded")
        fee = handle_compute_unit_failure(compute_error, attempt, BASE_FEE)
        
        # Calculate percentage increase
        increase = ((fee - BASE_FEE) / BASE_FEE) * 100
        
        print(f"Attempt {attempt}: {fee} micro-lamports ({increase:.1f}% increase)")
    
    print("-" * 60)
    print("Testing complete!")

if __name__ == "__main__":
    test_priority_fee_increases() 