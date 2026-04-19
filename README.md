# Trusted-BPI — Complete Setup & Deployment Guide

## CS 762 Advanced Blockchain — IIT Bombay
**Dipanshu Garg (24M0755) & Nitesh Singh (22B0919)**

---

## System Architecture

```
 LAPTOP 1 (Alice)                    YOUR SERVER                  LAPTOP 2 (Bob)
┌─────────────────┐              ┌─────────────────┐          ┌─────────────────┐
│  Wallet App     │   ONLINE     │  Mint Server    │  ONLINE  │  Wallet App     │
│  (Tkinter)      │─────────────>│  (Flask:9000)   │<─────────│  (Tkinter)      │
│                 │  register,   │                 │ register,│                 │
│  TEE Wallet     │  mint tokens,│  Blind Signer   │ settle   │  TEE Wallet     │
│  (simulated)    │  settle      │  Fraud Detector │          │  (simulated)    │
└────────┬────────┘              │                 │          └────────┬────────┘
         │                       │  ↕ web3.py      │                   │
         │    OFFLINE            │                 │                   │
         │    (TCP Socket)       │  ┌─────────────┐│                   │
         └───────────────────────┼──┤ Smart       ││───────────────────┘
           Direct P2P payment    │  │ Contract    ││
           No server involved!   │  │ (Sepolia)   ││
                                 │  └─────────────┘│
                                 └─────────────────┘
```

---

## Step-by-Step Setup

### Step 1: Install Python Dependencies (ALL machines)

```bash
pip install flask requests pycryptodome web3 eth-account
```

### Step 2: Deploy Smart Contract on Sepolia

**Option A: Using Remix IDE (RECOMMENDED — easiest)**

1. Go to https://remix.ethereum.org
2. Create new file: `TrustedBPI.sol`
3. Paste the code from `contracts/TrustedBPI.sol`
4. In the Solidity Compiler tab:
   - Select compiler version `0.8.19`
   - Click "Compile TrustedBPI.sol"
5. In the Deploy tab:
   - Environment: "Injected Provider - MetaMask"
   - Make sure MetaMask is on **Sepolia** network
   - Constructor args: `200` and `1000`
     (200 = 2x collateral ratio, 1000 = 10% penalty)
   - Click "Deploy" → Confirm in MetaMask
6. Copy the deployed contract address
7. View on https://sepolia.etherscan.io/address/YOUR_ADDRESS

**Getting Sepolia ETH (free):**
- https://sepoliafaucet.com (requires Alchemy account)
- https://www.infura.io/faucet/sepolia (requires Infura account)
- https://sepolia-faucet.pk910.de (PoW faucet, no account needed)

**Option B: Using Hardhat (command line)**

```bash
cd contracts/
npm init -y
npm install hardhat @nomicfoundation/hardhat-toolbox
npx hardhat compile
# Then write a deploy script (see deploy_contract.py)
```

### Step 3: Start the Mint Server

Pick ONE machine to run the server (can be any laptop, or a cloud server).

```bash
cd server/
python server_app.py
```

Output:
```
=========================================================
  TRUSTED-BPI MINT SERVER
=========================================================
  RSA key size: 2048 bits
  Collateral ratio: 2.0x
  Database: mint_data.db
=========================================================

  Server starting on http://0.0.0.0:9000
```

**Note the IP address of this machine.** All wallets will connect to it.
If running on your laptop: use `ipconfig` (Windows) or `ifconfig` (Mac/Linux) to find your IP.

### Step 4: Start Wallet App on Laptop 1 (Alice)

```bash
cd wallet/
python wallet_app.py
```

The Tkinter app opens. On the login screen:
- **User ID**: `alice`
- **Mint Server**: `http://<SERVER_IP>:9000`
- **P2P Port**: `9001`
- Click **"New + Register"**

This will:
1. Download the Mint's RSA public key
2. Generate Alice's ECDSA key pair inside the TEE
3. Register Alice with the Mint server

### Step 5: Start Wallet App on Laptop 2 (Bob)

Same thing on the second laptop:

```bash
cd wallet/
python wallet_app.py
```

- **User ID**: `bob`
- **Mint Server**: `http://<SERVER_IP>:9000`
- **P2P Port**: `9001`
- Click **"New + Register"**

### Step 6: Alice Deposits Collateral & Mints Tokens

On Alice's app:
1. Click **"Connect to Server"** → Status changes to ONLINE
2. Click **"Deposit Collateral"** → Enter `1000`
   - Alice now has ₹1000 locked, can mint up to ₹500 in tokens
3. Click **"Mint Tokens"** → Enter `100,100,100,50,50`
   - 5 blind-signed tokens are created (Mint never sees the serials!)
   - Wallet shows ₹400 balance

### Step 7: OFFLINE PAYMENT — Alice Pays Bob

**Now disconnect both laptops from the internet** (turn off WiFi to the server).
They only need to be on the **same local network** (same WiFi, or direct cable).

On Alice's app:
1. Enter Bob's IP address (shown on Bob's login screen)
2. Port: `9001`
3. Amount: `100`
4. Click **"⚡ SEND PAYMENT"**

