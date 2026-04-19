#!/usr/bin/env python3
"""
Trusted-BPI Mint Server

Central server that runs online. Wallet apps connect to this only for:
  1. Registration & collateral deposit
  2. Blind token issuance
  3. Payment settlement (after coming back online)

The server NEVER participates in offline payments between users.

Usage:
    python server_app.py
    -> Starts on http://0.0.0.0:9000

    Set SERVER_HOST and SERVER_PORT env vars to customize.
"""

import os
import sys
import json
import time
import hashlib

# 1. Get the path to the root 'V2' folder by going one level up ('..') from this file
root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))

# 2. Add that root folder to Python's system path
sys.path.insert(0, root_dir)


from flask import Flask, request, jsonify
from shared.blind_signature import BlindSignatureScheme
from shared.cut_and_choose import IdentityEmbedding
from database import MintDatabase

# Web3 for smart contract interaction
try:
    from web3 import Web3
    WEB3_AVAILABLE = True
except ImportError:
    WEB3_AVAILABLE = False
    print("[WARN] web3 not installed. Running without on-chain settlement.")
    print("[WARN] Install with: pip install web3")

app = Flask(__name__)

# ===========================================
# MINT STATE
# ===========================================

RSA_KEY_SIZE = 2048
COLLATERAL_RATIO = 2.0

blind_scheme = BlindSignatureScheme(key_size=RSA_KEY_SIZE)
db = MintDatabase(db_path="mint_data.db")

# ===========================================
# BLOCKCHAIN CONNECTION (Sepolia Testnet)
# ===========================================

INFURA_URL = "https://sepolia.infura.io/v3/63a484426e114166bc47ff9278cb253a"
CONTRACT_ADDRESS = "0x0a8d2843CA24Edc56052820236a3BcB6E4bEBc84"
MINT_PRIVATE_KEY = "bc7d880b3c5abeb66b200e71ea304e9a84e5f5fbc2b9cedf8329a13cc7ed7355"

# Smart contract ABI (only the functions we call)
CONTRACT_ABI = [
    {"inputs":[{"name":"paymentId","type":"bytes32"},{"name":"payer","type":"address"},{"name":"payee","type":"address"},{"name":"amount","type":"uint256"},{"name":"tokenSerial","type":"bytes32"}],"name":"settle","outputs":[],"stateMutability":"nonpayable","type":"function"},
    {"inputs":[{"name":"tokenSerial","type":"bytes32"},{"name":"cheater","type":"address"},{"name":"payment1Hash","type":"bytes32"},{"name":"payment2Hash","type":"bytes32"},{"name":"victim1","type":"address"},{"name":"victim2","type":"address"},{"name":"tokenValue","type":"uint256"}],"name":"reportDoubleSpend","outputs":[],"stateMutability":"nonpayable","type":"function"},
    {"inputs":[],"name":"getSystemStats","outputs":[{"name":"_totalCollateral","type":"uint256"},{"name":"_totalSettled","type":"uint256"},{"name":"_totalFrauds","type":"uint256"}],"stateMutability":"view","type":"function"},
    {"inputs":[{"name":"user","type":"address"}],"name":"getAccountInfo","outputs":[{"name":"collateral","type":"uint256"},{"name":"tokensIssued","type":"uint256"},{"name":"balance","type":"uint256"},{"name":"registered","type":"bool"},{"name":"slashed","type":"bool"}],"stateMutability":"view","type":"function"},
    {"anonymous":False,"inputs":[{"indexed":True,"name":"paymentId","type":"bytes32"},{"indexed":True,"name":"payer","type":"address"},{"indexed":True,"name":"payee","type":"address"},{"indexed":False,"name":"amount","type":"uint256"}],"name":"PaymentSettled","type":"event"},
    {"anonymous":False,"inputs":[{"indexed":True,"name":"tokenSerial","type":"bytes32"},{"indexed":True,"name":"cheater","type":"address"},{"indexed":False,"name":"amountSlashed","type":"uint256"}],"name":"DoubleSpendDetected","type":"event"},
]

w3 = None
contract = None
mint_account = None

