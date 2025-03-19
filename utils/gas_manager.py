"""
Gas management utilities for blockchain transactions.
Provides dynamic gas price and limit calculations based on network conditions.
"""

import logging
from web3 import Web3
from web3.middleware import geth_poa_middleware
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

class GasManager:
    def __init__(self, web3: Web3, chain_id: int):
        """
        Initialize GasManager with web3 instance and chain ID.
        
        Args:
            web3: Web3 instance (should already have middleware configured)
            chain_id: Blockchain network ID
        """
        self.web3 = web3
        self.chain_id = chain_id
        
    def configure_chain_specifics(self):
        """Configure chain-specific settings"""
        if self.chain_id == 56:  # BSC
            return {
                'max_gas_price': self.web3.to_wei(5, 'gwei'),
                'min_gas_price': self.web3.to_wei(1, 'gwei'),
                'use_max_cap': True
            }
        elif self.chain_id == 137:  # Polygon
            return {
                'max_gas_price': None,
                'min_gas_price': self.web3.to_wei(30, 'gwei'),
                'use_max_cap': False
            }
        elif self.chain_id == 8453:  # Base
            return {
                'max_gas_price': None,
                'min_gas_price': self.web3.to_wei(0.1, 'gwei'),
                'use_max_cap': False
            }
        else:
            return {
                'max_gas_price': None,
                'min_gas_price': None,
                'use_max_cap': False
            }

    def get_optimal_gas_price(self) -> int:
        """
        Calculate optimal gas price based on network conditions.
        Returns gas price in Wei.
        """
        try:
            # Get base gas price from network
            base_gas_price = self.web3.eth.gas_price
            
            # Get chain-specific configurations
            chain_config = self.configure_chain_specifics()
            
            # Get latest block for network congestion analysis
            latest_block = self.web3.eth.get_block('latest')
            
            # Calculate dynamic multiplier based on block utilization
            block_utilization = len(latest_block['transactions']) / 15000000
            dynamic_multiplier = 1 + (block_utilization * 0.2)
            
            # Calculate suggested gas price with dynamic adjustment
            suggested_gas_price = int(base_gas_price * dynamic_multiplier)
            
            # Apply chain-specific limits
            if chain_config['use_max_cap'] and chain_config['max_gas_price']:
                suggested_gas_price = min(suggested_gas_price, chain_config['max_gas_price'])
            
            if chain_config['min_gas_price']:
                suggested_gas_price = max(suggested_gas_price, chain_config['min_gas_price'])
            
            logger.info(f"Base gas price: {self.web3.from_wei(base_gas_price, 'gwei')} Gwei")
            logger.info(f"Network congestion multiplier: {dynamic_multiplier}")
            logger.info(f"Final gas price: {self.web3.from_wei(suggested_gas_price, 'gwei')} Gwei")
            
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
            # Get chain-specific configurations
            chain_config = self.configure_chain_specifics()
            
            # BSC and some other chains don't support EIP-1559
            if self.chain_id in [56, 97]:  # BSC Mainnet and Testnet
                gas_price = self.get_optimal_gas_price()
                base_params['gasPrice'] = gas_price
                logger.info(f"Using legacy gas price for BSC: {self.web3.from_wei(gas_price, 'gwei')} Gwei")
                
            else:
                # Try EIP-1559 transaction type for other chains
                try:
                    latest_block = self.web3.eth.get_block('latest')
                    if 'baseFeePerGas' in latest_block:
                        base_fee = latest_block['baseFeePerGas']
                        max_priority_fee = self.web3.eth.max_priority_fee
                        max_fee_per_gas = base_fee * 2 + max_priority_fee  # Double the base fee plus priority fee
                        
                        base_params.update({
                            'maxFeePerGas': max_fee_per_gas,
                            'maxPriorityFeePerGas': max_priority_fee,
                            'type': 2  # EIP-1559 transaction type
                        })
                        logger.info(f"Using EIP-1559 gas params: maxFeePerGas={self.web3.from_wei(max_fee_per_gas, 'gwei')} Gwei, "
                                  f"maxPriorityFeePerGas={self.web3.from_wei(max_priority_fee, 'gwei')} Gwei")
                    else:
                        # Fallback to legacy transaction type
                        gas_price = self.get_optimal_gas_price()
                        base_params['gasPrice'] = gas_price
                        logger.info(f"Using legacy gas price: {self.web3.from_wei(gas_price, 'gwei')} Gwei")
                except Exception as e:
                    logger.warning(f"Error setting EIP-1559 params: {e}, falling back to legacy gas price")
                    gas_price = self.get_optimal_gas_price()
                    base_params['gasPrice'] = gas_price
                    logger.info(f"Using fallback legacy gas price: {self.web3.from_wei(gas_price, 'gwei')} Gwei")
            
            return base_params
            
        except Exception as e:
            logger.error(f"Error preparing transaction params: {e}")
            # Ultimate fallback - use simple gas price
            gas_price = self.get_optimal_gas_price()
            base_params['gasPrice'] = gas_price
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