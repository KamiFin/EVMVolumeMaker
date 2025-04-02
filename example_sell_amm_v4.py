from raydium.amm_v4 import sell

if __name__ == "__main__":
    pair_address = "q3ULfxUXUuqU8U2YrWC3ajRRJaURvdrHX1AHdDreCnn"
    percentage = 100
    slippage = .05
    sell(pair_address, percentage, slippage)