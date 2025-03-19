"""
Gas management utilities for blockchain transactions.
Provides dynamic gas price and limit calculations based on network conditions.
"""

import logging
from web3 import Web3
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

class GasManager:
    def __init__(self, web3: Web3, chain_id: int):
        """
        Initialize GasManager with web3 instance and chain ID.
        
        Args:
            web3: Web3 instance
            chain_id: Blockchain network ID
        """
        self.web3 = web3
        self.chain_id = chain_id
        
    def get_optimal_gas_price(self) -> int:
        """
        Calculate optimal gas price based on network conditions.
        Returns gas price in Wei.
        """
        try:
            # Get base gas price from network
            base_gas_price = self.web3.eth.gas_price
            
            # Get latest block for network congestion analysis
            latest_block = self.web3.eth.get_block('latest')
            
            # Calculate dynamic multiplier based on block utilization
            block_utilization = len(latest_block['transactions']) / 15000000
            dynamic_multiplier = 1 + (block_utilization * 0.2)
            
            # Try to get EIP-1559 fees
            try:
                max_priority_fee = self.web3.eth.max_priority_fee
            except Exception:
                max_priority_fee = 0
                
            # Calculate suggested gas price with dynamic adjustment
            suggested_gas_price = int(base_gas_price * dynamic_multiplier) + max_priority_fee
            
            logger.info(f"Base gas price: {self.web3.from_wei(base_gas_price, 'gwei')} Gwei")
            logger.info(f"Network congestion multiplier: {dynamic_multiplier}")
            
            return suggested_gas_price
            
        except Exception as e:
            logger.error(f"Error getting optimal gas price: {e}")
            return self.web3.eth.gas_price
            
    def estimate_gas_limit(self, tx_params: Dict[str, Any], contract_call: Optional[Any] = None) -> int:
        """
        Estimate optimal gas limit for a transaction.
        
        Args:
            tx_params: Transaction parameters
            contract_call: Optional contract function call for estimation
        """
        try:
            # Get gas estimate
            if contract_call:
                base_estimate = contract_call.estimate_gas(tx_params)
            else:
                base_estimate = self.web3.eth.estimate_gas(tx_params)
                
            # Get block gas limit
            block_gas_limit = self.web3.eth.get_block('latest')['gasLimit']
            
            # Add 20% safety margin
            safe_gas_limit = int(base_estimate * 1.2)
            
            # Ensure we don't exceed block gas limit
            final_gas_limit = min(safe_gas_limit, block_gas_limit - 100000)
            
            logger.info(f"Base gas estimate: {base_estimate}")
            logger.info(f"Safe gas limit: {final_gas_limit}")
            
            return final_gas_limit
            
        except Exception as e:
            logger.error(f"Error estimating gas limit: {e}")
            return self._get_fallback_gas_limit()
            
    def prepare_transaction_params(self, base_params: Dict[str, Any]) -> Dict[str, Any]:
        """
        Prepare transaction parameters with appropriate gas settings.
        
        Args:
            base_params: Base transaction parameters
        """
        try:
            # Try EIP-1559 transaction type
            latest_block = self.web3.eth.get_block('latest')
            if 'baseFeePerGas' in latest_block:
                base_fee = latest_block['baseFeePerGas']
                base_params.update({
                    'maxFeePerGas': int(base_fee * 1.5),
                    'maxPriorityFeePerGas': self.web3.eth.max_priority_fee
                })
            else:
                # Legacy transaction type
                base_params['gasPrice'] = self.get_optimal_gas_price()
                
            return base_params
            
        except Exception as e:
            logger.error(f"Error preparing transaction params: {e}")
            base_params['gasPrice'] = self.get_optimal_gas_price()
            return base_params
            
    def _get_fallback_gas_limit(self) -> int:
        """Get fallback gas limit based on recent blocks"""
        try:
            recent_blocks = [self.web3.eth.get_block(block) for block in range(
                self.web3.eth.block_number - 3,
                self.web3.eth.block_number
            )]
            avg_gas_used = sum(block['gasUsed'] for block in recent_blocks) // len(recent_blocks)
            return min(avg_gas_used // 4, 500000)
        except Exception:
            return 500000  # Ultimate fallback 