What happens:
```
Alice's App                              Bob's App
    │                                        │
    │──── TCP connect ──────────────────────>│
    │     "I want to pay ₹100"              │
    │                                        │
    │<─── challenge bits [0,1,0,1,0] ───────│
    │     (for cut-and-choose)               │
    │                                        │
    │  [TEE: delete token, increment         │
    │   counter, sign payment]               │
    │                                        │
    │──── signed payment ──────────────────>│
    │     (tokens + ECDSA sig + counter)     │
    │                                        │
    │                   [TEE: verify Mint's   │
    │                    blind signature,     │
    │                    verify Alice's sig]  │
    │                                        │
    │<─── "ACCEPTED" ───────────────────────│
    │                                        │
```

**No server was contacted!** This is a real peer-to-peer payment.

### Step 8: Settlement — Bob Comes Online

Bob reconnects to the internet and:
1. Click **"Connect to Server"**
2. Click **"Settle Payments"**

The Mint server:
- Verifies the blind signature (confirms the token is real)
- Checks for double-spending (is this serial already used?)
- Records the settlement
- In production: calls `settle()` on the smart contract

### Step 9: Double-Spend Demo (for presentation)

To show fraud detection:

1. Run a THIRD wallet with `user_id = evil_alice`
2. Deposit ₹1000, mint tokens
3. Pay Bob ₹100
4. **Without settling**, pay Umang ₹100 with same tokens
   (requires modifying the wallet to not delete tokens — use the
   MaliciousWallet class from the demo script)
5. When both Bob and Umang settle, the Mint detects the double-spend
6. Cut-and-choose reveals evil_alice's identity
7. Collateral is slashed

---

## How the Smart Contract Fits In

Currently the Mint server handles settlement in its SQLite database.
To connect it to the real smart contract:

### What the Contract Does

```
User deposits ETH  ──→  deposit()      ──→  Collateral locked on-chain
Mint issues tokens  ──→  recordIssuance() ──→  Tracks issued value
Bob settles         ──→  settle()       ──→  Transfers ETH from Alice to Bob
Fraud detected      ──→  reportDoubleSpend() ──→  Slashes cheater, pays victims
```

### Connecting Server to Contract

In `server_app.py`, add web3.py integration:

```python
from web3 import Web3

# Connect to Sepolia
w3 = Web3(Web3.HTTPProvider("https://sepolia.infura.io/v3/YOUR_KEY"))

# Load contract
contract_address = "0xYOUR_DEPLOYED_CONTRACT_ADDRESS"
contract_abi = json.load(open("contracts/TrustedBPI_abi.json"))
contract = w3.eth.contract(address=contract_address, abi=contract_abi)

# In the settle endpoint, after database update:
tx = contract.functions.settle(
    payment_id_bytes32,
    payer_address,
    payee_address,
    amount_wei,
    token_serial_bytes32
).build_transaction({
    'from': MINT_WALLET_ADDRESS,
    'nonce': w3.eth.get_transaction_count(MINT_WALLET_ADDRESS),
    'gas': 200000,
    'gasPrice': w3.eth.gas_price,
})
signed = w3.eth.account.sign_transaction(tx, MINT_PRIVATE_KEY)
tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
```

### How the Coins Work

These are NOT real cryptocurrencies. The flow is:

1. **Deposit**: User sends real ETH to the smart contract (like putting cash in a locker)
2. **Mint tokens**: Server issues blind-signed digital tokens worth ≤ 50% of deposit
3. **Offline payment**: Tokens transfer between devices (like handing someone a signed check)
4. **Settlement**: Server calls contract to move ETH from payer's locker to payee's balance
5. **Withdraw**: Payee calls `withdrawBalance()` to get real ETH out

The "coins" in this system are the blind-signed tokens — they represent a claim on the locked ETH. The Mint's signature is what makes them valuable (like a bank's stamp on a cheque).

---

## File Structure

```
trusted-bpi-app/
├── server/
│   ├── server_app.py       ← Run this on the server machine
│   └── database.py         ← SQLite storage
├── wallet/
│   └── wallet_app.py       ← Run this on each user's laptop
├── shared/
│   ├── tee_wallet.py       ← TEE hardware wallet simulator
│   ├── blind_signature.py  ← Chaumian blind signatures
│   ├── cut_and_choose.py   ← Identity reveal protocol
│   ├── token.py            ← Data structures
│   └── monotonic_counter.py← Hardware counter simulation
├── contracts/
│   └── TrustedBPI.sol      ← Deploy on Sepolia via Remix
└── SETUP_GUIDE.md          ← This file
```

## Live Demo Checklist

- [ ] Smart contract deployed on Sepolia
- [ ] Server running on one machine
- [ ] Wallet app running on Laptop 1 (Alice)
- [ ] Wallet app running on Laptop 2 (Bob)
- [ ] Alice registered and deposited collateral
- [ ] Alice minted blind-signed tokens
- [ ] Alice sends offline payment to Bob via TCP
- [ ] Bob verifies payment (offline, no server)
- [ ] Bob comes online and settles
- [ ] Show settlement on server/blockchain
- [ ] Demo double-spend attack and fraud detection
