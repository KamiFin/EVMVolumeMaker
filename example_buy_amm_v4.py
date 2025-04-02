from raydium.amm_v4 import buy

if __name__ == "__main__":
    pair_address = "q3ULfxUXUuqU8U2YrWC3ajRRJaURvdrHX1AHdDreCnn"
    sol_in = .01
    slippage = .05
    buy(pair_address, sol_in, slippage)