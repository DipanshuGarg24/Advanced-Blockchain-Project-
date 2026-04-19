#!/usr/bin/env python3
"""
Trusted-BPI — Smart Contract Interaction Script

This script directly interacts with your deployed contract on Sepolia.
Run this to:
  1. See your contract info
  2. Register users on-chain
  3. Deposit collateral (real Sepolia ETH)
  4. Check balances

Usage:
    python contract_interact.py

Your contract: https://sepolia.etherscan.io/address/0xc1A2dd3210949C559466063aE96E201F4DD48B42
"""

import json
import sys
import time

try:
    from web3 import Web3
except ImportError:
    print("Install web3: pip install web3")
    sys.exit(1)

# ── Config ──
INFURA_URL = "https://sepolia.infura.io/v3/63a484426e114166bc47ff9278cb253a"
CONTRACT_ADDRESS = "0xc1A2dd3210949C559466063aE96E201F4DD48B42"
PRIVATE_KEY = "bc7d880b3c5abeb66b200e71ea304e9a84e5f5fbc2b9cedf8329a13cc7ed7355"

CONTRACT_ABI = [
    {"inputs":[],"name":"register","outputs":[],"stateMutability":"nonpayable","type":"function"},
    {"inputs":[],"name":"deposit","outputs":[],"stateMutability":"payable","type":"function"},
    {"inputs":[{"name":"amount","type":"uint256"}],"name":"withdrawBalance","outputs":[],"stateMutability":"nonpayable","type":"function"},
    {"inputs":[{"name":"amount","type":"uint256"}],"name":"withdrawCollateral","outputs":[],"stateMutability":"nonpayable","type":"function"},
    {"inputs":[{"name":"user","type":"address"},{"name":"value","type":"uint256"}],"name":"recordIssuance","outputs":[],"stateMutability":"nonpayable","type":"function"},
    {"inputs":[{"name":"paymentId","type":"bytes32"},{"name":"payer","type":"address"},{"name":"payee","type":"address"},{"name":"amount","type":"uint256"},{"name":"tokenSerial","type":"bytes32"}],"name":"settle","outputs":[],"stateMutability":"nonpayable","type":"function"},
    {"inputs":[{"name":"tokenSerial","type":"bytes32"},{"name":"cheater","type":"address"},{"name":"payment1Hash","type":"bytes32"},{"name":"payment2Hash","type":"bytes32"},{"name":"victim1","type":"address"},{"name":"victim2","type":"address"},{"name":"tokenValue","type":"uint256"}],"name":"reportDoubleSpend","outputs":[],"stateMutability":"nonpayable","type":"function"},
    {"inputs":[],"name":"getSystemStats","outputs":[{"name":"_totalCollateral","type":"uint256"},{"name":"_totalSettled","type":"uint256"},{"name":"_totalFrauds","type":"uint256"}],"stateMutability":"view","type":"function"},
    {"inputs":[{"name":"user","type":"address"}],"name":"getAccountInfo","outputs":[{"name":"collateral","type":"uint256"},{"name":"tokensIssued","type":"uint256"},{"name":"balance","type":"uint256"},{"name":"registered","type":"bool"},{"name":"slashed","type":"bool"}],"stateMutability":"view","type":"function"},
    {"inputs":[{"name":"user","type":"address"}],"name":"getMaxIssuance","outputs":[{"type":"uint256"}],"stateMutability":"view","type":"function"},
    {"inputs":[],"name":"mintAuthority","outputs":[{"type":"address"}],"stateMutability":"view","type":"function"},
    {"inputs":[],"name":"totalCollateralLocked","outputs":[{"type":"uint256"}],"stateMutability":"view","type":"function"},
    {"inputs":[],"name":"totalSettled","outputs":[{"type":"uint256"}],"stateMutability":"view","type":"function"},
    {"inputs":[],"name":"totalFraudsDetected","outputs":[{"type":"uint256"}],"stateMutability":"view","type":"function"},
    {"anonymous":False,"inputs":[{"indexed":True,"name":"user","type":"address"},{"indexed":False,"name":"amount","type":"uint256"},{"indexed":False,"name":"totalCollateral","type":"uint256"}],"name":"CollateralDeposited","type":"event"},
    {"anonymous":False,"inputs":[{"indexed":True,"name":"paymentId","type":"bytes32"},{"indexed":True,"name":"payer","type":"address"},{"indexed":True,"name":"payee","type":"address"},{"indexed":False,"name":"amount","type":"uint256"}],"name":"PaymentSettled","type":"event"},
    {"anonymous":False,"inputs":[{"indexed":True,"name":"tokenSerial","type":"bytes32"},{"indexed":True,"name":"cheater","type":"address"},{"indexed":False,"name":"amountSlashed","type":"uint256"}],"name":"DoubleSpendDetected","type":"event"},
]

