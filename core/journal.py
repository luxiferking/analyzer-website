"""
Jarvis Trade Journal — SQLite backed
Handles: add/close/query/stats for all trades.
"""
import sqlite3
import os
from datetime import datetime
from contextlib import contextmanager

# DB path: <project_root>/data/journal.db
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(_PROJECT_ROOT, "data", "journal.db")


class TradeJournal:
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self._init_schema()

    # ---------- Connection ----------
    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_schema(self):
        with self._conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS trades (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    date            TEXT NOT NULL,
                    time            TEXT,
                    ticker          TEXT NOT NULL,
                    direction       TEXT NOT NULL,       -- BUY / SELL
                    entry           REAL NOT NULL,
                    sl              REAL,
                    target          REAL,
                    exit_price      REAL,
                    qty             INTEGER NOT NULL,
                    investment      REAL,
                    pnl             REAL,
                    pnl_pct         REAL,
                    ai_confidence   INTEGER,
                    patterns        TEXT,
                    news_score      REAL,
                    sector          TEXT,
                    global_bias     TEXT,
                    status          TEXT DEFAULT 'OPEN', -- OPEN / CLOSED / CANCELLED
                    notes           TEXT,
                    created_at      TEXT DEFAULT CURRENT_TIMESTAMP,
                    closed_at       TEXT
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_ticker ON trades(ticker)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_status ON trades(status)")

    # ---------- Add / Close ----------
    def add_trade(self, ticker, direction, entry, qty,
                  sl=None, target=None, ai_confidence=None,
                  patterns=None, news_score=None, sector=None,
                  global_bias=None, notes=None, date=None, time=None):
        ticker = ticker.upper().replace(".NS", "").replace(".BO", "").strip()
        direction = direction.upper().strip()
        if direction not in ("BUY", "SELL"):
            return None, "Direction must be BUY or SELL"

        now = datetime.now()
        date = date or now.strftime("%Y-%m-%d")
        time = time or now.strftime("%H:%M")

        investment = round(entry * qty, 2)

        with self._conn() as conn:
            cur = conn.execute("""
                INSERT INTO trades
                (date, time, ticker, direction, entry, sl, target, qty,
                 investment, ai_confidence, patterns, news_score, sector,
                 global_bias, notes, status)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?, 'OPEN')
            """, (date, time, ticker, direction, entry, sl, target, qty,
                  investment, ai_confidence, patterns, news_score, sector,
                  global_bias, notes))
            return cur.lastrowid, None

    def close_trade(self, trade_id, exit_price, notes=None):
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM trades WHERE id = ?",
                               (trade_id,)).fetchone()
            if not row:
                return None, f"Trade #{trade_id} not found"
            if row["status"] != "OPEN":
                return None, f"Trade #{trade_id} is already {row['status']}"

            entry = row["entry"]
            qty = row["qty"]
            direction = row["direction"]

            if direction == "BUY":
                pnl = (exit_price - entry) * qty
            else:  # SELL / short
                pnl = (entry - exit_price) * qty

            investment = entry * qty
            pnl_pct = (pnl / investment * 100) if investment else 0

            merged_notes = row["notes"] or ""
            if notes:
                merged_notes += f"\n[close] {notes}"

            conn.execute("""
                UPDATE trades
                SET exit_price = ?, pnl = ?, pnl_pct = ?, status = 'CLOSED',
                    closed_at = ?, notes = ?
                WHERE id = ?
            """, (exit_price, round(pnl, 2), round(pnl_pct, 2),
                  datetime.now().isoformat(timespec="seconds"),
                  merged_notes.strip(), trade_id))

            return {
                "id": trade_id,
                "ticker": row["ticker"],
                "direction": direction,
                "entry": entry,
                "exit": exit_price,
                "qty": qty,
                "pnl": round(pnl, 2),
                "pnl_pct": round(pnl_pct, 2),
            }, None

    def cancel_trade(self, trade_id, reason=None):
        with self._conn() as conn:
            conn.execute("""
                UPDATE trades
                SET status = 'CANCELLED', notes = COALESCE(notes,'') || ?
                WHERE id = ? AND status = 'OPEN'
            """, (f"\n[cancelled] {reason or ''}".strip(), trade_id))
        return True

    # ---------- Query ----------
    def get_open_trades(self):
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM trades WHERE status = 'OPEN' ORDER BY id DESC"
            ).fetchall()
            return [dict(r) for r in rows]

    def get_trade(self, trade_id):
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM trades WHERE id = ?",
                               (trade_id,)).fetchone()
            return dict(row) if row else None

    def get_recent_trades(self, limit=10):
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM trades ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
            return [dict(r) for r in rows]

    def get_trades_by_ticker(self, ticker):
        ticker = ticker.upper().replace(".NS", "").replace(".BO", "").strip()
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM trades WHERE ticker = ? ORDER BY id DESC",
                (ticker,)
            ).fetchall()
            return [dict(r) for r in rows]

    # ---------- Stats ----------
    def get_stats(self):
        with self._conn() as conn:
            closed = conn.execute(
                "SELECT * FROM trades WHERE status = 'CLOSED'"
            ).fetchall()

            if not closed:
                open_count = conn.execute(
                    "SELECT COUNT(*) c FROM trades WHERE status = 'OPEN'"
                ).fetchone()["c"]
                return {
                    "total_trades": 0,
                    "open_trades": open_count,
                    "wins": 0, "losses": 0,
                    "win_rate": 0,
                    "total_pnl": 0,
                    "avg_win": 0, "avg_loss": 0,
                    "best": None, "worst": None,
                }

            wins = [r for r in closed if (r["pnl"] or 0) > 0]
            losses = [r for r in closed if (r["pnl"] or 0) <= 0]
            total_pnl = sum((r["pnl"] or 0) for r in closed)

            best = max(closed, key=lambda r: r["pnl"] or 0)
            worst = min(closed, key=lambda r: r["pnl"] or 0)

            open_count = conn.execute(
                "SELECT COUNT(*) c FROM trades WHERE status = 'OPEN'"
            ).fetchone()["c"]

            return {
                "total_trades": len(closed),
                "open_trades": open_count,
                "wins": len(wins),
                "losses": len(losses),
                "win_rate": round(len(wins) / len(closed) * 100, 2),
                "total_pnl": round(total_pnl, 2),
                "avg_win": round(sum(r["pnl"] for r in wins) / len(wins), 2) if wins else 0,
                "avg_loss": round(sum(r["pnl"] for r in losses) / len(losses), 2) if losses else 0,
                "best": {"ticker": best["ticker"], "pnl": best["pnl"]},
                "worst": {"ticker": worst["ticker"], "pnl": worst["pnl"]},
            }

    # ---------- Pretty Formatters ----------
    @staticmethod
    def format_open_trades(trades):
        if not trades:
            return "📭 No open trades."
        lines = ["📌 OPEN TRADES:", "─" * 70]
        for t in trades:
            lines.append(
                f"  #{t['id']:>3}  {t['ticker']:<12} {t['direction']:<4} "
                f"@ ₹{t['entry']:<8}  SL ₹{t.get('sl') or '-':<8} "
                f"TGT ₹{t.get('target') or '-':<8}  Qty {t['qty']}"
            )
        return "\n".join(lines)

    @staticmethod
    def format_stats(s):
        lines = [
            "📊 JOURNAL STATS",
            "─" * 40,
            f"  Closed trades : {s['total_trades']}",
            f"  Open trades   : {s['open_trades']}",
            f"  Wins / Losses : {s['wins']} / {s['losses']}",
            f"  Win rate      : {s['win_rate']}%",
            f"  Total P&L     : ₹{s['total_pnl']}",
            f"  Avg win       : ₹{s['avg_win']}",
            f"  Avg loss      : ₹{s['avg_loss']}",
        ]
        if s.get("best"):
            lines.append(f"  Best trade    : {s['best']['ticker']} (₹{s['best']['pnl']})")
        if s.get("worst"):
            lines.append(f"  Worst trade   : {s['worst']['ticker']} (₹{s['worst']['pnl']})")
        return "\n".join(lines)

    @staticmethod
    def format_close_result(r):
        emoji = "🟢" if r["pnl"] > 0 else "🔴"
        return (
            f"{emoji} Trade #{r['id']} closed — {r['ticker']} {r['direction']}\n"
            f"   Entry ₹{r['entry']} → Exit ₹{r['exit']}  Qty {r['qty']}\n"
            f"   P&L: ₹{r['pnl']}  ({r['pnl_pct']}%)"
        )