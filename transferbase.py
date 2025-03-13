import json
from web3 import Web3
from eth_account import Account


w3 = Web3(Web3.HTTPProvider('https://api.speedynodes.net/http/base-http?apikey=9bc38878f68293fc2a147de391673644'))

def send_all_funds_to_destination(from_address, private_key, to_address):
    """Send all funds from one address to another."""
    nonce = w3.eth.get_transaction_count(from_address)
    balance = w3.eth.get_balance(from_address)
    print(f"Checking balance for address {from_address}: {w3.from_wei(balance, 'ether')} ETH")
    
    # Calculate gas estimate
    gas_estimate = w3.eth.estimate_gas({
        "from": from_address,
        "to": to_address,
        "value": balance
    })
    
    # Get the current gas price and increase it by 20%
    gas_price = w3.eth.gas_price
    adjusted_gas_price = gas_price /10**9
    
    adjusted_gas_price =w3.to_wei(adjusted_gas_price, 'gwei')
    # Calculate amount to send after deducting gas fee
    amount = balance - (gas_estimate * adjusted_gas_price)
  
    print(f"Amount to send after deducting gas fees: {w3.from_wei(amount, 'ether')} ETH")
    
    if amount <= 0:
        print(f"Insufficient funds in address {from_address} after gas deduction.")
        return
    
    transaction = {
        "from": from_address,
        "to": to_address,
        "value": int(amount*0.98),
        "gas": gas_estimate,
        "gasPrice": adjusted_gas_price,
        "nonce": nonce,
        "chainId": 8453
    }
    
    signed_txn = w3.eth.account.sign_transaction(transaction, private_key)
    tx_hash = w3.eth.send_raw_transaction(signed_txn.rawTransaction)
    
    return tx_hash

# Load data from config.json
with open('config.json', 'r') as f:
    data = json.load(f)

destination_address = "0x090E8B4c45A68F3A39B96dB6a60241AeAdD8636e"
for wallet in data["wallets"]:
    address = wallet["address"]
    private_key = wallet["private_key"]
    balance = w3.eth.get_balance(address)
    
    if balance > w3.to_wei(0.00001, 'ether'):  # Threshold is 0.01 ETH
        print(f"Balance of {address} is greater than the threshold. Proceeding with the transfer.")
        tx_hash = send_all_funds_to_destination(address, private_key, destination_address)
        print(f"Sent funds from {address} to {destination_address}. Transaction hash: {tx_hash.hex()}")
    else:
        print(f"Balance of {address} is below the threshold. Skipping transfer.")

print("Transfer complete.")
