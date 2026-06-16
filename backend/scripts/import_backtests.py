"""
import_backtests.py — Import MT5 Strategy Tester backtest reports into Supabase.

Supports the single-run report format produced by MetaTrader 5's Strategy Tester
(File → Save as Report → xlsx). Optimization result files (multiple passes per sheet)
are detected and skipped with a warning.

Usage:
    python -m backend.scripts.import_backtests mql5/backtests/bb-USDCAD.xlsx
    python -m backend.scripts.import_backtests mql5/backtests/   # all xlsx in dir

Requires:
    pip install openpyxl supabase
    SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY in environment (or .env file).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def _is_optimization_file(path: Path) -> bool:
    """
    Return True if this file should be skipped as an optimization run.

    MT5 optimization exports are SpreadsheetML (XML-based, not OOXML zip).
    They start with an XML declaration and can't be opened by openpyxl.
    Single-run reports are true OOXML (.xlsx zip).
    """
    try:
        with path.open("rb") as f:
            header = f.read(4)
        # True xlsx (OOXML) starts with PK zip magic: 50 4B 03 04
        if header[:2] == b"PK":
            return False
        # SpreadsheetML starts with "<?xm" — treat as optimization / unsupported
        return True
    except OSError:
        return True


def _cell(rows: list, keyword: str, value_col: int = 3) -> Any:
    """Find first row where col 0 contains keyword, return col value_col."""
    kw = keyword.lower()
    for row in rows:
        if row and row[0] and str(row[0]).lower().startswith(kw):
            val = row[value_col] if len(row) > value_col else None
            return val
    return None


def _parse_single_run(path: Path) -> dict[str, Any]:
    """
    Parse a single-run MT5 Strategy Tester report xlsx.
    Returns a dict matching the ea_backtests schema columns.
    Raises ValueError on unrecognised format.
    """
    import openpyxl

    wb = openpyxl.load_workbook(path, read_only=True)
    ws = wb.worksheets[0]
    rows = [list(r) for r in ws.iter_rows(values_only=True)]

    if not rows or rows[0][0] != "Strategy Tester Report":
        raise ValueError(f"{path.name}: not a recognised MT5 single-run report")

    # --- identity ---
    ea_name_raw = _cell(rows, "Expert:")
    symbol = _cell(rows, "Symbol:")
    period = _cell(rows, "Period:")

    if ea_name_raw is None or symbol is None:
        raise ValueError(f"{path.name}: missing Expert/Symbol fields")

    ea_name = str(ea_name_raw).strip()
    symbol = str(symbol).strip()

    # --- account ---
    initial_deposit = _cell(rows, "Initial Deposit:")
    currency = _cell(rows, "Currency:")
    leverage = _cell(rows, "Leverage:")

    # --- results ---
    total_net_profit = _cell(rows, "Total Net Profit:")
    gross_profit     = _cell(rows, "Gross Profit:")
    gross_loss       = _cell(rows, "Gross Loss:")
    profit_factor    = _cell(rows, "Profit Factor:")
    recovery_factor  = _cell(rows, "Recovery Factor:")
    sharpe_ratio     = _cell(rows, "Sharpe Ratio:")
    expected_payoff  = _cell(rows, "Expected Payoff:")

    # Drawdown: "Balance Drawdown Relative:" col 4 is "X.XX% (YYY.YY)"
    dd_row = next(
        (r for r in rows if r and r[0] and "Balance Drawdown Relative" in str(r[0])),
        None,
    )
    equity_dd_pct: float | None = None
    if dd_row:
        raw_dd = dd_row[3] if len(dd_row) > 3 else None
        if raw_dd and isinstance(raw_dd, str) and "%" in raw_dd:
            try:
                equity_dd_pct = float(raw_dd.split("%")[0])
            except ValueError:
                pass
        elif isinstance(raw_dd, (int, float)):
            equity_dd_pct = float(raw_dd)

    # --- trade counts ---
    total_trades = _cell(rows, "Total Trades:")
    profit_trades_raw = _cell(rows, "Profit Trades")
    loss_trades_raw   = _cell(rows, "Short Trades")  # fallback parse below

    def _to_int(v: Any) -> int | None:
        if v is None:
            return None
        try:
            return int(float(str(v).split("(")[0].strip()))
        except (ValueError, TypeError):
            return None

    # "Profit Trades (% of total):" value is e.g. "85 (51.20%)"
    profit_trades = _to_int(profit_trades_raw)
    total_trades_int = _to_int(total_trades)
    loss_trades: int | None = None
    if total_trades_int is not None and profit_trades is not None:
        loss_trades = total_trades_int - profit_trades

    # --- EA params from Inputs: section ---
    ea_params: dict[str, str] = {}
    in_inputs = False
    for row in rows:
        if row and row[0] and str(row[0]).lower() == "inputs:":
            in_inputs = True
        if in_inputs:
            val_cell = row[3] if len(row) > 3 else None
            if val_cell and "=" in str(val_cell):
                k, _, v = str(val_cell).partition("=")
                ea_params[k.strip()] = v.strip()
            elif row[0] and str(row[0]).lower() not in ("inputs:", ""):
                break  # left the Inputs block

    def _f(v: Any) -> float | None:
        if v is None:
            return None
        try:
            return float(v)
        except (ValueError, TypeError):
            return None

    return {
        "ea_name":          ea_name,
        "source_file":      path.name,
        "symbol":           symbol.upper(),
        "period":           str(period) if period else None,
        "initial_deposit":  _f(initial_deposit),
        "currency":         str(currency) if currency else None,
        "leverage":         str(leverage) if leverage else None,
        "total_net_profit": _f(total_net_profit),
        "gross_profit":     _f(gross_profit),
        "gross_loss":       _f(gross_loss),
        "profit_factor":    _f(profit_factor),
        "recovery_factor":  _f(recovery_factor),
        "sharpe_ratio":     _f(sharpe_ratio),
        "expected_payoff":  _f(expected_payoff),
        "equity_dd_pct":    equity_dd_pct,
        "total_trades":     total_trades_int,
        "profit_trades":    profit_trades,
        "loss_trades":      loss_trades,
        "ea_params":        ea_params,
    }


# ---------------------------------------------------------------------------
# Supabase insert
# ---------------------------------------------------------------------------

def _insert(row: dict[str, Any]) -> str:
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        raise RuntimeError("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set.")

    try:
        from supabase import create_client
    except ImportError as exc:
        raise RuntimeError(f"supabase-py not installed: {exc}") from exc

    client = create_client(url, key)

    # JSONB must be a string when sent via REST
    row = {**row, "ea_params": json.dumps(row["ea_params"])}

    result = client.table("ea_backtests").insert(row).execute()
    if not result.data:
        raise RuntimeError("Supabase insert returned no data.")
    return result.data[0]["id"]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _collect_paths(targets: list[str]) -> list[Path]:
    paths: list[Path] = []
    for t in targets:
        p = Path(t)
        if p.is_dir():
            paths.extend(sorted(p.glob("*.xlsx")))
        elif p.is_file():
            paths.append(p)
        else:
            logger.warning("Path not found: %s", t)
    return paths


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    parser = argparse.ArgumentParser(description="Import MT5 backtest reports into Supabase.")
    parser.add_argument("targets", nargs="+", help="xlsx file(s) or directory containing them")
    parser.add_argument("--dry-run", action="store_true", help="Parse only, do not insert")
    args = parser.parse_args(argv)

    # Load .env if present
    env_path = Path(".env")
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())

    paths = _collect_paths(args.targets)
    if not paths:
        logger.error("No xlsx files found.")
        return 1

    ok = 0
    skipped = 0
    failed = 0

    for path in paths:
        if _is_optimization_file(path):
            logger.warning("SKIP (optimization run, not single-run report): %s", path.name)
            skipped += 1
            continue

        try:
            row = _parse_single_run(path)
        except ValueError as exc:
            logger.error("SKIP (parse error): %s", exc)
            skipped += 1
            continue

        logger.info(
            "Parsed: %s | %s | %s | net_profit=%.2f | trades=%s",
            row["ea_name"], row["symbol"], row["source_file"],
            row["total_net_profit"] or 0,
            row["total_trades"],
        )

        if args.dry_run:
            ok += 1
            continue

        try:
            record_id = _insert(row)
            logger.info("Inserted: id=%s", record_id)
            ok += 1
        except Exception as exc:
            logger.error("INSERT FAILED for %s: %s", path.name, exc)
            failed += 1

    logger.info("Done. ok=%d  skipped=%d  failed=%d", ok, skipped, failed)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