# ── Connect ──
print("=" * 55)
print("  TRUSTED-BPI CONTRACT INTERACTION")
print("=" * 55)

w3 = Web3(Web3.HTTPProvider(INFURA_URL))
if not w3.is_connected():
    print("Cannot connect to Sepolia!")
    sys.exit(1)

account = w3.eth.account.from_key(PRIVATE_KEY)
contract = w3.eth.contract(address=CONTRACT_ADDRESS, abi=CONTRACT_ABI)

print(f"  Network: Sepolia (Chain {w3.eth.chain_id})")
print(f"  Contract: {CONTRACT_ADDRESS}")
print(f"  Your wallet: {account.address}")

balance = w3.eth.get_balance(account.address)
print(f"  Your balance: {w3.from_wei(balance, 'ether')} ETH")
print("=" * 55)


def send_tx(tx_func, value=0, gas=200000):
    """Build, sign, send a transaction and wait for receipt."""
    tx = tx_func.build_transaction({
        'from': account.address,
        'nonce': w3.eth.get_transaction_count(account.address),
        'gas': gas,
        'gasPrice': w3.eth.gas_price,
        'value': value,
    })
    signed = w3.eth.account.sign_transaction(tx, PRIVATE_KEY)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    print(f"  Tx sent: {tx_hash.hex()}")
    print(f"  Waiting for confirmation...")
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
    print(f"  Block: {receipt.blockNumber}, Gas used: {receipt.gasUsed}")
    print(f"  Status: {'SUCCESS' if receipt.status == 1 else 'FAILED'}")
    print(f"  View: https://sepolia.etherscan.io/tx/{tx_hash.hex()}")
    return receipt


def show_menu():
    print("\n  ACTIONS:")
    print("  1. View contract stats")
    print("  2. Check account info")
    print("  3. Register on-chain")
    print("  4. Deposit collateral (0.001 ETH)")
    print("  5. Deposit custom amount")
    print("  6. Record token issuance")
    print("  7. Test settlement (demo)")
    print("  8. View all contract events")
    print("  9. Quit")
    return input("\n  Choose (1-9): ").strip()