if WEB3_AVAILABLE:
    try:
        w3 = Web3(Web3.HTTPProvider(INFURA_URL))
        if w3.is_connected():
            mint_account = w3.eth.account.from_key(MINT_PRIVATE_KEY)
            contract = w3.eth.contract(address=CONTRACT_ADDRESS, abi=CONTRACT_ABI)
            balance = w3.eth.get_balance(mint_account.address)
            print(f"  Blockchain: Connected to Sepolia")
            print(f"  Contract:   {CONTRACT_ADDRESS}")
            print(f"  Mint wallet: {mint_account.address}")
            print(f"  Mint balance: {w3.from_wei(balance, 'ether')} ETH")
        else:
            print("[WARN] Cannot connect to Sepolia. Running off-chain only.")
            w3 = None
    except Exception as e:
        print(f"[WARN] Web3 setup failed: {e}. Running off-chain only.")
        w3 = None

print("=" * 55)
print("  TRUSTED-BPI MINT SERVER")
print("=" * 55)
print(f"  RSA key size: {RSA_KEY_SIZE} bits")
print(f"  Collateral ratio: {COLLATERAL_RATIO}x")
print(f"  Database: mint_data.db")
print(f"  On-chain: {'YES [v]' if w3 and contract else 'NO (off-chain mode)'}")
print("=" * 55)


def _call_contract_settle(payment_id: str, payer_addr: str, payee_addr: str,
                           amount_wei: int, token_serial: str) -> str:
    """Call settle() on the smart contract. Returns tx hash or None."""
    if not w3 or not contract:
        return None

    try:
        # Convert strings to bytes32
        pid_bytes = Web3.keccak(text=payment_id)
        serial_bytes = Web3.keccak(text=token_serial)

        tx = contract.functions.settle(
            pid_bytes,
            Web3.to_checksum_address(payer_addr),
            Web3.to_checksum_address(payee_addr),
            amount_wei,
            serial_bytes
        ).build_transaction({
            'from': mint_account.address,
            'nonce': w3.eth.get_transaction_count(mint_account.address),
            'gas': 200000,
            'gasPrice': w3.eth.gas_price,
        })

        signed = w3.eth.account.sign_transaction(tx, MINT_PRIVATE_KEY)
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        print(f"[CHAIN] Settlement tx sent: {tx_hash.hex()}")
        print(f"[CHAIN] View: https://sepolia.etherscan.io/tx/{tx_hash.hex()}")
        return tx_hash.hex()
    except Exception as e:
        print(f"[CHAIN] Settlement tx failed: {e}")
        return None


def _call_contract_report_fraud(token_serial: str, cheater_addr: str,
                                  p1_id: str, p2_id: str,
                                  victim1_addr: str, victim2_addr: str,
                                  token_value_wei: int) -> str:
    """Call reportDoubleSpend() on the smart contract. Returns tx hash or None."""
    if not w3 or not contract:
        return None

    try:
        serial_bytes = Web3.keccak(text=token_serial)
        p1_bytes = Web3.keccak(text=p1_id)
        p2_bytes = Web3.keccak(text=p2_id)

        tx = contract.functions.reportDoubleSpend(
            serial_bytes,
            Web3.to_checksum_address(cheater_addr),
            p1_bytes, p2_bytes,
            Web3.to_checksum_address(victim1_addr),
            Web3.to_checksum_address(victim2_addr),
            token_value_wei
        ).build_transaction({
            'from': mint_account.address,
            'nonce': w3.eth.get_transaction_count(mint_account.address),
            'gas': 300000,
            'gasPrice': w3.eth.gas_price,
        })

        signed = w3.eth.account.sign_transaction(tx, MINT_PRIVATE_KEY)
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        print(f"[CHAIN] !! Fraud report tx: {tx_hash.hex()}")
        print(f"[CHAIN] View: https://sepolia.etherscan.io/tx/{tx_hash.hex()}")
        return tx_hash.hex()
    except Exception as e:
        print(f"[CHAIN] Fraud report tx failed: {e}")
        return None


# ===========================================
# API ROUTES
# ===========================================

@app.route('/api/info', methods=['GET'])
def server_info():
    """Get server info and public key."""
    pub = blind_scheme.get_public_key()
    return jsonify({
        "server": "Trusted-BPI Mint v1",
        "mint_public_key": {"n": str(pub["n"]), "e": pub["e"]},
        "collateral_ratio": COLLATERAL_RATIO,
    })


