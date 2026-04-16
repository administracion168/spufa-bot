import sqlite3
import os
from contextlib import contextmanager

# Use DATA_DIR env var for Railway Volume persistence (set to /data on Railway)
_data_dir = os.environ.get("DATA_DIR", os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(_data_dir, "spufa.db")


def init_db():
    os.makedirs(_data_dir, exist_ok=True)
    with get_conn() as conn:
        conn.executescript("""
            PRAGMA journal_mode=WAL;

            CREATE TABLE IF NOT EXISTS users (
                user_id     INTEGER PRIMARY KEY,
                username    TEXT,
                balance     INTEGER DEFAULT 0,
                total_used  INTEGER DEFAULT 0,
                total_topped_up INTEGER DEFAULT 0,
                referred_by INTEGER,
                referral_earnings_cents INTEGER DEFAULT 0,
                created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS transactions (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id         INTEGER,
                type            TEXT,
                amount          INTEGER,
                oxapay_order_id TEXT,
                created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS referral_withdrawals (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id         INTEGER NOT NULL,
                amount_cents    INTEGER NOT NULL,
                wallet_address  TEXT NOT NULL,
                network         TEXT NOT NULL,
                currency        TEXT NOT NULL,
                status          TEXT DEFAULT 'pending',
                oxapay_track_id TEXT,
                admin_note      TEXT,
                created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)

        # ── Migrate existing DBs that lack new columns ────────────────────────
        cols = {
            row[1]
            for row in conn.execute("PRAGMA table_info(users)").fetchall()
        }
        if "referred_by" not in cols:
            conn.execute("ALTER TABLE users ADD COLUMN referred_by INTEGER")
        if "referral_earnings_cents" not in cols:
            conn.execute(
                "ALTER TABLE users ADD COLUMN referral_earnings_cents INTEGER DEFAULT 0"
            )


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ── User helpers ──────────────────────────────────────────────────────────────

def get_or_create_user(user_id: int, username: str = None) -> dict:
    with get_conn() as conn:
        user = conn.execute(
            "SELECT * FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()
        if not user:
            conn.execute(
                "INSERT INTO users (user_id, username) VALUES (?, ?)",
                (user_id, username),
            )
            user = conn.execute(
                "SELECT * FROM users WHERE user_id = ?", (user_id,)
            ).fetchone()
    return dict(user)


def get_user(user_id: int) -> dict | None:
    with get_conn() as conn:
        user = conn.execute(
            "SELECT * FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()
        return dict(user) if user else None


def set_referral(user_id: int, referrer_id: int) -> bool:
    """Assign referrer to a user. Returns True if set, False if already had one."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT referred_by FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()
        if not row or row["referred_by"] is not None:
            return False
        conn.execute(
            "UPDATE users SET referred_by = ? WHERE user_id = ?",
            (referrer_id, user_id),
        )
        return True


def deduct_coins(user_id: int, amount: int = 1):
    with get_conn() as conn:
        conn.execute(
            "UPDATE users SET balance = balance - ?, total_used = total_used + ? WHERE user_id = ?",
            (amount, amount, user_id),
        )
        conn.execute(
            "INSERT INTO transactions (user_id, type, amount) VALUES (?, 'spend', ?)",
            (user_id, amount),
        )


def credit_coins(user_id: int, amount: int, order_id: str) -> bool:
    """Credit coins after payment. Returns True if first time (not duplicate)."""
    with get_conn() as conn:
        existing = conn.execute(
            "SELECT id FROM transactions WHERE oxapay_order_id = ?", (order_id,)
        ).fetchone()
        if existing:
            return False
        conn.execute(
            "UPDATE users SET balance = balance + ?, total_topped_up = total_topped_up + ? WHERE user_id = ?",
            (amount, amount, user_id),
        )
        conn.execute(
            "INSERT INTO transactions (user_id, type, amount, oxapay_order_id) VALUES (?, 'topup', ?, ?)",
            (user_id, amount, order_id),
        )
        return True


# ── Referral helpers ──────────────────────────────────────────────────────────

def add_referral_commission(referrer_id: int, amount_cents: int):
    """Add USD commission (in cents) to the referrer's balance."""
    with get_conn() as conn:
        conn.execute(
            "UPDATE users SET referral_earnings_cents = referral_earnings_cents + ? WHERE user_id = ?",
            (amount_cents, referrer_id),
        )
        conn.execute(
            "INSERT INTO transactions (user_id, type, amount) VALUES (?, 'referral_commission', ?)",
            (referrer_id, amount_cents),
        )


def get_referral_stats(user_id: int) -> dict:
    """Return referral stats for a user."""
    with get_conn() as conn:
        referred_count = conn.execute(
            "SELECT COUNT(*) FROM users WHERE referred_by = ?", (user_id,)
        ).fetchone()[0]

        user = conn.execute(
            "SELECT referral_earnings_cents FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()
        earnings_cents = user["referral_earnings_cents"] if user else 0

        # Sum of paid-out withdrawals
        paid_cents = conn.execute(
            "SELECT COALESCE(SUM(amount_cents), 0) FROM referral_withdrawals "
            "WHERE user_id = ? AND status = 'paid'",
            (user_id,),
        ).fetchone()[0]

        # Pending withdrawal request (if any)
        pending = conn.execute(
            "SELECT id, amount_cents FROM referral_withdrawals "
            "WHERE user_id = ? AND status = 'pending' ORDER BY created_at DESC LIMIT 1",
            (user_id,),
        ).fetchone()

    return {
        "referred_count": referred_count,
        "total_earned_cents": earnings_cents,
        "paid_out_cents": paid_cents,
        "available_cents": earnings_cents - paid_cents,
        "pending_withdrawal": dict(pending) if pending else None,
    }


# ── Withdrawal helpers ────────────────────────────────────────────────────────

def create_withdrawal(
    user_id: int,
    amount_cents: int,
    wallet_address: str,
    network: str,
    currency: str,
) -> int:
    """Create a pending withdrawal request. Returns the new row id."""
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO referral_withdrawals
               (user_id, amount_cents, wallet_address, network, currency)
               VALUES (?, ?, ?, ?, ?)""",
            (user_id, amount_cents, wallet_address, network, currency),
        )
        return cur.lastrowid


def get_pending_withdrawals() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT rw.*, u.username
               FROM referral_withdrawals rw
               JOIN users u ON u.user_id = rw.user_id
               WHERE rw.status = 'pending'
               ORDER BY rw.created_at ASC"""
        ).fetchall()
        return [dict(r) for r in rows]


def update_withdrawal_status(
    withdrawal_id: int,
    status: str,
    oxapay_track_id: str = None,
    admin_note: str = None,
):
    with get_conn() as conn:
        conn.execute(
            """UPDATE referral_withdrawals
               SET status = ?, oxapay_track_id = ?, admin_note = ?,
                   updated_at = CURRENT_TIMESTAMP
               WHERE id = ?""",
            (status, oxapay_track_id, admin_note, withdrawal_id),
        )