while True:
    choice = show_menu()

    if choice == "1":
        print("\n  ── Contract Stats ──")
        try:
            stats = contract.functions.getSystemStats().call()
            mint_auth = contract.functions.mintAuthority().call()
            print(f"  Mint authority: {mint_auth}")
            print(f"  Total collateral: {w3.from_wei(stats[0], 'ether')} ETH")
            print(f"  Total settled: {w3.from_wei(stats[1], 'ether')} ETH")
            print(f"  Total frauds: {stats[2]}")
        except Exception as e:
            print(f"  Error: {e}")

    elif choice == "2":
        addr = input("  Enter address (or press Enter for yours): ").strip()
        if not addr:
            addr = account.address
        try:
            info = contract.functions.getAccountInfo(Web3.to_checksum_address(addr)).call()
            print(f"\n  Account: {addr}")
            print(f"  Collateral: {w3.from_wei(info[0], 'ether')} ETH")
            print(f"  Tokens issued: {w3.from_wei(info[1], 'ether')} ETH")
            print(f"  Balance: {w3.from_wei(info[2], 'ether')} ETH")
            print(f"  Registered: {info[3]}")
            print(f"  Slashed: {info[4]}")
        except Exception as e:
            print(f"  Error: {e}")

    elif choice == "3":
        print("\n  Registering on-chain...")
        try:
            receipt = send_tx(contract.functions.register())
            if receipt.status == 1:
                print("  ✅ Registered!")
        except Exception as e:
            print(f"  Error: {e}")

    elif choice == "4":
        print("\n  Depositing 0.001 ETH as collateral...")
        deposit_amount = w3.to_wei(0.001, 'ether')
        try:
            receipt = send_tx(contract.functions.deposit(), value=deposit_amount)
            if receipt.status == 1:
                info = contract.functions.getAccountInfo(account.address).call()
                print(f"  ✅ Deposited! Collateral: {w3.from_wei(info[0], 'ether')} ETH")
        except Exception as e:
            print(f"  Error: {e}")

    elif choice == "5":
        amt = input("  Enter ETH amount (e.g. 0.005): ").strip()
        try:
            deposit_amount = w3.to_wei(float(amt), 'ether')
            print(f"\n  Depositing {amt} ETH...")
            receipt = send_tx(contract.functions.deposit(), value=deposit_amount)
            if receipt.status == 1:
                print(f"  ✅ Deposited!")
        except Exception as e:
            print(f"  Error: {e}")

    elif choice == "6":
        amt = input("  Enter issuance value in wei (e.g. 500000000000000): ").strip()
        try:
            receipt = send_tx(
                contract.functions.recordIssuance(account.address, int(amt))
            )
            if receipt.status == 1:
                print(f"  ✅ Issuance recorded!")
        except Exception as e:
            print(f"  Error: {e}")

    elif choice == "7":
        print("\n  Running demo settlement...")
        print("  This will register, deposit, record issuance, and settle.")
        try:
            # Check if already registered
            info = contract.functions.getAccountInfo(account.address).call()
            if not info[3]:
                print("\n  Step 1: Register...")
                send_tx(contract.functions.register())
                time.sleep(2)

            print("\n  Step 2: Deposit 0.002 ETH...")
            send_tx(contract.functions.deposit(), value=w3.to_wei(0.002, 'ether'))
            time.sleep(2)

            print("\n  Step 3: Record issuance (0.001 ETH worth)...")
            send_tx(contract.functions.recordIssuance(
                account.address, w3.to_wei(0.001, 'ether')
            ))
            time.sleep(2)

            print("\n  Step 4: Settle payment...")
            payment_id = Web3.keccak(text=f"demo-payment-{int(time.time())}")
            serial = Web3.keccak(text=f"demo-serial-{int(time.time())}")
            # Settle to self for demo
            send_tx(contract.functions.settle(
                payment_id,
                account.address,  # payer
                account.address,  # payee (self for demo)
                w3.to_wei(0.001, 'ether'),
                serial,
            ))

            print("\n  ✅ Demo complete! Check Etherscan:")
            print(f"  https://sepolia.etherscan.io/address/{CONTRACT_ADDRESS}")

        except Exception as e:
            print(f"  Error: {e}")

    elif choice == "8":
        print("\n  ── Recent Events ──")
        try:
            # Get last 100 blocks of events
            latest = w3.eth.block_number
            from_block = max(0, latest - 1000)

            settled_events = contract.events.PaymentSettled.get_logs(fromBlock=from_block)
            deposit_events = contract.events.CollateralDeposited.get_logs(fromBlock=from_block)
            fraud_events = contract.events.DoubleSpendDetected.get_logs(fromBlock=from_block)

            if not settled_events and not deposit_events and not fraud_events:
                print("  No events found. Run option 7 first to create some!")
            else:
                for e in deposit_events:
                    print(f"  DEPOSIT: {e.args.user[:10]}... deposited {w3.from_wei(e.args.amount, 'ether')} ETH")
                for e in settled_events:
                    print(f"  SETTLED: {w3.from_wei(e.args.amount, 'ether')} ETH | payer={e.args.payer[:10]}... payee={e.args.payee[:10]}...")
                for e in fraud_events:
                    print(f"  🚨 FRAUD: cheater={e.args.cheater[:10]}... slashed={w3.from_wei(e.args.amountSlashed, 'ether')} ETH")

        except Exception as e:
            print(f"  Error: {e}")

    elif choice == "9":
        print("\n  Bye!")
        break
    else:
        print("  Invalid choice")