@app.route('/api/register', methods=['POST'])
def register():
    """Register a user with their public key."""
    data = request.json
    user_id = data["user_id"]
    public_key = data["public_key"]

    try:
        db.register_user(user_id, public_key)
        print(f"[MINT] Registered: {user_id} ({public_key[:20]}...)")
        return jsonify({"success": True, "user_id": user_id})
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@app.route('/api/deposit', methods=['POST'])
def deposit():
    """Lock collateral for a user."""
    data = request.json
    user_id = data["user_id"]
    amount = float(data["amount"])

    try:
        result = db.lock_collateral(user_id, amount)
        available = db.get_available_issuance(user_id)
        print(f"[MINT] Deposit: {user_id} locked Rs.{amount}, available for tokens: Rs.{available}")
        return jsonify({
            "success": True,
            "collateral_locked": result["collateral_locked"],
            "available_for_tokens": available,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@app.route('/api/mint-token', methods=['POST'])
def mint_token():
    """Blind-sign a token. Mint does NOT see the serial."""
    data = request.json
    user_id = data["user_id"]
    blinded_message = int(data["blinded_message"])
    denomination = float(data["denomination"])

    try:
        available = db.get_available_issuance(user_id)
        if denomination > available:
            return jsonify({
                "error": f"Insufficient collateral. Available: Rs.{available}, Requested: Rs.{denomination}"
            }), 400

        db.record_token_issuance(user_id, str(blinded_message), denomination)
        blind_sig = blind_scheme.sign_blinded(blinded_message)

        print(f"[MINT] Blind-signed Rs.{denomination} for {user_id} (serial UNKNOWN to mint)")
        return jsonify({
            "success": True,
            "blind_signature": str(blind_sig),
            "denomination": denomination,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@app.route('/api/settle', methods=['POST'])
def settle():
    """
    Settle a payment - receiver submits after coming online.
    Checks for double-spending via spent serial database.
    """
    data = request.json
    payment = data["payment"]
    submitted_by = data["submitted_by"]

    results = {
        "payment_id": payment["payment_id"],
        "settled_tokens": [],
        "double_spend_detected": [],
        "total_settled": 0,
        "fraud_detected": False,
    }

    for token in payment["tokens"]:
        serial = token["serial"]
        denomination = token["denomination"]
        msg_hash = token["message_hash"]
        sig = token["signature"]

        # Verify mint's signature
        if not blind_scheme.verify_signature(msg_hash, sig):
            print(f"[MINT] [x] Invalid signature on {serial[:8]}")
            continue

        # Check double-spend
        check = db.check_serial_spent(serial)

        if check["spent"]:
            print(f"[MINT] !! DOUBLE SPEND: serial {serial[:8]}")

            # Recover identity via cut-and-choose
            first_data = json.loads(check["record"]["payment_data"])
            fraud_result = _detect_cheater(first_data, payment, serial)

            results["double_spend_detected"].append({
                "serial": serial,
                "denomination": denomination,
                "fraud_result": fraud_result,
            })
            results["fraud_detected"] = True
            results["total_settled"] += denomination
            results["settled_tokens"].append(serial)
        else:
            db.record_serial_spent(serial, denomination, submitted_by,
                                    payment["payment_id"], payment)
            results["settled_tokens"].append(serial)
            results["total_settled"] += denomination
            print(f"[MINT] [v] Settled {serial[:8]} (Rs.{denomination})")

    # Record settlement in database
    db.record_settlement(
        payment["payment_id"], payment["payer_pubkey"],
        payment["payee_pubkey"], results["total_settled"],
        results["settled_tokens"],
    )

    # -- On-chain settlement (Sepolia) --
    if results["total_settled"] > 0 and not results["fraud_detected"]:
        # Convert amount to wei (1 unit = 1 wei for demo purposes)
        amount_wei = int(results["total_settled"])
        for serial in results["settled_tokens"]:
            tx_hash = _call_contract_settle(
                payment["payment_id"],
                payment["payer_pubkey"],
                payment["payee_pubkey"],
                amount_wei,
                serial,
            )
            if tx_hash:
                results["tx_hash"] = tx_hash
                results["etherscan"] = f"https://sepolia.etherscan.io/tx/{tx_hash}"

    if results["fraud_detected"]:
        for ds in results["double_spend_detected"]:
            fr = ds.get("fraud_result", {})
            if fr.get("identified"):
                cheater_user = fr["cheater"]
                try:
                    cheater_db = db.get_user(cheater_user)
                    cheater_addr = cheater_db["public_key"]
                except:
                    cheater_addr = payment["payer_pubkey"]

                tx_hash = _call_contract_report_fraud(
                    ds["serial"],
                    cheater_addr,
                    results["payment_id"],
                    ds["serial"],  # use serial as second ID
                    payment["payee_pubkey"],
                    submitted_by,
                    int(ds["denomination"]),
                )
                if tx_hash:
                    results["fraud_tx_hash"] = tx_hash
                    results["fraud_etherscan"] = f"https://sepolia.etherscan.io/tx/{tx_hash}"

    print(f"[MINT] Settlement complete: Rs.{results['total_settled']}, fraud={results['fraud_detected']}")
    return jsonify(results)


@app.route('/api/balance/<user_id>', methods=['GET'])
def get_balance(user_id):
    """Get user's on-chain balance info."""
    try:
        user = db.get_user(user_id)
        return jsonify({
            "user_id": user_id,
            "collateral_locked": user["collateral_locked"],
            "tokens_issued_value": user["tokens_issued_value"],
            "tokens_spent_value": user.get("tokens_spent_value", 0),
            "settled_balance": user["balance"],
            "status": user["status"],
        })
    except ValueError:
        return jsonify({"error": "User not found"}), 404


@app.route('/api/sender-settle', methods=['POST'])
def sender_settle():
    """
    Alice settles her own sent payment - push model.
    Bob gets credited even if Bob hasn't come online.
    If already settled (Bob settled first), returns already_settled=True.
    """
    data = request.json
    user_id = data["user_id"]
    payment_id = data["payment_id"]

    # Check if already settled
    existing = db.get_all_settlements()
    for s in existing:
        if s["payment_id"] == payment_id:
            print(f"[MINT] Payment {payment_id[:8]} already settled (dedup)")
            return jsonify({"success": True, "already_settled": True})

    # Find the payment in spent_serials by payment_id
    cursor = db.conn.cursor()
    cursor.execute("SELECT * FROM spent_serials WHERE payment_id = ?", (payment_id,))
    records = [dict(r) for r in cursor.fetchall()]

    if not records:
        # Payment not yet in system - the sender needs to submit the payment data
        # For sender-settle, the sender sends the payment from their history
        return jsonify({"error": "Payment not found. Pay first via P2P, then settle."}), 404

    # Already spent-serial recorded, just create the settlement record
    total = sum(r["denomination"] for r in records)
    first_record = json.loads(records[0]["payment_data"]) if records else {}
    payer_pubkey = first_record.get("payer_pubkey", "")
    payee_pubkey = first_record.get("payee_pubkey", "")
    serials = [r["serial"] for r in records]

    tx_hash = None
    for serial in serials:
        tx_hash = _call_contract_settle(
            payment_id, payer_pubkey, payee_pubkey,
            int(total), serial
        )

    db.record_settlement(payment_id, payer_pubkey, payee_pubkey, total, serials, tx_hash)
    print(f"[MINT] Sender-settled {payment_id[:8]} - Rs.{total} -> {payee_pubkey[:12]}...")

    return jsonify({
        "success": True,
        "total_settled": total,
        "tx_hash": tx_hash,
    })


@app.route('/api/withdraw-collateral', methods=['POST'])
def withdraw_collateral():
    """Withdraw unused collateral. Only the portion not backing outstanding tokens."""
    data = request.json
    user_id = data["user_id"]
    amount = float(data["amount"])

    try:
        user = db.get_user(user_id)
        collateral = user["collateral_locked"]
        issued = user["tokens_issued_value"]
        spent = user.get("tokens_spent_value", 0)
        outstanding = max(0, issued - spent)
        min_locked = outstanding * COLLATERAL_RATIO
        withdrawable = max(0, collateral - min_locked)

        if amount > withdrawable:
            return jsonify({
                "error": f"Cannot withdraw Rs.{amount}. Withdrawable: Rs.{withdrawable}"
            }), 400

        # Update database
        cursor = db.conn.cursor()
        cursor.execute(
            "UPDATE users SET collateral_locked = collateral_locked - ? WHERE user_id = ?",
            (amount, user_id)
        )
        db.conn.commit()

        # Call smart contract
        tx_hash = None
        if w3 and contract:
            try:
                tx = contract.functions.withdrawCollateral(
                    int(amount)
                ).build_transaction({
                    'from': mint_account.address,
                    'nonce': w3.eth.get_transaction_count(mint_account.address),
                    'gas': 200000,
                    'gasPrice': w3.eth.gas_price,
                })
                signed = w3.eth.account.sign_transaction(tx, MINT_PRIVATE_KEY)
                tx_hash_bytes = w3.eth.send_raw_transaction(signed.raw_transaction)
                tx_hash = tx_hash_bytes.hex()
                print(f"[CHAIN] Withdrawal tx: {tx_hash}")
            except Exception as e:
                print(f"[CHAIN] Withdrawal tx failed: {e}")

        print(f"[MINT] Withdrew Rs.{amount} for {user_id}")
        return jsonify({"success": True, "amount": amount, "tx_hash": tx_hash})

    except Exception as e:
        return jsonify({"error": str(e)}), 400


@app.route('/api/transactions', methods=['GET'])
def get_transactions():
    """Get all settlements."""
    return jsonify(db.get_all_settlements())


@app.route('/api/frauds', methods=['GET'])
def get_frauds():
    """Get all fraud records."""
    return jsonify(db.get_fraud_records())


@app.route('/api/stats', methods=['GET'])
def get_stats():
    """Get system stats including blockchain info."""
    cursor = db.conn.cursor()
    cursor.execute("SELECT COUNT(*) as c FROM users")
    users = cursor.fetchone()["c"]
    cursor.execute("SELECT COUNT(*) as c FROM spent_serials")
    serials = cursor.fetchone()["c"]
    cursor.execute("SELECT COUNT(*) as c FROM fraud_records")
    frauds = cursor.fetchone()["c"]
    cursor.execute("SELECT COUNT(*) as c FROM settlements")
    settlements = cursor.fetchone()["c"]

    result = {
        "users": users,
        "tokens_settled": serials,
        "frauds_detected": frauds,
        "settlements": settlements,
        "blockchain_connected": w3 is not None and w3.is_connected() if w3 else False,
        "contract_address": CONTRACT_ADDRESS,
        "etherscan": f"https://sepolia.etherscan.io/address/{CONTRACT_ADDRESS}",
    }

    if w3 and contract:
        try:
            stats = contract.functions.getSystemStats().call()
            result["on_chain_collateral"] = str(stats[0])
            result["on_chain_settled"] = str(stats[1])
            result["on_chain_frauds"] = str(stats[2])
        except Exception as e:
            result["on_chain_error"] = str(e)

    return jsonify(result)


# -- Internal helpers --

def _detect_cheater(payment1: dict, payment2: dict, serial: str) -> dict:
    r1 = payment1.get("identity_responses", [])
    r2 = payment2.get("identity_responses", [])

    if not r1 or not r2:
        return {"identified": False, "reason": "No identity responses"}

    detection = IdentityEmbedding.detect_double_spend(r1, r2)

    if detection["detected"]:
        recovered_hash = detection["recovered_identity_hash"]
        cheater_user_id = None

        cursor = db.conn.cursor()
        cursor.execute("SELECT user_id FROM users")
        for row in cursor.fetchall():
            if IdentityEmbedding.match_identity(recovered_hash, row["user_id"]):
                cheater_user_id = row["user_id"]
                break

        if cheater_user_id:
            print(f"[MINT] !! CHEATER IDENTIFIED: {cheater_user_id}")
            token_denom = payment1["tokens"][0]["denomination"] if payment1.get("tokens") else 0
            slash_amount = token_denom * 2

            db.record_fraud(serial, recovered_hash, cheater_user_id,
                           payment1.get("payment_id", ""), payment2.get("payment_id", ""),
                           payment1, payment2, slash_amount)

            return {"identified": True, "cheater": cheater_user_id, "slashed": slash_amount}

    return {"identified": False, "detail": detection.get("detail", "")}


if __name__ == '__main__':
    host = os.environ.get("SERVER_HOST", "0.0.0.0")
    port = int(os.environ.get("SERVER_PORT", 9000))
    print(f"\n  Server starting on http://{host}:{port}")
    print(f"  Wallet apps connect to this address\n")
    app.run(host=host, port=port, debug=False)
