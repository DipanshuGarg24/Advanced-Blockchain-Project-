"""
Database Layer for the Mint Server

Manages:
- User accounts and their collateral balances
- Issued token tracking (blinded messages only — Mint never sees serials)
- Spent serial tracking for double-spend detection
- Fraud records
"""

import sqlite3
import time
import json
import os


class MintDatabase:
    """SQLite-backed database for the Trusted-BPI Mint server."""

    def __init__(self, db_path: str = ":memory:"):
        """
        Initialize database.
        
        Args:
            db_path: Path to SQLite file, or ":memory:" for in-memory DB
        """
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._create_tables()

    def _create_tables(self):
        """Create all required tables."""
        cursor = self.conn.cursor()

        cursor.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                user_id TEXT PRIMARY KEY,
                public_key TEXT NOT NULL,
                collateral_locked REAL DEFAULT 0.0,
                tokens_issued_value REAL DEFAULT 0.0,
                tokens_spent_value REAL DEFAULT 0.0,
                balance REAL DEFAULT 0.0,
                status TEXT DEFAULT 'active',
                created_at INTEGER DEFAULT 0,
                updated_at INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS issued_tokens (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                blinded_message TEXT NOT NULL,
                denomination REAL NOT NULL,
                issued_at INTEGER NOT NULL,
                status TEXT DEFAULT 'active',
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            );

            CREATE TABLE IF NOT EXISTS spent_serials (
                serial TEXT PRIMARY KEY,
                denomination REAL NOT NULL,
                submitted_by TEXT NOT NULL,
                payment_id TEXT NOT NULL,
                payment_data TEXT NOT NULL,
                settled_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS fraud_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                serial TEXT NOT NULL,
                cheater_identity_hash TEXT,
                cheater_user_id TEXT,
                payment1_id TEXT NOT NULL,
                payment2_id TEXT NOT NULL,
                payment1_data TEXT NOT NULL,
                payment2_data TEXT NOT NULL,
                amount_slashed REAL NOT NULL,
                detected_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS settlements (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                payment_id TEXT UNIQUE NOT NULL,
                payer_pubkey TEXT NOT NULL,
                payee_pubkey TEXT NOT NULL,
                amount REAL NOT NULL,
                token_serials TEXT NOT NULL,
                status TEXT DEFAULT 'pending',
                tx_hash TEXT,
                settled_at INTEGER
            );
        """)
        self.conn.commit()

    # ---- User Management ----

    def register_user(self, user_id: str, public_key: str) -> dict:
        """Register a new user."""
        now = int(time.time())
        cursor = self.conn.cursor()
        cursor.execute(
            "INSERT INTO users (user_id, public_key, created_at, updated_at) "
            "VALUES (?, ?, ?, ?)",
            (user_id, public_key, now, now)
        )
        self.conn.commit()
        return {"user_id": user_id, "public_key": public_key}

    def get_user(self, user_id: str) -> dict:
        """Get user by ID."""
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
        row = cursor.fetchone()
        if row is None:
            raise ValueError(f"User {user_id} not found")
        return dict(row)

    def get_user_by_pubkey(self, public_key: str) -> dict:
        """Get user by public key."""
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT * FROM users WHERE LOWER(public_key) = LOWER(?)",
            (public_key,)
        )
        row = cursor.fetchone()
        if row is None:
            raise ValueError(f"User with pubkey {public_key[:16]}... not found")
        return dict(row)

    def lock_collateral(self, user_id: str, amount: float) -> dict:
        """Lock collateral for a user (simulating on-chain deposit)."""
        user = self.get_user(user_id)
        new_collateral = user["collateral_locked"] + amount
        cursor = self.conn.cursor()
        cursor.execute(
            "UPDATE users SET collateral_locked = ?, updated_at = ? WHERE user_id = ?",
            (new_collateral, int(time.time()), user_id)
        )
        self.conn.commit()
        return {"user_id": user_id, "collateral_locked": new_collateral}

    def get_available_issuance(self, user_id: str) -> float:
        """
        Calculate how many more tokens can be issued.
        Enforces 2x collateral ratio: tokens_issued <= collateral / 2
        """
        user = self.get_user(user_id)
        max_issuance = user["collateral_locked"] / 2.0
        available = max_issuance - user["tokens_issued_value"]
        return max(0, available)

    def record_token_issuance(self, user_id: str, blinded_message: str,
                               denomination: float) -> bool:
        """Record that a token was issued to a user."""
        available = self.get_available_issuance(user_id)
        if denomination > available:
            raise ValueError(
                f"Insufficient collateral. Available: {available}, Requested: {denomination}"
            )

        cursor = self.conn.cursor()
        now = int(time.time())

        cursor.execute(
            "INSERT INTO issued_tokens (user_id, blinded_message, denomination, issued_at) "
            "VALUES (?, ?, ?, ?)",
            (user_id, blinded_message, denomination, now)
        )

        cursor.execute(
            "UPDATE users SET tokens_issued_value = tokens_issued_value + ?, updated_at = ? "
            "WHERE user_id = ?",
            (denomination, now, user_id)
        )
        self.conn.commit()
        return True

    # ---- Settlement ----

    def check_serial_spent(self, serial: str) -> dict:
        """Check if a serial has already been spent."""
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM spent_serials WHERE serial = ?", (serial,))
        row = cursor.fetchone()
        if row:
            return {"spent": True, "record": dict(row)}
        return {"spent": False, "record": None}

    def record_serial_spent(self, serial: str, denomination: float,
                             submitted_by: str, payment_id: str,
                             payment_data: dict) -> bool:
        """Record a serial as spent during settlement."""
        cursor = self.conn.cursor()
        cursor.execute(
            "INSERT INTO spent_serials (serial, denomination, submitted_by, "
            "payment_id, payment_data, settled_at) VALUES (?, ?, ?, ?, ?, ?)",
            (serial, denomination, submitted_by, payment_id,
             json.dumps(payment_data), int(time.time()))
        )
        self.conn.commit()
        return True

    def record_settlement(self, payment_id: str, payer_pubkey: str,
                           payee_pubkey: str, amount: float,
                           token_serials: list, tx_hash: str = None) -> bool:
        """Record a completed settlement."""
        cursor = self.conn.cursor()
        cursor.execute(
            "INSERT INTO settlements (payment_id, payer_pubkey, payee_pubkey, "
            "amount, token_serials, status, tx_hash, settled_at) "
            "VALUES (?, ?, ?, ?, ?, 'completed', ?, ?)",
            (payment_id, payer_pubkey, payee_pubkey, amount,
             json.dumps(token_serials), tx_hash, int(time.time()))
        )

        # Credit receiver's balance
        try:
            receiver = self.get_user_by_pubkey(payee_pubkey)
            cursor.execute(
                "UPDATE users SET balance = balance + ?, updated_at = ? WHERE user_id = ?",
                (amount, int(time.time()), receiver["user_id"])
            )
        except ValueError:
            pass  # Receiver not registered yet

        self.conn.commit()
        return True

    # ---- Fraud Detection ----

    def record_fraud(self, serial: str, cheater_identity_hash: str,
                     cheater_user_id: str, payment1_id: str, payment2_id: str,
                     payment1_data: dict, payment2_data: dict,
                     amount_slashed: float) -> dict:
        """Record a detected double-spend fraud."""
        cursor = self.conn.cursor()
        now = int(time.time())

        cursor.execute(
            "INSERT INTO fraud_records (serial, cheater_identity_hash, "
            "cheater_user_id, payment1_id, payment2_id, payment1_data, "
            "payment2_data, amount_slashed, detected_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (serial, cheater_identity_hash, cheater_user_id,
             payment1_id, payment2_id,
             json.dumps(payment1_data), json.dumps(payment2_data),
             amount_slashed, now)
        )

        # Slash the cheater's collateral
        if cheater_user_id:
            cursor.execute(
                "UPDATE users SET collateral_locked = collateral_locked - ?, "
                "status = 'slashed', updated_at = ? WHERE user_id = ?",
                (amount_slashed, now, cheater_user_id)
            )

        self.conn.commit()
        return {
            "serial": serial,
            "cheater": cheater_user_id,
            "amount_slashed": amount_slashed,
        }

    def get_fraud_records(self) -> list:
        """Get all fraud records."""
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM fraud_records ORDER BY detected_at DESC")
        return [dict(row) for row in cursor.fetchall()]

    def get_all_settlements(self) -> list:
        """Get all settlements."""
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM settlements ORDER BY settled_at DESC")
        return [dict(row) for row in cursor.fetchall()]

    def close(self):
        """Close the database connection."""
        self.conn.close()