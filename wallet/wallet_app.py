#!/usr/bin/env python3
"""
Trusted-BPI Desktop Wallet Application

A real desktop app that:
  - Connects to the Mint server (when ONLINE) for registration, deposits, minting, settlement
  - Connects DIRECTLY to another wallet app via TCP sockets (peer-to-peer) for OFFLINE payments
  - All crypto operations happen inside the simulated TEE

Usage:
    python wallet_app.py

    On Laptop 1 (Alice):  python wallet_app.py
    On Laptop 2 (Bob):    python wallet_app.py

    The app has two modes:
      ONLINE  -> talks to Mint server (deposit, mint tokens, settle)
      OFFLINE -> talks directly to peer wallet via TCP (send/receive payments)

Network:
    P2P Port: 9001 (configurable)
    Server:   http://<server-ip>:9000
"""

import os
import sys
import json
import time
import socket
import threading
import hashlib
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog
import requests

# 1. Get the path to the root 'V2' folder by going one level up ('..') from this file
root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))

# 2. Add that root folder to Python's system path
sys.path.insert(0, root_dir)


from shared.tee_wallet import TEEWallet
from shared.cut_and_choose import IdentityEmbedding

# ===========================================================
# CONFIG
# ===========================================================

DEFAULT_SERVER = "http://127.0.0.1:9000"
P2P_PORT = 9002
BUFFER_SIZE = 65536


class WalletApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Trusted-BPI Wallet")
        self.root.geometry("900x700")
        self.root.configure(bg="#0f1419")
        self.root.resizable(True, True)

        # State
        self.wallet: TEEWallet = None
        self.server_url = DEFAULT_SERVER
        self.is_online = False
        self.p2p_server_thread = None
        self.p2p_listening = False
        self.my_ip = self._get_local_ip()

        # Build UI
        self._build_styles()
        self._build_login_screen()

    # ===========================================
    # STYLES
    # ===========================================

    def _build_styles(self):
        self.BG = "#0f1419"
        self.CARD = "#1a2332"
        self.CARD2 = "#212d3b"
        self.BORDER = "#2d3f54"
        self.TEXT = "#e2e8f0"
        self.DIM = "#7a8a9e"
        self.GREEN = "#10b981"
        self.RED = "#ef4444"
        self.BLUE = "#3b82f6"
        self.YELLOW = "#f59e0b"
        self.FONT = ("Consolas", 11)
        self.FONT_BOLD = ("Consolas", 11, "bold")
        self.FONT_BIG = ("Consolas", 18, "bold")
        self.FONT_TITLE = ("Consolas", 14, "bold")
        self.FONT_SMALL = ("Consolas", 9)

    # ===========================================
    # LOGIN SCREEN
    # ===========================================

    def _build_login_screen(self):
        self.login_frame = tk.Frame(self.root, bg=self.BG)
        self.login_frame.pack(fill="both", expand=True)

        # Center container
        center = tk.Frame(self.login_frame, bg=self.CARD, padx=40, pady=40,
                          highlightbackground=self.BORDER, highlightthickness=1)
        center.place(relx=0.5, rely=0.5, anchor="center")

        tk.Label(center, text="TRUSTED-BPI", font=("Consolas", 24, "bold"),
                 fg=self.GREEN, bg=self.CARD).pack(pady=(0, 5))
        tk.Label(center, text="Offline Payment Wallet", font=self.FONT,
                 fg=self.DIM, bg=self.CARD).pack(pady=(0, 30))

        # User ID
        tk.Label(center, text="Your User ID:", font=self.FONT_BOLD,
                 fg=self.TEXT, bg=self.CARD, anchor="w").pack(fill="x")
        self.user_id_entry = tk.Entry(center, font=self.FONT, bg=self.CARD2,
                                       fg=self.TEXT, insertbackground=self.TEXT,
                                       relief="flat", bd=8)
        self.user_id_entry.pack(fill="x", pady=(5, 15))
        self.user_id_entry.insert(0, "alice")

        # Server URL
        tk.Label(center, text="Mint Server:", font=self.FONT_BOLD,
                 fg=self.TEXT, bg=self.CARD, anchor="w").pack(fill="x")
        self.server_entry = tk.Entry(center, font=self.FONT, bg=self.CARD2,
                                      fg=self.TEXT, insertbackground=self.TEXT,
                                      relief="flat", bd=8)
        self.server_entry.pack(fill="x", pady=(5, 15))
        self.server_entry.insert(0, DEFAULT_SERVER)

        # P2P Port
        tk.Label(center, text="P2P Listen Port:", font=self.FONT_BOLD,
                 fg=self.TEXT, bg=self.CARD, anchor="w").pack(fill="x")
        self.port_entry = tk.Entry(center, font=self.FONT, bg=self.CARD2,
                                    fg=self.TEXT, insertbackground=self.TEXT,
                                    relief="flat", bd=8)
        self.port_entry.pack(fill="x", pady=(5, 20))
        self.port_entry.insert(0, str(P2P_PORT))

        # Buttons
        btn_frame = tk.Frame(center, bg=self.CARD)
        btn_frame.pack(fill="x")

        tk.Button(btn_frame, text="Open Wallet", font=self.FONT_BOLD,
                  bg=self.GREEN, fg="#000", relief="flat", padx=20, pady=10,
                  command=self._open_wallet, cursor="hand2").pack(side="left", expand=True, fill="x", padx=(0, 5))

        tk.Button(btn_frame, text="New + Register", font=self.FONT_BOLD,
                  bg=self.BLUE, fg="#fff", relief="flat", padx=20, pady=10,
                  command=self._register_and_open, cursor="hand2").pack(side="left", expand=True, fill="x", padx=(5, 0))

        # Info
        tk.Label(center, text=f"Your IP: {self.my_ip}", font=self.FONT_SMALL,
                 fg=self.DIM, bg=self.CARD).pack(pady=(15, 0))

    # ===========================================
    # MAIN WALLET SCREEN
    # ===========================================

    def _build_main_screen(self):
        self.main_frame = tk.Frame(self.root, bg=self.BG)
        self.main_frame.pack(fill="both", expand=True)

        # -- Top bar --
        top = tk.Frame(self.main_frame, bg=self.CARD, pady=10, padx=15)
        top.pack(fill="x")

        tk.Label(top, text=f"TRUSTED-BPI", font=self.FONT_TITLE,
                 fg=self.GREEN, bg=self.CARD).pack(side="left")

        self.status_label = tk.Label(top, text="* OFFLINE", font=self.FONT_BOLD,
                                      fg=self.RED, bg=self.CARD)
        self.status_label.pack(side="right")

        self.user_label = tk.Label(top, text=f"User: {self.wallet.user_id}  |  {self.my_ip}:{self.p2p_port}",
                                    font=self.FONT_SMALL, fg=self.DIM, bg=self.CARD)
        self.user_label.pack(side="right", padx=20)

        # -- Balance cards --
        cards = tk.Frame(self.main_frame, bg=self.BG, pady=10, padx=10)
        cards.pack(fill="x")

        self.balance_label = self._make_card(cards, "WALLET BALANCE", "Rs.0", self.GREEN)
        self.balance_label.pack(side="left", expand=True, fill="x", padx=5)

        self.token_count_label = self._make_card(cards, "TOKENS", "0", self.BLUE)
        self.token_count_label.pack(side="left", expand=True, fill="x", padx=5)

        self.counter_label = self._make_card(cards, "COUNTER", "0", self.YELLOW)
        self.counter_label.pack(side="left", expand=True, fill="x", padx=5)

        self.pending_label = self._make_card(cards, "PENDING IN", "0", self.DIM)
        self.pending_label.pack(side="left", expand=True, fill="x", padx=5)

        # -- Notebook (tabs) --
        style = ttk.Style()
        style.theme_use('default')
        style.configure("TNotebook", background=self.BG, borderwidth=0)
        style.configure("TNotebook.Tab", background=self.CARD2, foreground=self.TEXT,
                        padding=[15, 8], font=self.FONT_BOLD)
        style.map("TNotebook.Tab", background=[("selected", self.CARD)])

        nb = ttk.Notebook(self.main_frame)
        nb.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        # Tab 1: Actions
        self.actions_tab = tk.Frame(nb, bg=self.BG)
        nb.add(self.actions_tab, text="  Actions  ")
        self._build_actions_tab()

        # Tab 2: Tokens
        self.tokens_tab = tk.Frame(nb, bg=self.BG)
        nb.add(self.tokens_tab, text="  Tokens  ")
        self._build_tokens_tab()

        # Tab 3: History
        self.history_tab = tk.Frame(nb, bg=self.BG)
        nb.add(self.history_tab, text="  History  ")
        self._build_history_tab()

        # Tab 4: Log
        self.log_tab = tk.Frame(nb, bg=self.BG)
        nb.add(self.log_tab, text="  Log  ")
        self._build_log_tab()

        # Tab 5: TEE Hardware Log
        self.tee_log_tab = tk.Frame(nb, bg=self.BG)
        nb.add(self.tee_log_tab, text="  TEE Log  ")
        self._build_tee_log_tab()

        # Start P2P listener
        self._start_p2p_listener()

        # Initial refresh
        self._refresh_ui()

    def _make_card(self, parent, title, value, color):
        frame = tk.Frame(parent, bg=self.CARD, padx=15, pady=12,
                         highlightbackground=self.BORDER, highlightthickness=1)
        tk.Label(frame, text=title, font=self.FONT_SMALL, fg=self.DIM, bg=self.CARD).pack(anchor="w")
        val_label = tk.Label(frame, text=value, font=self.FONT_BIG, fg=color, bg=self.CARD)
        val_label.pack(anchor="w")
        frame._val_label = val_label
        return frame

    # -- Actions Tab --

    def _build_actions_tab(self):
        f = self.actions_tab

        # Online section
        online_frame = tk.LabelFrame(f, text=" ONLINE - Mint Server ", font=self.FONT_BOLD,
                                      bg=self.BG, fg=self.BLUE, padx=15, pady=10)
        online_frame.pack(fill="x", padx=10, pady=(10, 5))

        btn_row1 = tk.Frame(online_frame, bg=self.BG)
        btn_row1.pack(fill="x", pady=5)

        tk.Button(btn_row1, text="Connect to Server", font=self.FONT, bg=self.BLUE,
                  fg="#fff", relief="flat", padx=15, pady=8,
                  command=self._connect_server, cursor="hand2").pack(side="left", padx=5)

        tk.Button(btn_row1, text="Deposit Collateral", font=self.FONT, bg=self.CARD2,
                  fg=self.TEXT, relief="flat", padx=15, pady=8,
                  command=self._deposit_collateral, cursor="hand2").pack(side="left", padx=5)

        tk.Button(btn_row1, text="Mint Tokens", font=self.FONT, bg=self.CARD2,
                  fg=self.TEXT, relief="flat", padx=15, pady=8,
                  command=self._mint_tokens, cursor="hand2").pack(side="left", padx=5)

        tk.Button(btn_row1, text="Settle Payments", font=self.FONT, bg=self.CARD2,
                  fg=self.TEXT, relief="flat", padx=15, pady=8,
                  command=self._settle_all, cursor="hand2").pack(side="left", padx=5)

        tk.Button(btn_row1, text="Check Balance", font=self.FONT, bg=self.CARD2,
                  fg=self.TEXT, relief="flat", padx=15, pady=8,
                  command=self._check_server_balance, cursor="hand2").pack(side="left", padx=5)

        btn_row2 = tk.Frame(online_frame, bg=self.BG)
        btn_row2.pack(fill="x", pady=5)

        tk.Button(btn_row2, text="Settle Sent Payments", font=self.FONT, bg="#6366f1",
                  fg="#fff", relief="flat", padx=15, pady=8,
                  command=self._sender_settle, cursor="hand2").pack(side="left", padx=5)

        tk.Button(btn_row2, text="Withdraw Collateral", font=self.FONT, bg=self.YELLOW,
                  fg="#000", relief="flat", padx=15, pady=8,
                  command=self._withdraw_collateral, cursor="hand2").pack(side="left", padx=5)

       

        # Offline section
        offline_frame = tk.LabelFrame(f, text=" OFFLINE - Peer-to-Peer Payment ", font=self.FONT_BOLD,
                                       bg=self.BG, fg=self.GREEN, padx=15, pady=10)
        offline_frame.pack(fill="x", padx=10, pady=5)

        pay_row = tk.Frame(offline_frame, bg=self.BG)
        pay_row.pack(fill="x", pady=5)

        tk.Label(pay_row, text="Peer IP:", font=self.FONT, fg=self.TEXT, bg=self.BG).pack(side="left")
        self.peer_ip_entry = tk.Entry(pay_row, font=self.FONT, bg=self.CARD2, fg=self.TEXT,
                                       insertbackground=self.TEXT, relief="flat", bd=5, width=18)
        self.peer_ip_entry.pack(side="left", padx=5)
        self.peer_ip_entry.insert(0, "127.0.0.1")

        tk.Label(pay_row, text="Port:", font=self.FONT, fg=self.TEXT, bg=self.BG).pack(side="left")
        self.peer_port_entry = tk.Entry(pay_row, font=self.FONT, bg=self.CARD2, fg=self.TEXT,
                                         insertbackground=self.TEXT, relief="flat", bd=5, width=6)
        self.peer_port_entry.pack(side="left", padx=5)
        self.peer_port_entry.insert(0, str(P2P_PORT))

        tk.Label(pay_row, text="Amount:", font=self.FONT, fg=self.TEXT, bg=self.BG).pack(side="left", padx=(10, 0))
        self.amount_entry = tk.Entry(pay_row, font=self.FONT, bg=self.CARD2, fg=self.TEXT,
                                      insertbackground=self.TEXT, relief="flat", bd=5, width=8)
        self.amount_entry.pack(side="left", padx=5)
        self.amount_entry.insert(0, "100")

        tk.Button(pay_row, text=" SEND PAYMENT", font=self.FONT_BOLD, bg=self.GREEN,
                  fg="#000", relief="flat", padx=20, pady=8,
                  command=self._send_payment, cursor="hand2").pack(side="left", padx=10)

        # Listening info
        self.listen_label = tk.Label(offline_frame, text=f"Listening for incoming payments on port {P2P_PORT}...",
                                      font=self.FONT_SMALL, fg=self.DIM, bg=self.BG)
        self.listen_label.pack(anchor="w", pady=(5, 0))

    # -- Tokens Tab --

    def _build_tokens_tab(self):
        self.tokens_listbox = tk.Listbox(self.tokens_tab, font=self.FONT, bg=self.CARD,
                                          fg=self.TEXT, selectbackground=self.CARD2,
                                          relief="flat", bd=0, highlightthickness=0)
        self.tokens_listbox.pack(fill="both", expand=True, padx=10, pady=10)

    # -- History Tab --

    def _build_history_tab(self):
        self.history_text = tk.Text(self.history_tab, font=self.FONT, bg=self.CARD,
                                     fg=self.TEXT, relief="flat", bd=0, wrap="word",
                                     state="disabled", highlightthickness=0)
        self.history_text.pack(fill="both", expand=True, padx=10, pady=10)
        self.history_text.tag_config("sent", foreground=self.RED)
        self.history_text.tag_config("received", foreground=self.GREEN)
        self.history_text.tag_config("info", foreground=self.BLUE)
        self.history_text.tag_config("time", foreground=self.DIM)

    # -- Log Tab --

    def _build_log_tab(self):
        self.log_text = tk.Text(self.log_tab, font=("Consolas", 10), bg="#0a0e14",
                                 fg=self.DIM, relief="flat", bd=0, wrap="word",
                                 state="disabled", highlightthickness=0)
        self.log_text.pack(fill="both", expand=True, padx=10, pady=10)
        self.log_text.tag_config("ok", foreground=self.GREEN)
        self.log_text.tag_config("err", foreground=self.RED)
        self.log_text.tag_config("warn", foreground=self.YELLOW)
        self.log_text.tag_config("info", foreground=self.BLUE)

    # -- TEE Hardware Log Tab --

    def _build_tee_log_tab(self):
        top_frame = tk.Frame(self.tee_log_tab, bg=self.BG)
        top_frame.pack(fill="x", padx=10, pady=(10, 5))

        tk.Label(top_frame, text="TEE Secure Enclave Operation Log",
                 font=self.FONT_BOLD, fg=self.YELLOW, bg=self.BG).pack(side="left")
        tk.Button(top_frame, text="Refresh", font=self.FONT_SMALL, bg=self.CARD2,
                  fg=self.TEXT, relief="flat", padx=10, pady=4,
                  command=self._refresh_tee_log, cursor="hand2").pack(side="right")

        tk.Label(self.tee_log_tab,
                 text="In production: these operations execute inside ARM TrustZone / ATECC608A secure element",
                 font=self.FONT_SMALL, fg=self.DIM, bg=self.BG).pack(anchor="w", padx=10)

        self.tee_log_text = tk.Text(self.tee_log_tab, font=("Consolas", 9), bg="#0a0a0f",
                                     fg="#8b9dc3", relief="flat", bd=0, wrap="word",
                                     state="disabled", highlightthickness=0)
        self.tee_log_text.pack(fill="both", expand=True, padx=10, pady=(5, 10))
        self.tee_log_text.tag_config("op_keygen", foreground="#22d3ee")
        self.tee_log_text.tag_config("op_token", foreground=self.GREEN)
        self.tee_log_text.tag_config("op_delete", foreground=self.RED)
        self.tee_log_text.tag_config("op_counter", foreground=self.YELLOW)
        self.tee_log_text.tag_config("op_sign", foreground="#a78bfa")
        self.tee_log_text.tag_config("op_verify", foreground=self.BLUE)
        self.tee_log_text.tag_config("op_tamper", foreground="#ff0000")

    def _refresh_tee_log(self):
        """Reload TEE log from file."""
        if not self.wallet:
            return
        self.tee_log_text.config(state="normal")
        self.tee_log_text.delete("1.0", "end")

        entries = self.wallet.tee_log.get_recent_entries(100)
        for entry in entries:
            tag = ""
            if "KEY_GEN" in entry or "KEY_EXPORT" in entry:
                tag = "op_keygen"
            elif "TOKEN_DELETE" in entry:
                tag = "op_delete"
            elif "TOKEN_" in entry:
                tag = "op_token"
            elif "COUNTER" in entry:
                tag = "op_counter"
            elif "PAYMENT_SIGN" in entry:
                tag = "op_sign"
            elif "VERIFY" in entry:
                tag = "op_verify"
            elif "TAMPER" in entry:
                tag = "op_tamper"
            self.tee_log_text.insert("end", entry + "\n", tag)

        self.tee_log_text.see("end")
        self.tee_log_text.config(state="disabled")

    # ===========================================
    # WALLET OPERATIONS
    # ===========================================

    def _open_wallet(self):
        user_id = self.user_id_entry.get().strip()
        self.server_url = self.server_entry.get().strip()
        self.p2p_port = int(self.port_entry.get().strip())

        if not user_id:
            messagebox.showerror("Error", "Enter a user ID")
            return

        self.wallet = TEEWallet(user_id, data_dir=os.path.expanduser(f"~/.trusted-bpi/{user_id}"))
        if not self.wallet.load_mint_pubkey():
            messagebox.showinfo("Info", "Mint public key not found. Connect to server first to register.")

        self.login_frame.destroy()
        self._build_main_screen()
        self._log("Wallet opened for " + user_id, "ok")

    def _register_and_open(self):
        user_id = self.user_id_entry.get().strip()
        self.server_url = self.server_entry.get().strip()
        self.p2p_port = int(self.port_entry.get().strip())

        if not user_id:
            messagebox.showerror("Error", "Enter a user ID")
            return

        try:
            # Get mint public key
            resp = requests.get(f"{self.server_url}/api/info", timeout=5)
            info = resp.json()
            mint_pk = {
                "n": int(info["mint_public_key"]["n"]),
                "e": info["mint_public_key"]["e"],
            }

            # Create wallet
            self.wallet = TEEWallet(user_id, mint_public_key=mint_pk,
                                     data_dir=os.path.expanduser(f"~/.trusted-bpi/{user_id}"))
            self.wallet.set_mint_pubkey(mint_pk)

            # Register with server
            resp = requests.post(f"{self.server_url}/api/register", json={
                "user_id": user_id,
                "public_key": self.wallet.public_key,
            }, timeout=5)

            result = resp.json()
            if resp.status_code != 200:
                messagebox.showwarning("Warning", f"Registration: {result.get('error', 'Unknown error')}\nWallet opened anyway.")
            else:
                messagebox.showinfo("Success", f"Registered {user_id} on Mint server!")

            self.is_online = True
            self.login_frame.destroy()
            self._build_main_screen()
            self._log(f"Registered and wallet opened for {user_id}", "ok")
            self._update_online_status(True)

        except requests.exceptions.ConnectionError:
            messagebox.showerror("Error", f"Cannot connect to server at {self.server_url}")
        except Exception as e:
            messagebox.showerror("Error", str(e))

    def _connect_server(self):
        try:
            resp = requests.get(f"{self.server_url}/api/info", timeout=5)
            info = resp.json()
            mint_pk = {"n": int(info["mint_public_key"]["n"]), "e": info["mint_public_key"]["e"]}
            self.wallet.set_mint_pubkey(mint_pk)
            self.is_online = True
            self._update_online_status(True)
            self._log(f"Connected to Mint server at {self.server_url}", "ok")
        except Exception as e:
            self.is_online = False
            self._update_online_status(False)
            self._log(f"Cannot connect: {e}", "err")
            messagebox.showerror("Error", f"Cannot connect to server:\n{e}")

    def _deposit_collateral(self):
        if not self.is_online:
            messagebox.showwarning("Offline", "Connect to server first!")
            return

        amount = simpledialog.askfloat("Deposit", "Amount to lock as collateral:",
                                        initialvalue=1000.0)
        if not amount:
            return

        try:
            resp = requests.post(f"{self.server_url}/api/deposit", json={
                "user_id": self.wallet.user_id,
                "amount": amount,
            }, timeout=5)
            result = resp.json()

            if result.get("success"):
                self._log(f"Deposited Rs.{amount}. Available for tokens: Rs.{result['available_for_tokens']}", "ok")
                messagebox.showinfo("Success",
                    f"Collateral locked: Rs.{result['collateral_locked']}\n"
                    f"Available for tokens: Rs.{result['available_for_tokens']}")
            else:
                self._log(f"Deposit failed: {result.get('error')}", "err")
                messagebox.showerror("Error", result.get("error"))
        except Exception as e:
            self._log(f"Deposit error: {e}", "err")

    def _mint_tokens(self):
        if not self.is_online:
            messagebox.showwarning("Offline", "Connect to server first!")
            return

        input_str = simpledialog.askstring("Mint Tokens",
            "Enter denominations (comma-separated):\ne.g. 100,100,50,50",
            initialvalue="100,100,50,50")

        if not input_str:
            return

        try:
            denoms = [float(x.strip()) for x in input_str.split(",")]
        except ValueError:
            messagebox.showerror("Error", "Invalid format. Use numbers separated by commas.")
            return

        minted = 0
        for denom in denoms:
            try:
                prep = self.wallet.prepare_blind_token(denom, expiry_hours=4.0)
                resp = requests.post(f"{self.server_url}/api/mint-token", json={
                    "user_id": self.wallet.user_id,
                    "blinded_message": str(prep["blinded_message"]),
                    "denomination": denom,
                }, timeout=5)
                result = resp.json()

                if result.get("success"):
                    blind_sig = int(result["blind_signature"])
                    self.wallet.load_signed_token(prep["internal_ref"], blind_sig)
                    self._log(f"Minted Rs.{denom} token (blind-signed by Mint)", "ok")
                    minted += 1
                else:
                    self._log(f"Mint failed for Rs.{denom}: {result.get('error')}", "err")
                    messagebox.showerror("Error", f"Rs.{denom}: {result.get('error')}")
                    break
            except Exception as e:
                self._log(f"Mint error: {e}", "err")
                break

        if minted > 0:
            messagebox.showinfo("Success", f"Minted {minted} tokens!")
            self._refresh_ui()

    def _settle_all(self):
        if not self.is_online:
            messagebox.showwarning("Offline", "Connect to server first!")
            return

        unsettled = self.wallet.get_unsettled_payments()
        if not unsettled:
            messagebox.showinfo("Info", "No pending payments to settle.")
            return

        settled_count = 0
        fraud_count = 0

        for p in unsettled:
            try:
                resp = requests.post(f"{self.server_url}/api/settle", json={
                    "payment": p["payment_data"],
                    "submitted_by": self.wallet.public_key,
                }, timeout=10)
                result = resp.json()

                self.wallet.mark_settled(p["payment_id"])
                settled_count += 1

                if result.get("fraud_detected"):
                    fraud_count += 1
                    self._log(f"!! DOUBLE SPEND detected during settlement of {p['payment_id'][:8]}!", "err")
                    for ds in result.get("double_spend_detected", []):
                        fr = ds.get("fraud_result", {})
                        if fr.get("identified"):
                            self._log(f"   Cheater: {fr['cheater']}, Slashed: Rs.{fr['slashed']}", "warn")
                else:
                    self._log(f"Settled payment {p['payment_id'][:8]} - Rs.{result['total_settled']}", "ok")

            except Exception as e:
                self._log(f"Settlement error: {e}", "err")

        msg = f"Settled {settled_count} payment(s)."
        if fraud_count:
            msg += f"\n!! {fraud_count} double-spend(s) detected!"
        messagebox.showinfo("Settlement Complete", msg)
        self._refresh_ui()

    def _check_server_balance(self):
        if not self.is_online:
            messagebox.showwarning("Offline", "Connect to server first!")
            return
        try:
            resp = requests.get(f"{self.server_url}/api/balance/{self.wallet.user_id}", timeout=5)
            data = resp.json()
            if "error" in data:
                messagebox.showinfo("Balance", data["error"])
            else:
                messagebox.showinfo("Server Balance",
                    f"Collateral Locked: Rs.{data['collateral_locked']}\n"
                    f"Tokens Issued: Rs.{data['tokens_issued_value']}\n"
                    f"Settled Balance: Rs.{data['settled_balance']}\n"
                    f"Status: {data['status']}")
        except Exception as e:
            self._log(f"Balance check error: {e}", "err")

    def _sender_settle(self):
        """Alice settles her OWN sent payments - push model.
        This means Bob gets money even if Bob hasn't come online yet."""
        if not self.is_online:
            messagebox.showwarning("Offline", "Connect to server first!")
            return

        # Get sent payments from history that haven't been sender-settled
        sent = [h for h in self.wallet.transaction_history
                if h["type"] == "sent" and not h.get("sender_settled")]

        if not sent:
            messagebox.showinfo("Info", "No sent payments to settle.")
            return

        settled_count = 0
        for s in sent:
            try:
                # Find the full payment data from the wallet's journal
                payment_id = s.get("payment_id")
                if not payment_id:
                    continue

                resp = requests.post(f"{self.server_url}/api/sender-settle", json={
                    "user_id": self.wallet.user_id,
                    "payment_id": payment_id,
                }, timeout=10)
                result = resp.json()

                if result.get("success"):
                    s["sender_settled"] = True
                    settled_count += 1
                    tx_hash = result.get("tx_hash", "")
                    self._log(f"Sender-settled {payment_id[:8]}... Rs.{s['amount']}", "ok")
                    if tx_hash:
                        self._log(f"  On-chain tx: {tx_hash[:16]}...", "info")
                elif result.get("already_settled"):
                    s["sender_settled"] = True
                    self._log(f"Payment {payment_id[:8]}... already settled", "info")
                else:
                    self._log(f"Sender-settle failed: {result.get('error', '?')}", "err")

            except Exception as e:
                self._log(f"Sender-settle error: {e}", "err")

        self.wallet._save_history()
        if settled_count > 0:
            messagebox.showinfo("Done", f"Sender-settled {settled_count} payment(s).\nBob will receive the money even if offline.")
        self._refresh_ui()

    def _withdraw_collateral(self):
        """Withdraw unused collateral back."""
        if not self.is_online:
            messagebox.showwarning("Offline", "Connect to server first!")
            return

        try:
            # First check how much is withdrawable
            resp = requests.get(f"{self.server_url}/api/balance/{self.wallet.user_id}", timeout=5)
            data = resp.json()

            collateral = data.get("collateral_locked", 0)
            issued = data.get("tokens_issued_value", 0)
            spent = data.get("tokens_spent_value", 0)
            outstanding = max(0, issued - spent)
            min_locked = outstanding * 2  # 2x ratio
            withdrawable = max(0, collateral - min_locked)

            if withdrawable <= 0:
                messagebox.showinfo("Info",
                    f"Nothing to withdraw.\n"
                    f"Collateral: Rs.{collateral}\n"
                    f"Outstanding tokens: Rs.{outstanding}\n"
                    f"Min locked (2x): Rs.{min_locked}")
                return

            amount = simpledialog.askfloat("Withdraw Collateral",
                f"Withdrawable: Rs.{withdrawable}\n\nAmount to withdraw:",
                initialvalue=withdrawable)

            if not amount or amount <= 0:
                return

            resp = requests.post(f"{self.server_url}/api/withdraw-collateral", json={
                "user_id": self.wallet.user_id,
                "amount": amount,
            }, timeout=10)
            result = resp.json()

            if result.get("success"):
                self._log(f"Withdrew Rs.{amount} collateral", "ok")
                if result.get("tx_hash"):
                    self._log(f"  On-chain: {result['tx_hash'][:16]}...", "info")
                messagebox.showinfo("Success", f"Withdrew Rs.{amount} collateral!")
            else:
                messagebox.showerror("Error", result.get("error", "Unknown error"))

        except Exception as e:
            self._log(f"Withdraw error: {e}", "err")

    def _open_etherscan(self):
        """Open the contract on Etherscan in browser."""
        import webbrowser
        try:
            resp = requests.get(f"{self.server_url}/api/stats", timeout=5)
            data = resp.json()
            url = data.get("etherscan", "https://sepolia.etherscan.io")
            webbrowser.open(url)
        except:
            webbrowser.open("https://sepolia.etherscan.io/address/0xc1A2dd3210949C559466063aE96E201F4DD48B42")

    # ===========================================
    # PEER-TO-PEER PAYMENT (OFFLINE - TCP SOCKET)
    # ===========================================

    def _send_payment(self):
        """Connect to peer's wallet via TCP and send a signed payment."""
        peer_ip = self.peer_ip_entry.get().strip()
        peer_port = int(self.peer_port_entry.get().strip())
        amount = float(self.amount_entry.get().strip())

        if amount <= 0:
            messagebox.showerror("Error", "Amount must be > 0")
            return
        if self.wallet.total_balance < amount:
            messagebox.showerror("Error", f"Insufficient balance. Have: Rs.{self.wallet.total_balance}")
            return

        self._log(f"Connecting to peer at {peer_ip}:{peer_port}...", "info")

        try:
            # Step 1: Connect to peer
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(10)
            sock.connect((peer_ip, peer_port))
            self._log(f"Connected to peer!", "ok")

            # Step 2: Exchange public keys
            hello = json.dumps({
                "type": "payment_init",
                "payer_pubkey": self.wallet.public_key,
                "payer_user_id": self.wallet.user_id,
                "amount": amount,
            }).encode()
            sock.sendall(len(hello).to_bytes(4, 'big') + hello)

            # Step 3: Receive challenge from peer
            resp_len = int.from_bytes(sock.recv(4), 'big')
            resp_data = b''
            while len(resp_data) < resp_len:
                resp_data += sock.recv(min(BUFFER_SIZE, resp_len - len(resp_data)))
            challenge_msg = json.loads(resp_data.decode())

            if challenge_msg["type"] == "reject":
                self._log(f"Peer rejected: {challenge_msg.get('reason')}", "err")
                messagebox.showwarning("Rejected", challenge_msg.get("reason", "Peer rejected payment"))
                sock.close()
                return

            challenge_bits = challenge_msg["challenge_bits"]
            payee_pubkey = challenge_msg["payee_pubkey"]
            self._log(f"Received challenge bits: {challenge_bits}", "info")

            # Step 4: Create signed payment inside TEE
            payment = self.wallet.make_payment(payee_pubkey, amount, challenge_bits)
            if payment is None:
                sock.close()
                self._log("Payment creation failed (insufficient tokens)", "err")
                return

            # Step 5: Send payment to peer
            payment_bytes = json.dumps(payment).encode()
            sock.sendall(len(payment_bytes).to_bytes(4, 'big') + payment_bytes)
            self._log(f"Payment sent: Rs.{amount} (counter={payment['monotonic_counter']})", "ok")

            # Step 6: Receive confirmation
            conf_len = int.from_bytes(sock.recv(4), 'big')
            conf_data = b''
            while len(conf_data) < conf_len:
                conf_data += sock.recv(min(BUFFER_SIZE, conf_len - len(conf_data)))
            confirmation = json.loads(conf_data.decode())

            sock.close()

            if confirmation.get("accepted"):
                self._log(f"[OK] Payment ACCEPTED by peer! Rs.{amount} sent.", "ok")
                messagebox.showinfo("Success", f"Payment of Rs.{amount} accepted by peer!")
            else:
                self._log(f"Payment rejected: {confirmation.get('errors')}", "err")
                messagebox.showerror("Rejected", f"Peer rejected: {confirmation.get('errors')}")

            self._refresh_ui()

        except socket.timeout:
            self._log("Connection timed out", "err")
            messagebox.showerror("Error", "Connection timed out. Is the peer's wallet running?")
        except ConnectionRefusedError:
            self._log("Connection refused", "err")
            messagebox.showerror("Error", f"Connection refused. Is the peer listening on {peer_ip}:{peer_port}?")
        except Exception as e:
            self._log(f"Payment error: {e}", "err")
            messagebox.showerror("Error", str(e))

    def _start_p2p_listener(self):
        """Start TCP server to receive incoming payments from peers."""
        self.p2p_listening = True
        self.p2p_server_thread = threading.Thread(target=self._p2p_listen_loop, daemon=True)
        self.p2p_server_thread.start()
        self._log(f"P2P listener started on port {self.p2p_port}", "info")

    def _p2p_listen_loop(self):
        """Background thread: listen for incoming P2P connections."""
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            server.bind(('0.0.0.0', self.p2p_port))
        except OSError as e:
            self.root.after(0, lambda: self._log(f"Cannot bind port {self.p2p_port}: {e}", "err"))
            return

        server.listen(5)
        server.settimeout(1)

        while self.p2p_listening:
            try:
                conn, addr = server.accept()
                threading.Thread(target=self._handle_incoming_payment,
                               args=(conn, addr), daemon=True).start()
            except socket.timeout:
                continue
            except Exception:
                break

        server.close()

    def _handle_incoming_payment(self, conn, addr):
        """Handle an incoming P2P payment connection."""
        try:
            self.root.after(0, lambda: self._log(f"Incoming connection from {addr[0]}:{addr[1]}", "info"))

            # Step 1: Receive payment init
            msg_len = int.from_bytes(conn.recv(4), 'big')
            msg_data = b''
            while len(msg_data) < msg_len:
                msg_data += conn.recv(min(BUFFER_SIZE, msg_len - len(msg_data)))
            init_msg = json.loads(msg_data.decode())

            payer = init_msg["payer_user_id"]
            amount = init_msg["amount"]
            self.root.after(0, lambda: self._log(
                f"Payment request from {payer}: Rs.{amount}", "warn"))

            # Step 2: Generate and send challenge
            challenge_bits = IdentityEmbedding.generate_challenge(5)
            challenge_resp = json.dumps({
                "type": "challenge",
                "challenge_bits": challenge_bits,
                "payee_pubkey": self.wallet.public_key,
            }).encode()
            conn.sendall(len(challenge_resp).to_bytes(4, 'big') + challenge_resp)

            # Step 3: Receive signed payment
            pay_len = int.from_bytes(conn.recv(4), 'big')
            pay_data = b''
            while len(pay_data) < pay_len:
                pay_data += conn.recv(min(BUFFER_SIZE, pay_len - len(pay_data)))
            payment = json.loads(pay_data.decode())

            # Step 4: Verify payment inside TEE (OFFLINE - no server!)
            verification = self.wallet.receive_payment(payment)

            # Step 5: Send confirmation
            if verification["valid"]:
                confirm = {"accepted": True, "amount": verification["amount"]}
                self.root.after(0, lambda: self._log(
                    f"[OK] ACCEPTED Rs.{verification['amount']} from {payer}!", "ok"))
                self.root.after(0, lambda: self._refresh_ui())
            else:
                confirm = {"accepted": False, "errors": verification["errors"]}
                self.root.after(0, lambda: self._log(
                    f"[X] REJECTED payment from {payer}: {verification['errors']}", "err"))

            confirm_bytes = json.dumps(confirm).encode()
            conn.sendall(len(confirm_bytes).to_bytes(4, 'big') + confirm_bytes)

        except Exception as e:
            self.root.after(0, lambda: self._log(f"Incoming payment error: {e}", "err"))
        finally:
            conn.close()

    # ===========================================
    # UI HELPERS
    # ===========================================

    def _refresh_ui(self):
        if not self.wallet:
            return

        # Balance cards
        self.balance_label._val_label.config(text=f"Rs.{self.wallet.total_balance:.0f}")
        self.token_count_label._val_label.config(text=str(self.wallet.token_count))
        self.counter_label._val_label.config(text=str(self.wallet.counter_value))
        self.pending_label._val_label.config(
            text=str(len(self.wallet.get_unsettled_payments())))

        # Token list
        self.tokens_listbox.delete(0, "end")
        for ts in self.wallet.get_token_summary():
            status = "EXPIRED" if ts["expired"] else f"expires in {ts['expires_in']}s"
            self.tokens_listbox.insert("end",
                f"  Rs.{ts['denomination']:.0f}  |  {ts['serial_short']}...  |  {status}")

        # History
        self.history_text.config(state="normal")
        self.history_text.delete("1.0", "end")
        for h in reversed(self.wallet.transaction_history):
            if h["type"] == "sent":
                self.history_text.insert("end", f"[{h['time']}] ", "time")
                self.history_text.insert("end", f"SENT Rs.{h['amount']:.0f}", "sent")
                status = ""
                if h.get("sender_settled"):
                    status = " [SETTLED ON-CHAIN]"
                self.history_text.insert("end", f" -> {h.get('to', '?')}{status}\n")
            elif h["type"] == "received":
                self.history_text.insert("end", f"[{h['time']}] ", "time")
                self.history_text.insert("end", f"RECEIVED Rs.{h['amount']:.0f}", "received")
                settled = " [SETTLED]" if h.get("settled") else " [PENDING]"
                self.history_text.insert("end", f" from {h.get('from', '?')}{settled}\n")
        self.history_text.config(state="disabled")

        # Auto-refresh TEE log
        self._refresh_tee_log()

    def _update_online_status(self, online):
        self.is_online = online
        if online:
            self.status_label.config(text="* ONLINE", fg=self.GREEN)
        else:
            self.status_label.config(text="* OFFLINE", fg=self.RED)

    def _log(self, message, tag=""):
        ts = time.strftime("%H:%M:%S")
        self.log_text.config(state="normal")
        self.log_text.insert("end", f"[{ts}] ", "")
        self.log_text.insert("end", message + "\n", tag)
        self.log_text.see("end")
        self.log_text.config(state="disabled")

    def _get_local_ip(self):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except Exception:
            return "127.0.0.1"

    def on_close(self):
        self.p2p_listening = False
        self.root.destroy()


def main():
    root = tk.Tk()
    app = WalletApp(root)
    root.protocol("WM_DELETE_WINDOW", app.on_close)
    root.mainloop()


if __name__ == "__main__":
    main()
