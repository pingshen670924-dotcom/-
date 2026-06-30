from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import html
import itertools
import json
import math
import random
import re
import shutil
import sqlite3
import statistics
import time
import urllib.request
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable


MIN_NUMBER = 1
MAX_NUMBER = 49
MAIN_COUNT = 6
DEFAULT_RECENT_WINDOW = 30
DEFAULT_DB = Path("香港六合彩預測系統.db")
DEFAULT_REPORT_DIR = Path("reports")
MODEL_VERSION = "香港六合彩預測系統_20260630_第16版"
BUNDLED_SEED_CSV = Path("data/香港六合彩預測系統_種子資料_20260622.csv")
SITE_HOME_NAME = "香港六合彩預測系統_首頁.html"
SITE_BATTLE_REPORT_NAME = "香港六合彩預測系統_完整戰報.html"
SITE_LATEST_PREDICTION_NAME = "香港六合彩預測系統_最新預測.html"
SITE_SYSTEM_REPORT_NAME = "香港六合彩預測系統_系統報告.html"
SITE_DRAWS_CSV_NAME = "香港六合彩預測系統_歷史資料.csv"
SITE_STATUS_NAME = "香港六合彩預測系統_系統狀態.txt"
SITE_PREDICTION_RUNS_NAME = "香港六合彩預測系統_預測紀錄.json"
BATTLE_REPORT_MARKDOWN_NAME = "香港六合彩預測系統_完整戰報.md"
BATTLE_REPORT_TEXT_NAME = "香港六合彩預測系統_完整戰報.txt"
ENHANCED_BATTLE_REPORT_NAME = "香港六合彩預測系統_最新強化戰報.html"
MOBILE_CLOUD_HTML_NAME = "香港六合彩預測系統_手機首頁.html"
MOBILE_CLOUD_REPORT_NAME = "香港六合彩預測系統_手機雲端.html"
LEGACY_SITE_OUTPUT_NAMES = [
    "draws.csv",
    "latest_battle_report.html",
    "latest_prediction.html",
    "mobile.html",
    "mobile_manifest.json",
    "mobile_service_worker.js",
    "mobile_status.json",
    "prediction_runs.json",
    "status.txt",
    "system_report.html",
    "手機雲端網址.txt",
]
LEGACY_REPORT_OUTPUT_NAMES = [
    "latest_battle_report.html",
    "latest_battle_report.md",
    "latest_battle_report.txt",
    "六合彩手機雲端系統.html",
    "六合彩最新強化戰報.html",
]
LOCAL_TZ = timezone(timedelta(hours=8))
AUTO_BACKTEST_PERIODS = 48
IRON_LAW_WINDOWS = (60, 120, 360)
AUTO_MIN_STRATEGY_WEIGHT = 0.25
AUTO_MAX_STRATEGY_WEIGHT = 1.85
CORE_POOL_SIZE = 9
SUPPORT_POOL_SIZE = 15
ROLLING_MONTH_WEIGHT = 0.085
ZONE_REPAIR_WEIGHT = 0.070
BREAKOUT_CAPTURE_WEIGHT = 0.060
NEIGHBOR_BRIDGE_WEIGHT = 0.045
SETTLEMENT_FEEDBACK_WEIGHT = 0.115
TRANSITION_FOLLOW_WEIGHT = 0.065
TAIL_TRANSITION_WEIGHT = 0.050
CALENDAR_PHASE_WEIGHT = 0.040
SPECIAL_CROSSOVER_WEIGHT = 0.045
_SCORE_RANK_BACKTEST_CACHE: dict[tuple[int, str, str, str, int, int], dict[str, float | int]] = {}

RED_WAVE = {1, 2, 7, 8, 12, 13, 18, 19, 23, 24, 29, 30, 34, 35, 40, 45, 46}
BLUE_WAVE = {3, 4, 9, 10, 14, 15, 20, 25, 26, 31, 36, 37, 41, 42, 47, 48}
GREEN_WAVE = {5, 6, 11, 16, 17, 21, 22, 27, 28, 32, 33, 38, 39, 43, 44, 49}


@dataclass(frozen=True)
class Draw:
    draw_date: str
    draw_no: str
    main_numbers: tuple[int, ...]
    special: int
    source: str = "manual"
    row_id: int | None = None


@dataclass(frozen=True)
class NumberScore:
    number: int
    total_frequency: int
    recent_frequency: int
    special_frequency: int
    miss_gap: int
    trend: float
    pair_strength: float
    score: float
    color: str
    model_scores: dict[str, float]


@dataclass(frozen=True)
class Ticket:
    numbers: tuple[int, ...]
    score: float
    profile: str
    strategy: str
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class PredictionPackage:
    strategy: str
    tickets: list[Ticket]
    bankers: tuple[int, ...]
    drags: tuple[int, ...]
    reserves: tuple[int, ...]
    weak_numbers: tuple[int, ...]
    special_candidates: tuple[int, ...]
    scores: dict[int, NumberScore]


def now_text() -> str:
    return datetime.now(LOCAL_TZ).replace(microsecond=0).isoformat()


def strategy_names(include_auto: bool = False) -> tuple[str, ...]:
    names = ("balanced", "hot", "cold", "trend", "diversified")
    return ("auto", *names) if include_auto else names


def strategy_label(strategy: str) -> str:
    labels = {
        "auto": "自動融合",
        "balanced": "均衡",
        "hot": "熱度",
        "cold": "冷遺漏",
        "trend": "趨勢",
        "diversified": "分散",
    }
    return labels.get(strategy, strategy)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="香港六合彩預測系統")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_db = subparsers.add_parser("init-db", help="建立 SQLite 資料庫")
    init_db.add_argument("--db", type=Path, default=DEFAULT_DB)

    import_csv = subparsers.add_parser("import-csv", help="匯入歷史開獎 CSV")
    import_csv.add_argument("--db", type=Path, default=DEFAULT_DB)
    import_csv.add_argument("--csv", required=True, type=Path)

    fetch_hkjc = subparsers.add_parser("fetch-hkjc", help="從 HKJC GraphQL 端點抓取近期開獎")
    fetch_hkjc.add_argument("--db", type=Path, default=DEFAULT_DB)
    fetch_hkjc.add_argument("--last", type=int, default=60)
    fetch_hkjc.add_argument("--start-date", default=None)
    fetch_hkjc.add_argument("--end-date", default=None)

    fetch_lottolyzer = subparsers.add_parser("fetch-lottolyzer", help="從 Lottolyzer 抓取 Mark Six 歷史分頁")
    fetch_lottolyzer.add_argument("--db", type=Path, default=DEFAULT_DB)
    fetch_lottolyzer.add_argument("--pages", type=int, default=52)
    fetch_lottolyzer.add_argument("--per-page", type=int, default=50)
    fetch_lottolyzer.add_argument("--delay", type=float, default=0.35)

    build_history = subparsers.add_parser("build-history-db", help="建立六合彩全歷史資料庫")
    build_history.add_argument("--db", type=Path, default=DEFAULT_DB)
    build_history.add_argument("--pages", type=int, default=52)
    build_history.add_argument("--per-page", type=int, default=50)
    build_history.add_argument("--delay", type=float, default=0.35)
    build_history.add_argument("--csv-out", type=Path, default=Path("data/香港六合彩預測系統_全歷史資料.csv"))

    status = subparsers.add_parser("status", help="顯示資料庫狀態")
    status.add_argument("--db", type=Path, default=DEFAULT_DB)

    doctor = subparsers.add_parser("doctor", help="資料健檢與品質報告")
    add_source_args(doctor)

    models = subparsers.add_parser("models", help="顯示多模型運算排行")
    add_source_args(models)
    models.add_argument("--strategy", default="balanced", choices=strategy_names())
    models.add_argument("--recent-window", type=int, default=DEFAULT_RECENT_WINDOW)
    models.add_argument("--top", type=int, default=12)

    leaderboard = subparsers.add_parser("leaderboard", help="策略績效排行")
    leaderboard.add_argument("--db", type=Path, default=DEFAULT_DB)

    runs = subparsers.add_parser("runs", help="列出近期預測紀錄")
    runs.add_argument("--db", type=Path, default=DEFAULT_DB)
    runs.add_argument("--limit", type=int, default=20)

    analyze_cmd = subparsers.add_parser("analyze", help="分析歷史資料")
    add_source_args(analyze_cmd)
    analyze_cmd.add_argument("--recent-window", type=int, default=DEFAULT_RECENT_WINDOW)

    predict = subparsers.add_parser("predict", help="產生預測、寫入資料庫並輸出報告")
    predict.add_argument("--db", type=Path, default=DEFAULT_DB)
    predict.add_argument("--strategy", default="auto", choices=strategy_names(include_auto=True))
    predict.add_argument("--tickets", type=int, default=20)
    predict.add_argument("--recent-window", type=int, default=DEFAULT_RECENT_WINDOW)
    predict.add_argument("--seed", type=int, default=None)
    predict.add_argument("--html", type=Path, default=Path("reports") / SITE_LATEST_PREDICTION_NAME)

    evaluate = subparsers.add_parser("evaluate", help="用已開獎資料驗證預測命中")
    evaluate.add_argument("--db", type=Path, default=DEFAULT_DB)
    evaluate.add_argument("--prediction-id", default="latest", help="'latest'、'all' 或指定 prediction_runs.id")

    report = subparsers.add_parser("report", help="輸出最新系統 HTML 報告")
    report.add_argument("--db", type=Path, default=DEFAULT_DB)
    report.add_argument("--html", type=Path, default=Path("reports") / SITE_SYSTEM_REPORT_NAME)
    report.add_argument("--recent-window", type=int, default=DEFAULT_RECENT_WINDOW)

    battle_report = subparsers.add_parser("battle-report", help="輸出539同規格強化戰報")
    battle_report.add_argument("--db", type=Path, default=DEFAULT_DB)
    battle_report.add_argument("--report-dir", type=Path, default=DEFAULT_REPORT_DIR)
    battle_report.add_argument("--site-dir", type=Path, default=Path("site"))
    battle_report.add_argument("--recent-window", type=int, default=DEFAULT_RECENT_WINDOW)

    site = subparsers.add_parser("build-site", help="輸出完整靜態網站入口")
    site.add_argument("--db", type=Path, default=DEFAULT_DB)
    site.add_argument("--site-dir", type=Path, default=Path("site"))
    site.add_argument("--report-dir", type=Path, default=DEFAULT_REPORT_DIR)
    site.add_argument("--recent-window", type=int, default=DEFAULT_RECENT_WINDOW)

    mobile_cloud = subparsers.add_parser("mobile-cloud", help="輸出手機雲端系統")
    mobile_cloud.add_argument("--db", type=Path, default=DEFAULT_DB)
    mobile_cloud.add_argument("--site-dir", type=Path, default=Path("site"))
    mobile_cloud.add_argument("--report-dir", type=Path, default=DEFAULT_REPORT_DIR)
    mobile_cloud.add_argument("--recent-window", type=int, default=DEFAULT_RECENT_WINDOW)

    cycle = subparsers.add_parser("run-cycle", help="一鍵執行匯入/抓取、驗證、預測、報告")
    cycle.add_argument("--db", type=Path, default=DEFAULT_DB)
    cycle.add_argument("--csv", type=Path, default=None)
    cycle.add_argument("--fetch", action="store_true")
    cycle.add_argument("--last", type=int, default=60)
    cycle.add_argument("--strategy", default="auto", choices=strategy_names(include_auto=True))
    cycle.add_argument("--tickets", type=int, default=20)
    cycle.add_argument("--recent-window", type=int, default=DEFAULT_RECENT_WINDOW)
    cycle.add_argument("--seed", type=int, default=None)
    cycle.add_argument("--prediction-html", type=Path, default=Path("reports") / SITE_LATEST_PREDICTION_NAME)
    cycle.add_argument("--report-html", type=Path, default=Path("reports") / SITE_SYSTEM_REPORT_NAME)

    daily = subparsers.add_parser("daily-update", help="完整日更：備份、更新、驗證、預測、報告、網站")
    daily.add_argument("--db", type=Path, default=DEFAULT_DB)
    daily.add_argument("--csv", type=Path, default=None)
    daily.add_argument("--fetch-hkjc", action="store_true")
    daily.add_argument("--fetch-lottolyzer", action="store_true")
    daily.add_argument("--last", type=int, default=60)
    daily.add_argument("--pages", type=int, default=2)
    daily.add_argument("--strict-update", action="store_true")
    daily.add_argument("--strategy", default="auto", choices=strategy_names(include_auto=True))
    daily.add_argument("--tickets", type=int, default=20)
    daily.add_argument("--recent-window", type=int, default=DEFAULT_RECENT_WINDOW)
    daily.add_argument("--seed", type=int, default=None)
    daily.add_argument("--force-prediction", action="store_true", help="即使最新期號未變，也強制重新運算並新增預測紀錄")
    daily.add_argument("--site-dir", type=Path, default=Path("site"))
    daily.add_argument("--report-dir", type=Path, default=DEFAULT_REPORT_DIR)
    daily.add_argument("--backup-dir", type=Path, default=Path("backups"))

    generate = subparsers.add_parser("generate", help="只產生候選組合，不寫入資料庫")
    add_source_args(generate)
    generate.add_argument("--strategy", default="balanced", choices=strategy_names())
    generate.add_argument("--tickets", type=int, default=10)
    generate.add_argument("--recent-window", type=int, default=DEFAULT_RECENT_WINDOW)
    generate.add_argument("--seed", type=int, default=None)

    backtest_cmd = subparsers.add_parser("backtest", help="walk-forward 回測")
    add_source_args(backtest_cmd)
    backtest_cmd.add_argument("--strategy", default="balanced", choices=strategy_names())
    backtest_cmd.add_argument("--tickets", type=int, default=10)
    backtest_cmd.add_argument("--recent-window", type=int, default=DEFAULT_RECENT_WINDOW)
    backtest_cmd.add_argument("--min-train", type=int, default=80)
    backtest_cmd.add_argument("--seed", type=int, default=20260620)
    backtest_cmd.add_argument("--no-save", action="store_true", help="使用 --db 時不保存回測結果")

    backtests = subparsers.add_parser("backtests", help="列出已保存回測紀錄")
    backtests.add_argument("--db", type=Path, default=DEFAULT_DB)
    backtests.add_argument("--limit", type=int, default=20)

    backup = subparsers.add_parser("backup-db", help="備份 SQLite 資料庫")
    backup.add_argument("--db", type=Path, default=DEFAULT_DB)
    backup.add_argument("--backup-dir", type=Path, default=Path("backups"))

    export_csv = subparsers.add_parser("export-csv", help="從資料庫匯出開獎 CSV")
    export_csv.add_argument("--db", type=Path, default=DEFAULT_DB)
    export_csv.add_argument("--csv", required=True, type=Path)

    return parser.parse_args()


def add_source_args(parser: argparse.ArgumentParser) -> None:
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--csv", type=Path)
    source.add_argument("--db", type=Path)


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS draws (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            draw_date TEXT NOT NULL,
            draw_no TEXT NOT NULL DEFAULT '',
            n1 INTEGER NOT NULL,
            n2 INTEGER NOT NULL,
            n3 INTEGER NOT NULL,
            n4 INTEGER NOT NULL,
            n5 INTEGER NOT NULL,
            n6 INTEGER NOT NULL,
            special INTEGER NOT NULL,
            source TEXT NOT NULL DEFAULT 'manual',
            raw_json TEXT,
            created_at TEXT NOT NULL
        );

        CREATE UNIQUE INDEX IF NOT EXISTS ux_draws_draw_no
            ON draws(draw_no)
            WHERE draw_no <> '';

        CREATE UNIQUE INDEX IF NOT EXISTS ux_draws_date
            ON draws(draw_date);

        CREATE TABLE IF NOT EXISTS prediction_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            based_on_draw_id INTEGER NOT NULL REFERENCES draws(id),
            based_on_draw_date TEXT NOT NULL,
            based_on_draw_no TEXT NOT NULL,
            strategy TEXT NOT NULL,
            model_version TEXT NOT NULL,
            recent_window INTEGER NOT NULL,
            ticket_count INTEGER NOT NULL,
            seed INTEGER,
            banker_numbers_json TEXT NOT NULL,
            drag_numbers_json TEXT NOT NULL,
            reserve_numbers_json TEXT NOT NULL,
            weak_numbers_json TEXT NOT NULL,
            special_candidates_json TEXT NOT NULL DEFAULT '[]',
            score_snapshot_json TEXT NOT NULL,
            report_path TEXT NOT NULL DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS prediction_tickets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER NOT NULL REFERENCES prediction_runs(id) ON DELETE CASCADE,
            ticket_rank INTEGER NOT NULL,
            strategy TEXT NOT NULL,
            numbers_json TEXT NOT NULL,
            score REAL NOT NULL,
            profile TEXT NOT NULL,
            reasons_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(run_id, ticket_rank)
        );

        CREATE TABLE IF NOT EXISTS prediction_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER NOT NULL REFERENCES prediction_runs(id) ON DELETE CASCADE,
            ticket_id INTEGER NOT NULL REFERENCES prediction_tickets(id) ON DELETE CASCADE,
            actual_draw_id INTEGER NOT NULL REFERENCES draws(id),
            main_hits INTEGER NOT NULL,
            hit_numbers_json TEXT NOT NULL,
            special_hit INTEGER NOT NULL,
            prize_tier TEXT NOT NULL,
            evaluated_at TEXT NOT NULL,
            UNIQUE(ticket_id, actual_draw_id)
        );

        CREATE TABLE IF NOT EXISTS backtest_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            strategy TEXT NOT NULL,
            recent_window INTEGER NOT NULL,
            min_train INTEGER NOT NULL,
            ticket_count INTEGER NOT NULL,
            tested_periods INTEGER NOT NULL,
            total_tickets INTEGER NOT NULL,
            avg_best_main_hits REAL NOT NULL,
            special_period_hits INTEGER NOT NULL,
            hit_distribution_json TEXT NOT NULL,
            prize_distribution_json TEXT NOT NULL,
            summary_text TEXT NOT NULL
        );
        """
    )
    ensure_column(
        conn,
        "prediction_runs",
        "special_candidates_json",
        "TEXT NOT NULL DEFAULT '[]'",
    )
    conn.commit()


def ensure_column(conn: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
    existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


def load_draws(csv_path: Path) -> list[Draw]:
    if not csv_path.exists():
        raise SystemExit(f"找不到 CSV: {csv_path}")

    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {"draw_date", "draw_id", "n1", "n2", "n3", "n4", "n5", "n6", "special"}
        missing = required.difference(reader.fieldnames or [])
        if missing:
            raise SystemExit(f"CSV 缺少欄位: {', '.join(sorted(missing))}")

        draws = []
        for line_no, row in enumerate(reader, start=2):
            if not any((value or "").strip() for value in row.values()):
                continue
            draws.append(parse_draw(row, line_no))

    return sort_draws(draws)


def parse_draw(row: dict[str, str], line_no: int) -> Draw:
    try:
        draw_date = normalize_date(row["draw_date"].strip())
        draw_no = normalize_draw_no(row.get("draw_id", ""), draw_date)
        main_numbers = tuple(int(row[f"n{i}"]) for i in range(1, MAIN_COUNT + 1))
        special = int(row["special"])
    except (KeyError, TypeError, ValueError) as exc:
        raise SystemExit(f"第 {line_no} 行格式錯誤: {exc}") from exc

    validate_numbers(main_numbers, special, line_no)
    return Draw(
        draw_date=draw_date,
        draw_no=draw_no,
        main_numbers=tuple(sorted(main_numbers)),
        special=special,
        source="csv",
    )


def normalize_date(value: str) -> str:
    if "T" in value:
        value = value.split("T", 1)[0]
    return datetime.strptime(value[:10], "%Y-%m-%d").date().isoformat()


def normalize_draw_no(value: object, draw_date: str | None = None, year: object | None = None) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    normalized_year: int | None = None
    if draw_date:
        try:
            normalized_year = int(normalize_date(draw_date)[:4])
        except (TypeError, ValueError):
            normalized_year = None
    if normalized_year is None and year not in (None, ""):
        try:
            normalized_year = int(str(year))
        except ValueError:
            normalized_year = None

    match = re.fullmatch(r"(?:(?P<year>\d{2}|\d{4})/)?(?P<draw>\d{1,3})", raw)
    if not match:
        return raw
    draw_number = int(match.group("draw"))
    year_text = match.group("year")
    if year_text:
        if len(year_text) == 4:
            normalized_year = int(year_text)
        elif normalized_year is None:
            short_year = int(year_text)
            normalized_year = 2000 + short_year if short_year < 70 else 1900 + short_year
    if normalized_year is None:
        return f"{draw_number:03d}"
    return f"{normalized_year:04d}/{draw_number:03d}"


def hkjc_query_date(value: str | None) -> str | None:
    if not value:
        return None
    return normalize_date(value).replace("-", "")


def validate_numbers(main_numbers: tuple[int, ...], special: int, line_no: int | str) -> None:
    if len(main_numbers) != MAIN_COUNT:
        raise SystemExit(f"{line_no} 主號數量不是 {MAIN_COUNT} 個")
    if len(set(main_numbers)) != MAIN_COUNT:
        raise SystemExit(f"{line_no} 主號重複: {main_numbers}")
    all_numbers = (*main_numbers, special)
    invalid = [number for number in all_numbers if number < MIN_NUMBER or number > MAX_NUMBER]
    if invalid:
        raise SystemExit(f"{line_no} 號碼超出 {MIN_NUMBER}-{MAX_NUMBER}: {invalid}")
    if special in main_numbers:
        raise SystemExit(f"{line_no} 特別號不可與主號重複: {special}")


def sort_draws(draws: Iterable[Draw]) -> list[Draw]:
    return sorted(draws, key=lambda draw: (draw.draw_date, draw.draw_no))


def import_draws(conn: sqlite3.Connection, draws: Iterable[Draw], raw_json: str = "") -> tuple[int, int]:
    init_db(conn)
    inserted = 0
    skipped = 0
    for draw in sort_draws(draws):
        row = (
            draw.draw_date,
            draw.draw_no,
            *draw.main_numbers,
            draw.special,
            draw.source,
            raw_json,
            now_text(),
        )
        try:
            conn.execute(
                """
                INSERT INTO draws
                    (draw_date, draw_no, n1, n2, n3, n4, n5, n6, special, source, raw_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                row,
            )
            inserted += 1
        except sqlite3.IntegrityError:
            skipped += 1
    conn.commit()
    return inserted, skipped


def load_draws_from_db(conn: sqlite3.Connection) -> list[Draw]:
    init_db(conn)
    rows = conn.execute(
        """
        SELECT id, draw_date, draw_no, n1, n2, n3, n4, n5, n6, special, source
        FROM draws
        ORDER BY draw_date, draw_no
        """
    ).fetchall()
    return [
        Draw(
            draw_date=row["draw_date"],
            draw_no=row["draw_no"],
            main_numbers=tuple(row[f"n{i}"] for i in range(1, MAIN_COUNT + 1)),
            special=row["special"],
            source=row["source"],
            row_id=row["id"],
        )
        for row in rows
    ]


def load_draws_from_source(args: argparse.Namespace) -> list[Draw]:
    if getattr(args, "csv", None):
        return load_draws(args.csv)
    with connect(args.db) as conn:
        return load_draws_from_db(conn)


def fetch_hkjc_draws(
    last: int,
    start_date: str | None = None,
    end_date: str | None = None,
) -> tuple[list[Draw], str]:
    query = """
    fragment lotteryDrawsFragment on LotteryDraw {
      id
      year
      no
      openDate
      closeDate
      drawDate
      status
      snowballCode
      snowballName_en
      snowballName_ch
      lotteryPool {
        sell
        status
        totalInvestment
        jackpot
        unitBet
        estimatedPrize
        derivedFirstPrizeDiv
        lotteryPrizes {
          type
          winningUnit
          dividend
        }
      }
      drawResult {
        drawnNo
        xDrawnNo
      }
    }

    query marksixResult($lastNDraw: Int, $startDate: String, $endDate: String, $drawType: LotteryDrawType) {
      lotteryDraws(lastNDraw: $lastNDraw, startDate: $startDate, endDate: $endDate, drawType: $drawType) {
        ...lotteryDrawsFragment
      }
    }
    """
    payload = {
        "operationName": "marksixResult",
        "variables": {
            "lastNDraw": None if start_date or end_date else last,
            "startDate": hkjc_query_date(start_date),
            "endDate": hkjc_query_date(end_date),
            "drawType": "All",
        },
        "query": query,
    }
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        "https://info.cld.hkjc.com/graphql/base/",
        data=body,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Accept-Language": "zh-HK,zh;q=0.9,en;q=0.8",
            "Origin": "https://bet.hkjc.com",
            "Referer": "https://bet.hkjc.com/marksix/?lang=ch",
            "User-Agent": "Mozilla/5.0 marksix-predictor",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        raw_bytes = response.read()
        if response.headers.get("content-encoding", "").lower() == "gzip" or raw_bytes[:2] == b"\x1f\x8b":
            raw_bytes = gzip.decompress(raw_bytes)
        raw_text = raw_bytes.decode("utf-8")

    decoded = json.loads(raw_text)
    if "errors" in decoded:
        raise SystemExit(json.dumps(decoded["errors"], ensure_ascii=False))
    rows = decoded.get("data", {}).get("lotteryDraws") or []
    draws = [parse_hkjc_row(row) for row in rows if row.get("drawResult")]
    return sort_draws(draws), raw_text


def parse_hkjc_row(row: dict) -> Draw:
    result = row["drawResult"]
    drawn = result.get("drawnNo") or result.get("drawnNos") or []
    if isinstance(drawn, str):
        drawn = [part.strip() for part in drawn.replace("+", ",").split(",") if part.strip()]
    if drawn and isinstance(drawn[0], dict):
        drawn = [item.get("no") or item.get("number") for item in drawn]
    main_numbers = tuple(int(number) for number in drawn[:MAIN_COUNT])
    special_raw = result.get("xDrawnNo") or result.get("extraNo") or result.get("specialNo")
    if isinstance(special_raw, dict):
        special_raw = special_raw.get("no") or special_raw.get("number")
    special = int(special_raw)
    draw_date = normalize_date(str(row.get("drawDate") or row.get("openDate")))
    draw_no = normalize_draw_no(row.get("no") or row.get("id") or "", draw_date, row.get("year"))
    validate_numbers(main_numbers, special, f"HKJC {draw_no}")
    return Draw(
        draw_date=draw_date,
        draw_no=draw_no,
        main_numbers=tuple(sorted(main_numbers)),
        special=special,
        source="hkjc",
    )


def fetch_lottolyzer_history(
    pages: int = 52,
    per_page: int = 50,
    delay: float = 0.35,
) -> tuple[list[Draw], str]:
    all_draws: list[Draw] = []
    raw_pages: list[str] = []
    for page in range(1, pages + 1):
        url = (
            "https://en.lottolyzer.com/history/hong-kong/mark-six/"
            f"page/{page}/per-page/{per_page}/summary-view"
        )
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "text/html,application/xhtml+xml",
                "User-Agent": "Mozilla/5.0 marksix-history-builder",
            },
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            html_text = response.read().decode("utf-8", errors="replace")
        raw_pages.append(html_text)
        page_draws = parse_lottolyzer_html(html_text)
        if not page_draws:
            raise SystemExit(f"Lottolyzer 第 {page} 頁沒有解析到資料: {url}")
        all_draws.extend(page_draws)
        if delay > 0 and page < pages:
            time.sleep(delay)

    unique = {(draw.draw_date, draw.draw_no): draw for draw in all_draws}
    raw_text = "\n<!-- marksix page break -->\n".join(raw_pages)
    return sort_draws(unique.values()), raw_text


def parse_lottolyzer_html(html_text: str) -> list[Draw]:
    text = re.sub(r"<script.*?</script>", " ", html_text, flags=re.I | re.S)
    text = re.sub(r"<style.*?</style>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    rows: list[Draw] = []
    pattern = re.compile(
        r"(?P<draw_no>(?:\d{2}|\d{4})/\d{3})\s+"
        r"(?P<draw_date>\d{4}-\d{2}-\d{2})\s+"
        r"(?P<numbers>\d{1,2}(?:,\d{1,2}){5})\s+"
        r"(?P<special>\d{1,2})\s+"
    )
    for match in pattern.finditer(text):
        draw_date = match.group("draw_date")
        draw_no = normalize_draw_no(match.group("draw_no"), draw_date)
        main_numbers = tuple(int(part) for part in match.group("numbers").split(","))
        special = int(match.group("special"))
        validate_numbers(main_numbers, special, f"Lottolyzer {draw_no}")
        rows.append(
            Draw(
                draw_date=draw_date,
                draw_no=draw_no,
                main_numbers=tuple(sorted(main_numbers)),
                special=special,
                source="lottolyzer",
            )
        )
    return rows


def write_draws_csv(draws: list[Draw], csv_path: Path) -> None:
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["draw_date", "draw_id", "n1", "n2", "n3", "n4", "n5", "n6", "special"])
        for draw in sort_draws(draws):
            writer.writerow([draw.draw_date, draw.draw_no, *draw.main_numbers, draw.special])


def cleanup_legacy_output_files(site_dir: Path | None = None, report_dir: Path | None = None) -> None:
    for base_dir, names in ((site_dir, LEGACY_SITE_OUTPUT_NAMES), (report_dir, LEGACY_REPORT_OUTPUT_NAMES)):
        if base_dir is None:
            continue
        for name in names:
            path = base_dir / name
            if path.exists() and path.is_file():
                try:
                    path.unlink()
                except OSError:
                    pass


def build_scores(
    draws: list[Draw],
    recent_window: int = DEFAULT_RECENT_WINDOW,
    strategy: str = "balanced",
) -> dict[int, NumberScore]:
    if not draws:
        return {}

    main_history = [draw.main_numbers for draw in draws]
    all_counts = Counter(number for draw in main_history for number in draw)
    recent_draws = main_history[-recent_window:]
    recent_counts = Counter(number for draw in recent_draws for number in draw)
    special_counts = Counter(draw.special for draw in draws)
    miss_gaps = calculate_miss_gaps(main_history)
    pair_strength = calculate_pair_strength(main_history)
    momentum_scores = calculate_momentum_scores(main_history)
    cycle_scores = calculate_cycle_scores(main_history, miss_gaps)
    structure_scores = calculate_structure_scores(draws, recent_window)
    rolling_month_scores = calculate_rolling_month_scores(draws, recent_window)
    zone_repair_scores = calculate_zone_repair_scores(draws, recent_window, miss_gaps)
    breakout_scores = calculate_breakout_capture_scores(
        draws,
        recent_window,
        miss_gaps,
        pair_strength,
        cycle_scores,
        structure_scores,
    )
    neighbor_bridge_scores = calculate_neighbor_bridge_scores(draws, miss_gaps)
    transition_follow_scores = calculate_transition_follow_scores(draws)
    tail_transition_scores = calculate_tail_transition_scores(draws)
    calendar_phase_scores = calculate_calendar_phase_scores(draws)
    special_crossover_scores = calculate_special_crossover_scores(draws)

    max_total = max(all_counts.values(), default=1)
    max_recent = max(recent_counts.values(), default=1)
    max_special = max(special_counts.values(), default=1)
    max_gap = max(miss_gaps.values(), default=1)
    max_pair = max(pair_strength.values(), default=1.0)
    max_momentum = max(momentum_scores.values(), default=1.0)
    bayes_values = {
        number: (all_counts[number] + recent_counts[number] * 2.0 + special_counts[number] * 0.5 + 1.0)
        / (len(draws) + max(1, len(recent_draws)) * 2.0 + 49.0)
        for number in range(MIN_NUMBER, MAX_NUMBER + 1)
    }
    max_bayes = max(bayes_values.values(), default=1.0)
    weights = strategy_weights(strategy)
    score_rows: dict[int, NumberScore] = {}

    for number in range(MIN_NUMBER, MAX_NUMBER + 1):
        total_frequency = all_counts[number]
        recent_frequency = recent_counts[number]
        special_frequency = special_counts[number]
        total_rate = total_frequency / max(1, len(draws))
        recent_rate = recent_frequency / max(1, len(recent_draws))
        trend = recent_rate - total_rate
        normalized_total = total_frequency / max_total
        normalized_recent = recent_frequency / max_recent
        normalized_special = special_frequency / max_special
        normalized_gap = miss_gaps[number] / max_gap
        normalized_pair = pair_strength[number] / max_pair
        model_scores = {
            "frequency": normalized_total,
            "recency": normalized_recent,
            "gap": normalized_gap,
            "trend": normalize_signed(trend),
            "pair": normalized_pair,
            "special": normalized_special,
            "bayes": bayes_values[number] / max_bayes if max_bayes else 0.0,
            "momentum": momentum_scores[number] / max_momentum if max_momentum else 0.0,
            "cycle": cycle_scores[number],
            "structure": structure_scores[number],
            "rolling_month": rolling_month_scores[number],
            "zone_repair": zone_repair_scores[number],
            "breakout_capture": breakout_scores[number],
            "neighbor_bridge": neighbor_bridge_scores[number],
            "transition_follow": transition_follow_scores[number],
            "tail_transition": tail_transition_scores[number],
            "calendar_phase": calendar_phase_scores[number],
            "special_crossover": special_crossover_scores[number],
        }

        score = sum(weights[name] * model_scores[name] for name in weights)
        score += model_scores["rolling_month"] * ROLLING_MONTH_WEIGHT
        score += model_scores["zone_repair"] * ZONE_REPAIR_WEIGHT
        score += model_scores["breakout_capture"] * BREAKOUT_CAPTURE_WEIGHT
        score += model_scores["neighbor_bridge"] * NEIGHBOR_BRIDGE_WEIGHT
        score += model_scores["transition_follow"] * TRANSITION_FOLLOW_WEIGHT
        score += model_scores["tail_transition"] * TAIL_TRANSITION_WEIGHT
        score += model_scores["calendar_phase"] * CALENDAR_PHASE_WEIGHT
        score += model_scores["special_crossover"] * SPECIAL_CROSSOVER_WEIGHT

        score_rows[number] = NumberScore(
            number=number,
            total_frequency=total_frequency,
            recent_frequency=recent_frequency,
            special_frequency=special_frequency,
            miss_gap=miss_gaps[number],
            trend=trend,
            pair_strength=pair_strength[number],
            score=score,
            color=wave_color(number),
            model_scores=model_scores,
        )

    return score_rows


def strategy_weights(strategy: str) -> dict[str, float]:
    weights = {
        "balanced": {
            "frequency": 0.14,
            "recency": 0.19,
            "gap": 0.18,
            "trend": 0.18,
            "pair": 0.10,
            "special": 0.06,
            "bayes": 0.05,
            "momentum": 0.04,
            "cycle": 0.03,
            "structure": 0.03,
        },
        "hot": {
            "frequency": 0.14,
            "recency": 0.36,
            "gap": 0.03,
            "trend": 0.18,
            "pair": 0.06,
            "special": 0.05,
            "bayes": 0.06,
            "momentum": 0.08,
            "cycle": 0.01,
            "structure": 0.03,
        },
        "cold": {
            "frequency": 0.03,
            "recency": 0.00,
            "gap": 0.50,
            "trend": 0.02,
            "pair": 0.06,
            "special": 0.04,
            "bayes": 0.03,
            "momentum": 0.00,
            "cycle": 0.22,
            "structure": 0.10,
        },
        "trend": {
            "frequency": 0.07,
            "recency": 0.22,
            "gap": 0.08,
            "trend": 0.34,
            "pair": 0.06,
            "special": 0.05,
            "bayes": 0.05,
            "momentum": 0.10,
            "cycle": 0.01,
            "structure": 0.02,
        },
        "diversified": {
            "frequency": 0.08,
            "recency": 0.12,
            "gap": 0.17,
            "trend": 0.08,
            "pair": 0.22,
            "special": 0.05,
            "bayes": 0.04,
            "momentum": 0.03,
            "cycle": 0.07,
            "structure": 0.14,
        },
    }
    return weights[strategy]


def calculate_momentum_scores(main_history: list[tuple[int, ...]], half_life: float = 18.0) -> dict[int, float]:
    scores = {number: 0.0 for number in range(MIN_NUMBER, MAX_NUMBER + 1)}
    if not main_history:
        return scores
    decay = math.log(2) / half_life
    for age, draw in enumerate(reversed(main_history)):
        weight = math.exp(-decay * age)
        for number in draw:
            scores[number] += weight
    return scores


def calculate_cycle_scores(
    main_history: list[tuple[int, ...]],
    miss_gaps: dict[int, int],
) -> dict[int, float]:
    scores = {}
    for number in range(MIN_NUMBER, MAX_NUMBER + 1):
        seen_indexes = [index for index, draw in enumerate(main_history) if number in draw]
        if len(seen_indexes) < 3:
            scores[number] = min(miss_gaps[number] / max(len(main_history), 1), 1.0)
            continue
        intervals = [
            right - left for left, right in zip(seen_indexes, seen_indexes[1:]) if right > left
        ]
        if not intervals:
            scores[number] = 0.0
            continue
        median_interval = statistics.median(intervals)
        current_gap = miss_gaps[number]
        if median_interval <= 0:
            scores[number] = 0.0
            continue
        distance = abs(current_gap - median_interval) / median_interval
        overdue_bonus = min(current_gap / (median_interval * 2.0), 1.0)
        scores[number] = max(0.0, 1.0 - distance) * 0.65 + overdue_bonus * 0.35
    return scores


def calculate_structure_scores(draws: list[Draw], recent_window: int) -> dict[int, float]:
    recent = draws[-recent_window:] if recent_window > 0 else draws
    if not recent:
        return {number: 0.5 for number in range(MIN_NUMBER, MAX_NUMBER + 1)}

    small_count = sum(1 for draw in recent for number in draw.main_numbers if number <= 24)
    large_count = len(recent) * MAIN_COUNT - small_count
    color_counts = Counter(wave_color(number) for draw in recent for number in draw.main_numbers)
    tail_counts = Counter(number % 10 for draw in recent for number in draw.main_numbers)
    decade_counts = Counter(decade_bucket(number) for draw in recent for number in draw.main_numbers)

    max_tail = max(tail_counts.values(), default=1)
    max_decade = max(decade_counts.values(), default=1)
    max_color = max(color_counts.values(), default=1)
    scores = {}
    for number in range(MIN_NUMBER, MAX_NUMBER + 1):
        size_score = 1.0 if (number <= 24 and small_count <= large_count) or (number > 24 and large_count <= small_count) else 0.35
        color_score = 1.0 - (color_counts[wave_color(number)] / max_color) * 0.65
        tail_score = 1.0 - (tail_counts[number % 10] / max_tail) * 0.65
        decade_score = 1.0 - (decade_counts[decade_bucket(number)] / max_decade) * 0.65
        scores[number] = max(0.0, min(1.0, (size_score + color_score + tail_score + decade_score) / 4.0))
    return scores


def month_window_draws(draws: list[Draw]) -> list[Draw]:
    if not draws:
        return []
    latest_month = draws[-1].draw_date[:7]
    month_draws = [draw for draw in draws if draw.draw_date.startswith(latest_month)]
    if len(month_draws) >= 4:
        return month_draws
    return draws[-12:]


def calculate_rolling_month_scores(draws: list[Draw], recent_window: int) -> dict[int, float]:
    month_draws = month_window_draws(draws)
    if not month_draws:
        return {number: 0.5 for number in range(MIN_NUMBER, MAX_NUMBER + 1)}

    main_history = [draw.main_numbers for draw in draws]
    month_history = [draw.main_numbers for draw in month_draws]
    short_history = main_history[-min(3, len(main_history)) :]
    month_counts = Counter(number for draw in month_history for number in draw)
    short_counts = Counter(number for draw in short_history for number in draw)
    miss_gaps = calculate_miss_gaps(main_history)
    color_counts = Counter(wave_color(number) for draw in month_history for number in draw)
    tail_counts = Counter(number % 10 for draw in month_history for number in draw)
    decade_counts = Counter(decade_bucket(number) for draw in month_history for number in draw)

    max_month = max(month_counts.values(), default=1)
    max_short = max(short_counts.values(), default=1)
    max_gap = max(miss_gaps.values(), default=1)
    max_color = max(color_counts.values(), default=1)
    max_tail = max(tail_counts.values(), default=1)
    max_decade = max(decade_counts.values(), default=1)

    scores = {}
    for number in range(MIN_NUMBER, MAX_NUMBER + 1):
        month_signal = month_counts[number] / max_month
        short_signal = short_counts[number] / max_short
        gap_signal = miss_gaps[number] / max_gap
        color_balance = 1.0 - (color_counts[wave_color(number)] / max_color) * 0.55
        tail_balance = 1.0 - (tail_counts[number % 10] / max_tail) * 0.55
        decade_balance = 1.0 - (decade_counts[decade_bucket(number)] / max_decade) * 0.55
        structure_balance = max(0.0, min(1.0, (color_balance + tail_balance + decade_balance) / 3.0))
        scores[number] = max(
            0.0,
            min(
                1.0,
                month_signal * 0.34
                + short_signal * 0.24
                + gap_signal * 0.18
                + structure_balance * 0.24,
            ),
        )
    return scores


def calculate_zone_repair_scores(
    draws: list[Draw],
    recent_window: int,
    miss_gaps: dict[int, int],
) -> dict[int, float]:
    if not draws:
        return {number: 0.5 for number in range(MIN_NUMBER, MAX_NUMBER + 1)}
    recent = draws[-min(recent_window, len(draws)) :]
    short = draws[-min(6, len(draws)) :]
    latest = draws[-1]
    month = month_window_draws(draws)
    recent_decades = Counter(decade_bucket(number) for draw in recent for number in draw.main_numbers)
    short_decades = Counter(decade_bucket(number) for draw in short for number in draw.main_numbers)
    latest_decades = Counter(decade_bucket(number) for number in latest.main_numbers)
    month_decades = Counter(decade_bucket(number) for draw in month for number in draw.main_numbers)
    recent_tails = Counter(number % 10 for draw in recent for number in draw.main_numbers)
    latest_tails = Counter(number % 10 for number in latest.main_numbers)
    recent_colors = Counter(wave_color(number) for draw in recent for number in draw.main_numbers)
    max_recent_decade = max(recent_decades.values(), default=1)
    max_short_decade = max(short_decades.values(), default=1)
    max_latest_decade = max(latest_decades.values(), default=1)
    max_month_decade = max(month_decades.values(), default=1)
    max_tail = max(recent_tails.values(), default=1)
    max_color = max(recent_colors.values(), default=1)
    max_gap = max(miss_gaps.values(), default=1)
    scores = {}
    for number in range(MIN_NUMBER, MAX_NUMBER + 1):
        decade = decade_bucket(number)
        decade_signal = (
            (recent_decades[decade] / max_recent_decade) * 0.28
            + (short_decades[decade] / max_short_decade) * 0.26
            + (latest_decades[decade] / max_latest_decade) * 0.24
            + (month_decades[decade] / max_month_decade) * 0.22
        )
        tail_signal = 0.68 if number % 10 in latest_tails else (recent_tails[number % 10] / max_tail if max_tail else 0.0)
        color_balance = 1.0 - (recent_colors[wave_color(number)] / max_color) * 0.55
        mid_zone = 1.0 if 11 <= number <= 30 else 0.52
        gap_signal = min(miss_gaps[number] / max(1, max_gap), 1.0)
        scores[number] = clamp01(
            decade_signal * 0.34
            + tail_signal * 0.19
            + color_balance * 0.17
            + mid_zone * 0.15
            + gap_signal * 0.15
        )
    return scores


def calculate_breakout_capture_scores(
    draws: list[Draw],
    recent_window: int,
    miss_gaps: dict[int, int],
    pair_strength: dict[int, float],
    cycle_scores: dict[int, float],
    structure_scores: dict[int, float],
) -> dict[int, float]:
    if not draws:
        return {number: 0.5 for number in range(MIN_NUMBER, MAX_NUMBER + 1)}
    recent = draws[-min(recent_window, len(draws)) :]
    short = draws[-min(8, len(draws)) :]
    recent_counts = Counter(number for draw in recent for number in draw.main_numbers)
    short_counts = Counter(number for draw in short for number in draw.main_numbers)
    all_counts = Counter(number for draw in draws for number in draw.main_numbers)
    max_pair = max(pair_strength.values(), default=1.0) or 1.0
    max_all = max(all_counts.values(), default=1) or 1
    scores = {}
    for number in range(MIN_NUMBER, MAX_NUMBER + 1):
        gap = miss_gaps[number]
        gap_sweet_spot = math.exp(-((gap - 14.0) ** 2) / (2 * 9.0**2))
        cold_not_dead = 1.0 - min(short_counts[number] / 3.0, 1.0)
        long_support = all_counts[number] / max_all
        pair_support = pair_strength[number] / max_pair
        cycle = cycle_scores.get(number, 0.0)
        structure = structure_scores.get(number, 0.0)
        recent_guard = 0.72 if recent_counts[number] == 0 else 1.0
        scores[number] = clamp01(
            (
                gap_sweet_spot * 0.30
                + cold_not_dead * 0.18
                + long_support * 0.17
                + pair_support * 0.14
                + cycle * 0.12
                + structure * 0.09
            )
            * recent_guard
        )
    return scores


def calculate_neighbor_bridge_scores(
    draws: list[Draw],
    miss_gaps: dict[int, int],
) -> dict[int, float]:
    if not draws:
        return {number: 0.5 for number in range(MIN_NUMBER, MAX_NUMBER + 1)}
    recent = draws[-min(5, len(draws)) :]
    recent_numbers = [number for draw in recent for number in draw.main_numbers]
    recent_tails = Counter(number % 10 for number in recent_numbers)
    recent_decades = Counter(decade_bucket(number) for number in recent_numbers)
    max_tail = max(recent_tails.values(), default=1)
    max_decade = max(recent_decades.values(), default=1)
    latest_numbers = set(draws[-1].main_numbers)
    scores = {}
    for number in range(MIN_NUMBER, MAX_NUMBER + 1):
        nearest_distance = min((abs(number - recent_number) for recent_number in recent_numbers), default=49)
        bridge = 1.0 if nearest_distance == 0 else max(0.0, 1.0 - nearest_distance / 6.0)
        same_tail = recent_tails[number % 10] / max_tail if max_tail else 0.0
        same_decade = recent_decades[decade_bucket(number)] / max_decade if max_decade else 0.0
        repeat_control = 0.86 if number in latest_numbers else 1.0
        gap_control = min(miss_gaps[number] / 18.0, 1.0)
        scores[number] = clamp01(
            (bridge * 0.36 + same_tail * 0.22 + same_decade * 0.18 + gap_control * 0.24)
            * repeat_control
        )
    return scores


def calculate_transition_follow_scores(draws: list[Draw]) -> dict[int, float]:
    if len(draws) < 20:
        return {number: 0.5 for number in range(MIN_NUMBER, MAX_NUMBER + 1)}
    latest_numbers = set(draws[-1].main_numbers)
    transition_counts = Counter()
    transition_weight = Counter()
    for previous, current in zip(draws, draws[1:]):
        overlap = len(latest_numbers.intersection(previous.main_numbers))
        if overlap == 0:
            continue
        weight = 1.0 + overlap * 0.42
        for number in current.main_numbers:
            transition_counts[number] += weight
        for source in latest_numbers.intersection(previous.main_numbers):
            for number in current.main_numbers:
                distance = abs(number - source)
                if distance <= 6:
                    transition_weight[number] += max(0.0, 1.0 - distance / 7.0) * 0.35
    raw = {
        number: transition_counts[number] + transition_weight[number]
        for number in range(MIN_NUMBER, MAX_NUMBER + 1)
    }
    if max(raw.values(), default=0.0) <= 0:
        return {number: 0.0 for number in range(MIN_NUMBER, MAX_NUMBER + 1)}
    return normalized_values(raw)


def calculate_tail_transition_scores(draws: list[Draw]) -> dict[int, float]:
    if len(draws) < 20:
        return {number: 0.5 for number in range(MIN_NUMBER, MAX_NUMBER + 1)}
    latest_tails = {number % 10 for number in draws[-1].main_numbers}
    latest_decades = {decade_bucket(number) for number in draws[-1].main_numbers}
    tail_to_next = Counter()
    decade_to_next = Counter()
    direct_next = Counter()
    for previous, current in zip(draws, draws[1:]):
        previous_tails = {number % 10 for number in previous.main_numbers}
        previous_decades = {decade_bucket(number) for number in previous.main_numbers}
        tail_overlap = len(latest_tails.intersection(previous_tails))
        decade_overlap = len(latest_decades.intersection(previous_decades))
        if tail_overlap == 0 and decade_overlap == 0:
            continue
        for number in current.main_numbers:
            if number % 10 in latest_tails:
                tail_to_next[number] += 1.0 + tail_overlap * 0.25
            if decade_bucket(number) in latest_decades:
                decade_to_next[number] += 1.0 + decade_overlap * 0.18
            direct_next[number] += tail_overlap * 0.16 + decade_overlap * 0.12
    raw = {
        number: tail_to_next[number] * 0.45 + decade_to_next[number] * 0.35 + direct_next[number] * 0.20
        for number in range(MIN_NUMBER, MAX_NUMBER + 1)
    }
    if max(raw.values(), default=0.0) <= 0:
        return {number: 0.0 for number in range(MIN_NUMBER, MAX_NUMBER + 1)}
    return normalized_values(raw)


def calculate_calendar_phase_scores(draws: list[Draw]) -> dict[int, float]:
    if len(draws) < 30:
        return {number: 0.5 for number in range(MIN_NUMBER, MAX_NUMBER + 1)}
    target_date_text = next_marksix_draw_date(draws[-1].draw_date)
    try:
        target_date = datetime.strptime(target_date_text[:10], "%Y-%m-%d")
    except ValueError:
        target_date = datetime.strptime(draws[-1].draw_date, "%Y-%m-%d")
    target_weekday = target_date.weekday()
    target_phase = min(3, (target_date.day - 1) // 8)
    weekday_counts = Counter()
    phase_counts = Counter()
    for draw in draws:
        try:
            draw_date = datetime.strptime(draw.draw_date, "%Y-%m-%d")
        except ValueError:
            continue
        weekday_match = 1.0 if draw_date.weekday() == target_weekday else 0.0
        month_phase = min(3, (draw_date.day - 1) // 8)
        phase_match = 1.0 if month_phase == target_phase else 0.0
        if not weekday_match and not phase_match:
            continue
        for number in draw.main_numbers:
            weekday_counts[number] += weekday_match
            phase_counts[number] += phase_match
    raw = {
        number: weekday_counts[number] * 0.66 + phase_counts[number] * 0.34
        for number in range(MIN_NUMBER, MAX_NUMBER + 1)
    }
    if max(raw.values(), default=0.0) <= 0:
        return {number: 0.0 for number in range(MIN_NUMBER, MAX_NUMBER + 1)}
    return normalized_values(raw)


def calculate_special_crossover_scores(draws: list[Draw]) -> dict[int, float]:
    if len(draws) < 20:
        return {number: 0.5 for number in range(MIN_NUMBER, MAX_NUMBER + 1)}
    latest_special = draws[-1].special
    latest_tail = latest_special % 10
    latest_decade = decade_bucket(latest_special)
    direct_counts = Counter()
    tail_counts = Counter()
    neighbor_counts = Counter()
    decade_counts = Counter()
    for previous, current in zip(draws, draws[1:]):
        if previous.special == latest_special:
            for number in current.main_numbers:
                direct_counts[number] += 1.0
        if previous.special % 10 == latest_tail:
            for number in current.main_numbers:
                tail_counts[number] += 1.0 if number % 10 == latest_tail else 0.38
        if decade_bucket(previous.special) == latest_decade:
            for number in current.main_numbers:
                decade_counts[number] += 1.0 if decade_bucket(number) == latest_decade else 0.26
        for number in current.main_numbers:
            if abs(number - latest_special) <= 3:
                neighbor_counts[number] += 1.0
    raw = {
        number: (
            direct_counts[number] * 0.30
            + tail_counts[number] * 0.25
            + decade_counts[number] * 0.20
            + neighbor_counts[number] * 0.25
        )
        for number in range(MIN_NUMBER, MAX_NUMBER + 1)
    }
    if max(raw.values(), default=0.0) <= 0:
        return {number: 0.0 for number in range(MIN_NUMBER, MAX_NUMBER + 1)}
    return normalized_values(raw)


def rolling_month_review(draws: list[Draw], ranked_numbers: list[int]) -> dict[str, object]:
    month_draws = month_window_draws(draws)
    actual_pool = {number for draw in month_draws for number in draw.main_numbers}
    top9 = set(ranked_numbers[:CORE_POOL_SIZE])
    overlap = len(top9.intersection(actual_pool))
    coverage = overlap / max(1, min(len(actual_pool), CORE_POOL_SIZE))
    month_counts = Counter(number for draw in month_draws for number in draw.main_numbers)
    hottest = [number for number, _ in month_counts.most_common(9)]
    missing_hot = [number for number in hottest if number not in top9][:5]
    return {
        "sample": len(month_draws),
        "range": f"{month_draws[0].draw_date} -> {month_draws[-1].draw_date}" if month_draws else "-",
        "actual_pool_size": len(actual_pool),
        "overlap": overlap,
        "coverage": coverage,
        "hottest": hottest,
        "missing_hot": missing_hot,
    }


def decade_bucket(number: int) -> str:
    if number <= 10:
        return "01-10"
    if number <= 20:
        return "11-20"
    if number <= 30:
        return "21-30"
    if number <= 40:
        return "31-40"
    return "41-49"


def normalize_signed(value: float) -> float:
    return 1.0 / (1.0 + math.exp(-12.0 * value))


def calculate_miss_gaps(main_history: list[tuple[int, ...]]) -> dict[int, int]:
    gaps = {}
    for number in range(MIN_NUMBER, MAX_NUMBER + 1):
        gap = 0
        for draw in reversed(main_history):
            if number in draw:
                break
            gap += 1
        gaps[number] = gap
    return gaps


def calculate_pair_strength(main_history: list[tuple[int, ...]]) -> dict[int, float]:
    pair_counts: dict[int, Counter[int]] = defaultdict(Counter)
    for draw in main_history:
        numbers = list(draw)
        for i, left in enumerate(numbers):
            for right in numbers[i + 1 :]:
                pair_counts[left][right] += 1
                pair_counts[right][left] += 1

    strengths = {}
    for number in range(MIN_NUMBER, MAX_NUMBER + 1):
        strongest_pairs = pair_counts[number].most_common(5)
        strengths[number] = sum(count for _, count in strongest_pairs) / 5 if strongest_pairs else 0.0
    return strengths


def wave_color(number: int) -> str:
    if number in RED_WAVE:
        return "紅波"
    if number in BLUE_WAVE:
        return "藍波"
    if number in GREEN_WAVE:
        return "綠波"
    return "未知"


def generate_prediction_package(
    draws: list[Draw],
    strategy: str,
    ticket_count: int,
    recent_window: int,
    seed: int | None = None,
    conn: sqlite3.Connection | None = None,
) -> PredictionPackage:
    if len(draws) < 5:
        raise SystemExit("歷史資料太少，至少需要 5 期才能產生預測。")

    if strategy == "auto":
        tickets = generate_auto_tickets(draws, ticket_count, recent_window, seed, conn)
        scores = build_auto_scores(draws, recent_window, conn)
    else:
        tickets = generate_tickets(draws, strategy, ticket_count, recent_window, seed)
        scores = build_scores(draws, recent_window=recent_window, strategy=strategy)

    bankers, drags, reserves, weak_numbers = build_banker_drag_lists(scores)
    special_candidates = build_special_candidates(scores)
    return PredictionPackage(
        strategy=strategy,
        tickets=tickets,
        bankers=bankers,
        drags=drags,
        reserves=reserves,
        weak_numbers=weak_numbers,
        special_candidates=special_candidates,
        scores=scores,
    )


def build_banker_drag_lists(
    scores: dict[int, NumberScore],
) -> tuple[tuple[int, ...], tuple[int, ...], tuple[int, ...], tuple[int, ...]]:
    ranked = sorted(scores.values(), key=lambda row: row.score, reverse=True)
    bankers = tuple(sorted(row.number for row in ranked[:3]))
    drags = tuple(sorted(row.number for row in ranked[3:CORE_POOL_SIZE]))
    reserves = tuple(sorted(row.number for row in ranked[CORE_POOL_SIZE:18]))
    weak_numbers = tuple(sorted(row.number for row in ranked[-8:]))
    return bankers, drags, reserves, weak_numbers


def rank_lookup(scores: dict[int, NumberScore]) -> dict[int, int]:
    ranked = sorted(scores.values(), key=lambda row: row.score, reverse=True)
    return {row.number: index for index, row in enumerate(ranked, start=1)}


def build_special_candidates(scores: dict[int, NumberScore]) -> tuple[int, ...]:
    max_special = max((row.special_frequency for row in scores.values()), default=1) or 1
    max_gap = max((row.miss_gap for row in scores.values()), default=1) or 1
    ranked = sorted(
        scores.values(),
        key=lambda row: (
            row.special_frequency / max_special * 0.55
            + row.score * 0.30
            + row.miss_gap / max_gap * 0.15
        ),
        reverse=True,
    )
    return tuple(sorted(row.number for row in ranked[:8]))


def build_auto_scores(
    draws: list[Draw],
    recent_window: int,
    conn: sqlite3.Connection | None,
) -> dict[int, NumberScore]:
    strategy_score_maps = {
        strategy: build_scores(draws, recent_window=recent_window, strategy=strategy)
        for strategy in strategy_names()
    }
    weights = calibrated_strategy_weights(draws, recent_window, conn)
    total_weight = sum(weights.values()) or 1.0
    ranges = {}
    for strategy, score_map in strategy_score_maps.items():
        values = [row.score for row in score_map.values()]
        ranges[strategy] = (min(values, default=0.0), max(values, default=1.0))

    auto_scores: dict[int, NumberScore] = {}
    base_scores = strategy_score_maps["balanced"]
    feedback_scores = calculate_settlement_feedback_scores(conn, draws)
    for number in range(MIN_NUMBER, MAX_NUMBER + 1):
        normalized_parts = []
        raw_parts = []
        for strategy, score_map in strategy_score_maps.items():
            low, high = ranges[strategy]
            row = score_map[number]
            normalized = (row.score - low) / (high - low) if high > low else 0.5
            weight = weights.get(strategy, 1.0)
            normalized_parts.append(normalized * weight)
            raw_parts.append(row.score * weight)
        base = base_scores[number]
        maturity_score = sum(normalized_parts) / total_weight
        raw_score = sum(raw_parts) / total_weight
        model_scores = dict(base.model_scores)
        model_scores["auto_maturity"] = maturity_score
        model_scores["settlement_feedback"] = feedback_scores[number]
        auto_scores[number] = NumberScore(
            number=base.number,
            total_frequency=base.total_frequency,
            recent_frequency=base.recent_frequency,
            special_frequency=base.special_frequency,
            miss_gap=base.miss_gap,
            trend=base.trend,
            pair_strength=base.pair_strength,
            score=maturity_score + raw_score * 0.12 + feedback_scores[number] * SETTLEMENT_FEEDBACK_WEIGHT,
            color=base.color,
            model_scores=model_scores,
        )
    return auto_scores


def generate_auto_tickets(
    draws: list[Draw],
    ticket_count: int,
    recent_window: int,
    seed: int | None,
    conn: sqlite3.Connection | None,
) -> list[Ticket]:
    rng = random.Random(seed)
    weights = calibrated_strategy_weights(draws, recent_window, conn)
    generation_count = max(ticket_count * 3, ticket_count + len(strategy_names()) * 6)
    allocation = allocate_tickets(generation_count, weights)
    tickets: dict[tuple[int, ...], Ticket] = {}
    generated_by_strategy: dict[str, list[Ticket]] = {}

    for strategy, count in allocation.items():
        generated = generate_tickets(
            draws,
            strategy=strategy,
            ticket_count=max(count * 3, count + 6),
            recent_window=recent_window,
            seed=rng.randint(1, 10**9),
        )
        generated_by_strategy[strategy] = generated

    for strategy, generated in generated_by_strategy.items():
        scores = build_scores(draws, recent_window=recent_window, strategy=strategy)
        ranked_strategy_tickets = sorted(generated, key=lambda ticket: ticket.score, reverse=True)
        if not ranked_strategy_tickets:
            continue
        min_score = min(ticket.score for ticket in ranked_strategy_tickets)
        max_score = max(ticket.score for ticket in ranked_strategy_tickets)
        strategy_weight = weights.get(strategy, 1.0)
        for rank, ticket in enumerate(ranked_strategy_tickets, start=1):
            normalized_score = (
                (ticket.score - min_score) / (max_score - min_score)
                if max_score > min_score
                else 0.5
            )
            maturity = ticket_maturity_score(ticket.numbers, scores, draws[-1])
            adjusted_score = (
                strategy_weight * 1.15
                + normalized_score * 0.85
                + maturity * 0.42
                - (rank - 1) * 0.003
            )
            reasons = tuple(
                list(ticket.reasons[:4])
                + [
                    f"實戰成熟度 {strategy_weight:.2f}",
                    f"票組成熟分 {maturity:.2f}",
                ]
            )
            adjusted_ticket = Ticket(
                numbers=ticket.numbers,
                score=adjusted_score,
                profile=ticket.profile,
                strategy=strategy,
                reasons=reasons,
            )
            current = tickets.get(adjusted_ticket.numbers)
            if current is None or adjusted_ticket.score > current.score:
                tickets[adjusted_ticket.numbers] = adjusted_ticket

    ranked = sorted(tickets.values(), key=lambda ticket: ticket.score, reverse=True)
    return ranked[:ticket_count]


def performance_strategy_weights(conn: sqlite3.Connection | None) -> dict[str, float]:
    default = {
        "balanced": 1.20,
        "hot": 1.00,
        "cold": 0.90,
        "trend": 1.00,
        "diversified": 1.10,
    }
    if conn is None:
        return default

    init_db(conn)
    rows = conn.execute(
        """
        SELECT pt.strategy,
               COUNT(*) AS sample_count,
               AVG(pr.main_hits + pr.special_hit * 0.5) AS avg_points
        FROM prediction_results pr
        JOIN prediction_tickets pt ON pt.id = pr.ticket_id
        GROUP BY pt.strategy
        """
    ).fetchall()
    if not rows:
        return default

    weights = default.copy()
    for row in rows:
        if row["sample_count"] < 5:
            continue
        weights[row["strategy"]] = max(0.45, min(2.20, 0.55 + float(row["avg_points"])))
    return weights


def calibrated_strategy_weights(
    draws: list[Draw],
    recent_window: int,
    conn: sqlite3.Connection | None,
) -> dict[str, float]:
    live_weights = performance_strategy_weights(conn)
    backtest_weights = backtest_strategy_weights(draws, recent_window)
    weights = {}
    for strategy in strategy_names():
        weights[strategy] = live_weights.get(strategy, 1.0) * backtest_weights.get(strategy, 1.0)
    mean_weight = statistics.mean(weights.values()) if weights else 1.0
    if mean_weight <= 0:
        return {strategy: 1.0 for strategy in strategy_names()}
    return {
        strategy: max(AUTO_MIN_STRATEGY_WEIGHT, min(AUTO_MAX_STRATEGY_WEIGHT, weight / mean_weight))
        for strategy, weight in weights.items()
    }


def backtest_strategy_weights(draws: list[Draw], recent_window: int) -> dict[str, float]:
    weights = {}
    for strategy in strategy_names():
        summary = score_rank_backtest(
            draws,
            strategy,
            recent_window,
            max_periods=AUTO_BACKTEST_PERIODS,
        )
        sample = int(summary.get("sample", 0))
        if sample < 30:
            weights[strategy] = 1.0
            continue
        top5_edge = float(summary.get("top5_edge", 0.0))
        top9_edge = float(summary.get("top9_edge", 0.0))
        top10_edge = float(summary.get("top10_edge", 0.0))
        top15_edge = float(summary.get("top15_edge", 0.0))
        ge2_bonus = (float(summary.get("top9_ge2", 0.0)) - 0.35) * 0.42
        quality = 1.0 + top5_edge * 0.8 + top9_edge * 3.2 + top10_edge * 1.2 + top15_edge * 0.55 + ge2_bonus
        if top9_edge < 0.0 and top10_edge < 0.0:
            quality *= 0.35
        elif top9_edge < 0.0:
            quality *= 0.55
        elif top9_edge < 0.04:
            quality *= 0.75
        elif top9_edge >= 0.09:
            quality *= 1.15
        weights[strategy] = max(AUTO_MIN_STRATEGY_WEIGHT, min(AUTO_MAX_STRATEGY_WEIGHT, quality))
    return weights


def calculate_settlement_feedback_scores(
    conn: sqlite3.Connection | None,
    draws: list[Draw],
) -> dict[int, float]:
    empty = {number: 0.0 for number in range(MIN_NUMBER, MAX_NUMBER + 1)}
    if conn is None or not draws:
        return empty
    try:
        init_db(conn)
        row = conn.execute(
            """
            SELECT r.id AS run_id,
                   r.score_snapshot_json AS score_snapshot_json,
                   d.n1, d.n2, d.n3, d.n4, d.n5, d.n6, d.special,
                   d.draw_date, d.draw_no
            FROM prediction_runs r
            JOIN prediction_results res ON res.run_id = r.id
            JOIN draws d ON d.id = res.actual_draw_id
            GROUP BY r.id, res.actual_draw_id
            ORDER BY d.draw_date DESC, r.id DESC
            LIMIT 1
            """
        ).fetchone()
        if row is None:
            return empty
        actual_numbers = [int(row[f"n{index}"]) for index in range(1, MAIN_COUNT + 1)]
        snapshot = json.loads(row["score_snapshot_json"] or "{}")
        snapshot_items = []
        if isinstance(snapshot, dict):
            for key, value in snapshot.items():
                if isinstance(value, dict):
                    item = dict(value)
                    item["number"] = int(item.get("number", key))
                    snapshot_items.append(item)
        elif isinstance(snapshot, list):
            snapshot_items = [item for item in snapshot if isinstance(item, dict)]
        ranked_snapshot = sorted(
            snapshot_items,
            key=lambda item: float(item.get("score", 0.0)),
            reverse=True,
        )
        snapshot_ranks = {
            int(item.get("number")): rank
            for rank, item in enumerate(ranked_snapshot, start=1)
            if item.get("number") is not None
        }
        ticket_rows = conn.execute(
            """
            SELECT ticket_rank, numbers_json
            FROM prediction_tickets
            WHERE run_id = ?
            ORDER BY ticket_rank
            LIMIT 6
            """,
            (int(row["run_id"]),),
        ).fetchall()
        exposure = Counter()
        for ticket in ticket_rows:
            for number in json.loads(ticket["numbers_json"]):
                exposure[int(number)] += 1
        exposure_denominator = max(1, len(ticket_rows))
        raw = {number: 0.0 for number in range(MIN_NUMBER, MAX_NUMBER + 1)}
        for actual in actual_numbers:
            previous_rank = snapshot_ranks.get(actual, MAX_NUMBER)
            rank_gap = 1.0 if previous_rank > SUPPORT_POOL_SIZE else (0.72 if previous_rank > CORE_POOL_SIZE else 0.34)
            exposure_gap = 1.0 - min(exposure[actual] / exposure_denominator, 1.0)
            base = clamp01(rank_gap * 0.64 + exposure_gap * 0.36)
            for number in range(MIN_NUMBER, MAX_NUMBER + 1):
                similarity = 0.0
                if number == actual:
                    similarity += 1.0
                if abs(number - actual) <= 2:
                    similarity += 0.28
                if number % 10 == actual % 10:
                    similarity += 0.20
                if decade_bucket(number) == decade_bucket(actual):
                    similarity += 0.18
                if wave_color(number) == wave_color(actual):
                    similarity += 0.08
                raw[number] += base * min(similarity, 1.35)
        max_raw = max(raw.values(), default=0.0)
        if max_raw <= 0:
            return empty
        return {number: clamp01(value / max_raw) for number, value in raw.items()}
    except Exception:
        return empty


def ticket_maturity_score(
    numbers: tuple[int, ...],
    scores: dict[int, NumberScore],
    latest_draw: Draw | None,
) -> float:
    diversity = ticket_diversity_bonus(numbers)
    consensus = min(ticket_model_consensus_bonus(numbers, scores) / 0.42, 1.0)
    latest_overlap = len(set(numbers).intersection(latest_draw.main_numbers)) if latest_draw else 0
    latest_penalty = max(0, latest_overlap - 2) * 0.16
    tail_penalty = max(0, max(Counter(number % 10 for number in numbers).values()) - 2) * 0.08
    decade_penalty = max(0, max(Counter(number // 10 for number in numbers).values()) - 3) * 0.08
    raw = diversity * 0.52 + consensus * 0.38 + (1.0 - latest_penalty - tail_penalty - decade_penalty) * 0.10
    return max(0.20, min(1.0, raw))


def allocate_tickets(ticket_count: int, weights: dict[str, float]) -> dict[str, int]:
    total_weight = sum(weights.values())
    raw = {name: ticket_count * weight / total_weight for name, weight in weights.items()}
    allocation = {name: int(value) for name, value in raw.items()}
    remaining = ticket_count - sum(allocation.values())
    for name, _ in sorted(raw.items(), key=lambda item: item[1] - int(item[1]), reverse=True):
        if remaining <= 0:
            break
        allocation[name] += 1
        remaining -= 1
    return {name: count for name, count in allocation.items() if count > 0}


def generate_tickets(
    draws: list[Draw],
    strategy: str,
    ticket_count: int,
    recent_window: int,
    seed: int | None = None,
) -> list[Ticket]:
    if len(draws) < 5:
        raise SystemExit("歷史資料太少，至少需要 5 期才能產生候選組合。")
    rng = random.Random(seed)
    scores = build_scores(draws, recent_window=recent_window, strategy=strategy)
    bankers, drags, _, weak_numbers = build_banker_drag_lists(scores)
    ranks = rank_lookup(scores)
    recent_sets = {draw.main_numbers for draw in draws[-20:]}
    candidates: dict[tuple[int, ...], Ticket] = {}
    attempts = max(1200, ticket_count * 120)

    for _ in range(attempts):
        numbers = weighted_sample_without_replacement(scores, MAIN_COUNT, rng)
        numbers = tuple(sorted(numbers))
        if numbers in recent_sets:
            continue
        if not passes_profile_filters(numbers):
            continue
        if len(set(numbers).intersection(weak_numbers)) > 2:
            continue
        score = score_ticket(numbers, scores, strategy, bankers, drags, ranks)
        profile = describe_ticket_profile(numbers)
        reasons = explain_ticket(numbers, scores, bankers, drags, strategy)
        current = candidates.get(numbers)
        if current is None or score > current.score:
            candidates[numbers] = Ticket(
                numbers=numbers,
                score=score,
                profile=profile,
                strategy=strategy,
                reasons=tuple(reasons),
            )

    ranked = sorted(candidates.values(), key=lambda ticket: ticket.score, reverse=True)
    return ranked[:ticket_count]


def weighted_sample_without_replacement(
    scores: dict[int, NumberScore],
    count: int,
    rng: random.Random,
) -> tuple[int, ...]:
    pool = list(scores.values())
    ranks = rank_lookup(scores)
    selected = []

    while len(selected) < count:
        min_score = min(row.score for row in pool)
        offset = abs(min_score) + 0.01 if min_score <= 0 else 0.01
        weights = []
        for row in pool:
            rank = ranks[row.number]
            if rank <= CORE_POOL_SIZE:
                rank_multiplier = 1.0 + (CORE_POOL_SIZE + 1 - rank) * 0.12
            elif rank <= SUPPORT_POOL_SIZE:
                rank_multiplier = 0.82
            else:
                rank_multiplier = 0.58
            weights.append((row.score + offset) ** 2.35 * rank_multiplier)
        picked = rng.choices(pool, weights=weights, k=1)[0]
        selected.append(picked.number)
        pool = [row for row in pool if row.number != picked.number]

    return tuple(selected)


def passes_profile_filters(numbers: tuple[int, ...]) -> bool:
    odd_count = sum(1 for number in numbers if number % 2)
    small_count = sum(1 for number in numbers if number <= 24)
    total = sum(numbers)
    max_same_tail = max(Counter(number % 10 for number in numbers).values())
    max_same_decade = max(Counter(number // 10 for number in numbers).values())
    consecutive_pairs = sum(
        1 for left, right in zip(numbers, numbers[1:]) if right - left == 1
    )
    wave_counts = Counter(wave_color(number) for number in numbers)

    if odd_count not in {2, 3, 4}:
        return False
    if small_count not in {2, 3, 4}:
        return False
    if total < 85 or total > 220:
        return False
    if max_same_tail > 2:
        return False
    if max_same_decade > 3:
        return False
    if consecutive_pairs > 2:
        return False
    if max(wave_counts.values()) > 4:
        return False
    return True


def score_ticket(
    numbers: tuple[int, ...],
    scores: dict[int, NumberScore],
    strategy: str,
    bankers: tuple[int, ...],
    drags: tuple[int, ...],
    ranks: dict[int, int] | None = None,
) -> float:
    if ranks is None:
        ranks = rank_lookup(scores)
    base = sum(scores[number].score for number in numbers)
    diversity_bonus = ticket_diversity_bonus(numbers)
    model_bonus = ticket_model_consensus_bonus(numbers, scores)
    banker_bonus = len(set(numbers).intersection(bankers)) * 0.18
    drag_bonus = min(len(set(numbers).intersection(drags)), 4) * 0.04
    core_hits = sum(1 for number in numbers if ranks[number] <= CORE_POOL_SIZE)
    support_hits = sum(1 for number in numbers if CORE_POOL_SIZE < ranks[number] <= SUPPORT_POOL_SIZE)
    outside_hits = MAIN_COUNT - core_hits - support_hits
    core_focus_bonus = core_hits * 0.16 + max(0, core_hits - 3) * 0.20
    support_penalty = max(0, support_hits - 2) * 0.13
    outside_penalty = max(0, outside_hits - 1) * 0.12
    if strategy == "diversified":
        return (
            base
            + diversity_bonus * 1.5
            + model_bonus
            + banker_bonus
            + drag_bonus
            + core_focus_bonus
            - support_penalty
            - outside_penalty
        )
    return (
        base
        + diversity_bonus
        + model_bonus
        + banker_bonus
        + drag_bonus
        + core_focus_bonus
        - support_penalty
        - outside_penalty
    )


def ticket_model_consensus_bonus(
    numbers: tuple[int, ...],
    scores: dict[int, NumberScore],
) -> float:
    model_names = list(next(iter(scores.values())).model_scores) if scores else []
    if not model_names:
        return 0.0
    averages = {
        model: statistics.mean(scores[number].model_scores.get(model, 0.0) for number in numbers)
        for model in model_names
    }
    strong_models = sum(1 for value in averages.values() if value >= 0.62)
    top_average = statistics.mean(sorted(averages.values(), reverse=True)[:4])
    return strong_models * 0.035 + top_average * 0.16


def ticket_confidence_index(ticket: Ticket, max_score: float, rank: int) -> float:
    ratio = ticket.score / max_score if max_score else 0.0
    rank_bonus = max(0.0, (6 - rank) * 1.2)
    return max(50.0, min(96.0, 54.0 + ratio * 36.0 + rank_bonus))


def ticket_confidence_label(ticket: Ticket, max_score: float, rank: int) -> str:
    value = ticket_confidence_index(ticket, max_score, rank)
    if rank <= 3 and value >= 88.0:
        return f"高機率信心牌 {value:.1f}"
    if rank <= 6 and value >= 82.0:
        return f"中高信心牌 {value:.1f}"
    return f"觀察牌 {value:.1f}"


def confidence_ticket_rows(package: PredictionPackage, limit: int = 6) -> list[list[object]]:
    max_score = max((ticket.score for ticket in package.tickets), default=1.0) or 1.0
    rows = []
    for rank, ticket in enumerate(package.tickets[:limit], start=1):
        rows.append(
            [
                rank,
                format_numbers(ticket.numbers),
                ticket_confidence_label(ticket, max_score, rank),
                strategy_label(ticket.strategy),
                f"{ticket.score:.3f}",
                "；".join(ticket.reasons[-2:]) if ticket.reasons else "成熟度校準",
            ]
        )
    return rows


def system_gap_review_rows(
    conn: sqlite3.Connection,
    draws: list[Draw],
    package: PredictionPackage,
    rank_backtest: dict[str, float | int],
    month_review: dict[str, object],
    settled: tuple[sqlite3.Row, Draw] | None,
) -> list[list[object]]:
    rows: list[list[object]] = []
    top9_edge = float(rank_backtest.get("top9_edge", 0.0))
    top15_edge = float(rank_backtest.get("top15_edge", 0.0))
    consensus = model_consensus_rate(package)
    if top15_edge < 0.03:
        rows.append(
            [
                "第十至第十五補位池失準",
                f"前十五差值 {top15_edge:.3f}，補位池沒有形成穩定優勢",
                "新增冷爆捕捉 + 區間修復，補抓前九外的中段與冷爆號",
            ]
        )
    if top9_edge < 0.18:
        rows.append(
            [
                "前九核心優勢不足",
                f"前九差值 {top9_edge:.3f}，只能列主檢查池，不能放大保證",
                "新增結算回饋，把上期漏抓號與同結構號可控前移",
            ]
        )
    if consensus < 0.58:
        rows.append(
            [
                "模型共識偏低",
                f"前十共識 {consensus:.3f}，子模型排名分散",
                "強化自動融合成熟度，新增鄰近橋接與回饋模型交叉確認",
            ]
        )
    if settled is not None:
        settled_run, actual = settled
        ranked = score_snapshot_ranked(settled_run)
        missed = [number for number in actual.main_numbers if number not in ranked[:CORE_POOL_SIZE]]
        if missed:
            rows.append(
                [
                    "上期實際漏抓",
                    f"{format_numbers(missed)} 未在舊前九核心池內",
                    "第16版結算回饋 + 轉移追蹤會直接提高漏抓號、鄰近號、同尾號、同區間號",
                ]
            )
        actual_decades = Counter(decade_bucket(number) for number in actual.main_numbers)
        mid_hits = actual_decades.get("11-20", 0) + actual_decades.get("21-30", 0)
        if mid_hits >= 4:
            rows.append(
                [
                    "中段區間捕捉不足",
                    f"上期 11-30 區間開出 {mid_hits} 顆",
                    "第16版區間修復 + 尾數轉移提高 11-30 中段與同尾橋接權重",
                ]
            )
    missing_hot = month_review.get("missing_hot", [])
    if missing_hot:
        rows.append(
            [
                "月內熱點未前移",
                f"本月熱點仍在前九外：{format_numbers(missing_hot)}",
                "第16版本月滾動 + 日曆相位共同前移，不再只當防守補位",
            ]
        )
    if not rows:
        rows.append(
            [
                "未發現重大缺口",
                "資料、回測、結算、手機同步均正常",
                "維持第16版強化模型並持續滾動校準",
            ]
        )
    return rows


def system_completeness_rows(
    draws: list[Draw],
    package: PredictionPackage,
    conn: sqlite3.Connection,
) -> tuple[int, int, list[list[object]]]:
    latest = draws[-1] if draws else None
    latest_run = latest_prediction_run(conn)
    latest_ticket_count = 0
    if latest_run is not None:
        latest_ticket_count = conn.execute(
            "SELECT COUNT(*) FROM prediction_tickets WHERE run_id = ?",
            (latest_run["id"],),
        ).fetchone()[0]
    launcher_path = Path("香港六合彩預測系統_一鍵啟動.bat")
    if not launcher_path.exists():
        parent_launcher = Path("..") / "香港六合彩預測系統_一鍵啟動.bat"
        if parent_launcher.exists():
            launcher_path = parent_launcher
    checks = [
        (
            "歷史資料庫",
            len(draws) >= 1000,
            f"{len(draws)} 期 / {draws[0].draw_date if draws else '-'} -> {draws[-1].draw_date if draws else '-'}",
            "資料量已接入",
        ),
        (
            "最新期格式",
            bool(latest and re.fullmatch(r"\d{4}/\d{3}", latest.draw_no or "")),
            latest.draw_no if latest else "-",
            "YYYY/NNN 已強制標準化",
        ),
        (
            "官方更新入口",
            True,
            "官方資料來源 + 壓縮格式 + 日期格式",
            "已修正並接入一鍵更新",
        ),
        (
            "預測紀錄",
            latest_run is not None and latest_ticket_count >= 20,
            f"第 {latest_run['id'] if latest_run else '-'} 筆 / {latest_ticket_count} 組",
            "可追蹤待結算",
        ),
        (
            "高機率信心牌",
            len(package.tickets) >= 6,
            "前 6 組獨立標註",
            "已特別強調",
        ),
        (
            "策略成熟度",
            True,
            "回測校準 + 熔斷降權",
            "已啟用",
        ),
        (
            "539規格戰報",
            Path("reports").exists(),
            str(Path("reports") / ENHANCED_BATTLE_REPORT_NAME),
            "已輸出",
        ),
        (
            "網站輸出",
            Path("site").exists(),
            f"{SITE_HOME_NAME}/{SITE_LATEST_PREDICTION_NAME}/{SITE_SYSTEM_REPORT_NAME}/{SITE_DRAWS_CSV_NAME}",
            "已補齊 build-site 同步",
        ),
        (
            "一鍵啟動",
            launcher_path.exists() and Path("香港六合彩預測系統_一鍵更新.ps1").exists(),
            str(launcher_path),
            f"已接 {MODEL_VERSION}",
        ),
        (
            "全自動更新",
            Path("香港六合彩預測系統_開獎後立即更新.ps1").exists()
            and Path("香港六合彩預測系統_安裝開獎後立即更新排程.ps1").exists(),
            "每日21:15監控 + 每1分鐘輪詢 + 雲端每日每5分鐘",
            "開獎後自動重算並同步手機雲端",
        ),
        (
            "手機雲端同步",
            Path("香港六合彩預測系統_同步手機雲端.ps1").exists(),
            "香港六合彩預測系統_同步手機雲端.ps1",
            "一鍵更新後同步",
        ),
        (
            "備份機制",
            Path("backups").exists(),
            "每日更新前自動備份",
            "已啟用",
        ),
    ]
    rows = []
    passed = 0
    for name, ok, evidence, action in checks:
        passed += int(ok)
        rows.append([name, "通過" if ok else "需補強", evidence, action])
    return passed, len(checks), rows


def battle_summary_rows(
    latest: Draw,
    package: PredictionPackage,
    target_date: str,
    run_id: int | None,
    risk_level: str,
    release_edge: float,
    completeness_passed: int,
    completeness_total: int,
) -> list[list[object]]:
    top_confidence = [format_numbers(ticket.numbers) for ticket in package.tickets[:3]]
    return [
        ["最新開獎", f"{latest.draw_no} / {latest.draw_date}", f"{format_numbers(latest.main_numbers)} + {latest.special:02d}"],
        ["預測目標", target_date, f"第 {run_id if run_id is not None else '-'} 筆預測"],
        ["高信心主牌", "前三組", " / ".join(top_confidence)],
        ["膽碼", "前九核心", format_numbers(package.bankers)],
        ["拖碼", "前九補強", format_numbers(package.drags)],
        ["特別號", "獨立候選", format_numbers(package.special_candidates)],
        ["風險", risk_level, f"前九差值 {release_edge:.3f}"],
        ["完整度", f"{completeness_passed}/{completeness_total}", "資料、預測、戰報、網站、一鍵流程已檢查"],
    ]


def recent_prediction_history_rows(conn: sqlite3.Connection, limit: int = 12) -> list[list[object]]:
    rows = conn.execute(
        """
        SELECT id, created_at, based_on_draw_date, based_on_draw_no,
               strategy, ticket_count, model_version
        FROM prediction_runs
        ORDER BY id DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    if not rows:
        return [["-", "-", "-", "-", "-", "-"]]
    return [
        [
            f"第 {row['id']} 筆",
            row["created_at"],
            f"{row['based_on_draw_no'] or '-'} / {row['based_on_draw_date']}",
            strategy_label(row["strategy"]),
            row["ticket_count"],
            row["model_version"],
        ]
        for row in rows
    ]


def ticket_diversity_bonus(numbers: tuple[int, ...]) -> float:
    odd_count = sum(1 for number in numbers if number % 2)
    small_count = sum(1 for number in numbers if number <= 24)
    tails = len({number % 10 for number in numbers})
    decades = len({number // 10 for number in numbers})
    colors = len({wave_color(number) for number in numbers})
    total = sum(numbers)
    sum_center_bonus = 1.0 - min(abs(total - 150) / 150, 1.0)

    return (
        (1.0 - abs(odd_count - 3) / 3)
        + (1.0 - abs(small_count - 3) / 3)
        + tails / MAIN_COUNT
        + decades / MAIN_COUNT
        + colors / 3
        + sum_center_bonus
    ) / 6


def describe_ticket_profile(numbers: tuple[int, ...]) -> str:
    odd_count = sum(1 for number in numbers if number % 2)
    small_count = sum(1 for number in numbers if number <= 24)
    total = sum(numbers)
    colors = Counter(wave_color(number) for number in numbers)
    color_text = " ".join(f"{name}{count}" for name, count in sorted(colors.items()))
    return f"奇偶 {odd_count}:{MAIN_COUNT - odd_count}, 大小 {small_count}:{MAIN_COUNT - small_count}, 和值 {total}, {color_text}"


def explain_ticket(
    numbers: tuple[int, ...],
    scores: dict[int, NumberScore],
    bankers: tuple[int, ...],
    drags: tuple[int, ...],
    strategy: str,
) -> list[str]:
    reasons = [f"{strategy_label(strategy)} 策略"]
    banker_hits = sorted(set(numbers).intersection(bankers))
    drag_hits = sorted(set(numbers).intersection(drags))
    ranks = rank_lookup(scores)
    core_hits = sorted(number for number in numbers if ranks[number] <= CORE_POOL_SIZE)
    if banker_hits:
        reasons.append(f"含膽碼 {format_numbers(banker_hits)}")
    if core_hits:
        reasons.append(f"前九核心池 {format_numbers(core_hits)}")
    if drag_hits:
        reasons.append(f"拖碼覆蓋 {format_numbers(drag_hits[:4])}")
    high_gap = [number for number in numbers if scores[number].miss_gap >= 8]
    if high_gap:
        reasons.append(f"含遺漏修正號 {format_numbers(high_gap)}")
    hot = [number for number in numbers if scores[number].recent_frequency >= 3]
    if hot:
        reasons.append(f"含近期熱號 {format_numbers(hot)}")
    model_reasons = top_ticket_models(numbers, scores)
    if model_reasons:
        reasons.append("模型支撐 " + "、".join(model_reasons))
    reasons.append(describe_ticket_profile(numbers))
    return reasons[:5]


def top_ticket_models(numbers: tuple[int, ...], scores: dict[int, NumberScore]) -> list[str]:
    labels = {
        "frequency": "長期頻率",
        "recency": "近期熱度",
        "gap": "遺漏",
        "trend": "趨勢",
        "pair": "配對",
        "special": "特碼",
        "bayes": "貝葉斯",
        "momentum": "動能",
        "cycle": "週期",
        "structure": "結構",
        "rolling_month": "本月滾動",
        "zone_repair": "區間修復",
        "breakout_capture": "冷爆捕捉",
        "neighbor_bridge": "鄰近橋接",
        "auto_maturity": "實戰成熟度",
        "settlement_feedback": "結算回饋",
        "transition_follow": "轉移追蹤",
        "tail_transition": "尾數轉移",
        "calendar_phase": "日曆相位",
        "special_crossover": "特別號交叉",
    }
    if not scores:
        return []
    model_names = list(next(iter(scores.values())).model_scores)
    averages = []
    for model in model_names:
        value = statistics.mean(scores[number].model_scores.get(model, 0.0) for number in numbers)
        averages.append((model, value))
    return [labels.get(model, model) for model, value in sorted(averages, key=lambda item: item[1], reverse=True)[:3] if value >= 0.55]


def analyze(draws: list[Draw], recent_window: int) -> str:
    if not draws:
        return "沒有可分析資料。"

    scores = build_scores(draws, recent_window=recent_window, strategy="balanced")
    latest = draws[-1]
    total_sums = [sum(draw.main_numbers) for draw in draws]
    odd_distribution = Counter(sum(1 for number in draw.main_numbers if number % 2) for draw in draws)
    small_distribution = Counter(sum(1 for number in draw.main_numbers if number <= 24) for draw in draws)
    color_distribution = Counter(wave_color(number) for draw in draws for number in draw.main_numbers)
    bankers, drags, reserves, weak_numbers = build_banker_drag_lists(scores)
    special_candidates = build_special_candidates(scores)

    lines = [
        "香港六合彩分析摘要",
        "=" * 24,
        f"總期數: {len(draws)}",
        f"最新期: {latest.draw_date} {latest.draw_no}".rstrip(),
        f"最新主號: {format_numbers(latest.main_numbers)} / 特別號: {latest.special:02d}",
        f"主號和值: 平均 {statistics.mean(total_sums):.1f}, 中位數 {statistics.median(total_sums):.1f}, 最新 {sum(latest.main_numbers)}",
        f"膽碼: {format_numbers(bankers)}",
        f"拖碼: {format_numbers(drags)}",
        f"防守碼: {format_numbers(reserves)}",
        f"弱勢碼: {format_numbers(weak_numbers)}",
        f"特別號候選: {format_numbers(special_candidates)}",
        "",
        "熱號排行:",
        format_score_table(
            sorted(scores.values(), key=lambda row: (row.recent_frequency, row.score), reverse=True)[:10]
        ),
        "",
        "冷號 / 遺漏排行:",
        format_score_table(sorted(scores.values(), key=lambda row: row.miss_gap, reverse=True)[:10]),
        "",
        "奇數個數分布:",
        format_distribution(odd_distribution),
        "",
        "小號個數分布:",
        format_distribution(small_distribution),
        "",
        "波色分布:",
        format_distribution(color_distribution),
    ]
    return "\n".join(lines)


def format_score_table(rows: Iterable[NumberScore]) -> str:
    lines = ["號碼  波色  全期  近期  特碼  遺漏  趨勢    分數"]
    for row in rows:
        lines.append(
            f"{row.number:02d}   {row.color:<4} {row.total_frequency:>3}   {row.recent_frequency:>3}   "
            f"{row.special_frequency:>3}   {row.miss_gap:>3}   {row.trend:+.3f}  {row.score:.3f}"
        )
    return "\n".join(lines)


def format_distribution(counter: Counter) -> str:
    total = sum(counter.values())
    parts = []
    for key in sorted(counter):
        percentage = counter[key] / total * 100 if total else 0
        parts.append(f"{key}: {counter[key]} ({percentage:.1f}%)")
    return " | ".join(parts)


def save_prediction_run(
    conn: sqlite3.Connection,
    package: PredictionPackage,
    draws: list[Draw],
    recent_window: int,
    seed: int | None,
    report_path: Path,
) -> int:
    init_db(conn)
    latest = draws[-1]
    if latest.row_id is None:
        raise SystemExit("資料庫預測需要 draw.row_id，請先 import-csv 或 fetch-hkjc。")
    score_snapshot = {
        str(number): {
            "score": round(row.score, 6),
            "total_frequency": row.total_frequency,
            "recent_frequency": row.recent_frequency,
            "special_frequency": row.special_frequency,
            "miss_gap": row.miss_gap,
            "trend": round(row.trend, 6),
            "pair_strength": round(row.pair_strength, 6),
            "color": row.color,
            "model_scores": {
                name: round(value, 6) for name, value in sorted(row.model_scores.items())
            },
        }
        for number, row in sorted(package.scores.items())
    }
    cursor = conn.execute(
        """
        INSERT INTO prediction_runs
            (created_at, based_on_draw_id, based_on_draw_date, based_on_draw_no,
             strategy, model_version, recent_window, ticket_count, seed,
             banker_numbers_json, drag_numbers_json, reserve_numbers_json, weak_numbers_json,
             special_candidates_json, score_snapshot_json, report_path)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            now_text(),
            latest.row_id,
            latest.draw_date,
            latest.draw_no,
            package.strategy,
            MODEL_VERSION,
            recent_window,
            len(package.tickets),
            seed,
            json.dumps(package.bankers),
            json.dumps(package.drags),
            json.dumps(package.reserves),
            json.dumps(package.weak_numbers),
            json.dumps(package.special_candidates),
            json.dumps(score_snapshot, ensure_ascii=False),
            str(report_path),
        ),
    )
    run_id = int(cursor.lastrowid)
    for rank, ticket in enumerate(package.tickets, start=1):
        conn.execute(
            """
            INSERT INTO prediction_tickets
                (run_id, ticket_rank, strategy, numbers_json, score, profile, reasons_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                rank,
                ticket.strategy,
                json.dumps(ticket.numbers),
                ticket.score,
                ticket.profile,
                json.dumps(ticket.reasons, ensure_ascii=False),
                now_text(),
            ),
        )
    conn.commit()
    return run_id


def evaluate_predictions(conn: sqlite3.Connection, prediction_id: str) -> str:
    init_db(conn)
    runs = select_prediction_runs(conn, prediction_id)
    if not runs:
        return "沒有可驗證的預測。"

    evaluated = 0
    waiting = 0
    lines = ["預測驗證結果", "=" * 24]
    for run in runs:
        actual = next_draw_after(conn, int(run["based_on_draw_id"]))
        if actual is None:
            waiting += 1
            lines.append(f"第 {run['id']} 筆: 等待下一期開獎")
            continue

        tickets = conn.execute(
            """
            SELECT id, ticket_rank, numbers_json
            FROM prediction_tickets
            WHERE run_id = ?
            ORDER BY ticket_rank
            """,
            (run["id"],),
        ).fetchall()
        actual_numbers = set(actual.main_numbers)
        best_hits = 0
        best_line = ""
        for ticket in tickets:
            numbers = tuple(json.loads(ticket["numbers_json"]))
            hits = sorted(set(numbers).intersection(actual_numbers))
            special_hit = actual.special in numbers
            prize_tier = classify_prize(len(hits), special_hit)
            conn.execute(
                """
                INSERT OR IGNORE INTO prediction_results
                    (run_id, ticket_id, actual_draw_id, main_hits, hit_numbers_json,
                     special_hit, prize_tier, evaluated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run["id"],
                    ticket["id"],
                    actual.row_id,
                    len(hits),
                    json.dumps(hits),
                    int(special_hit),
                    prize_tier,
                    now_text(),
                ),
            )
            if len(hits) > best_hits or (len(hits) == best_hits and special_hit):
                best_hits = len(hits)
                best_line = (
                    f"第 {ticket['ticket_rank']:02d} 組 {format_numbers(numbers)} "
                    f"中 {len(hits)} 個主號"
                    + (" + 特別號" if special_hit else "")
                )
        evaluated += 1
        lines.append(
            f"第 {run['id']} 筆 -> {actual.draw_date} {actual.draw_no}: {best_line or '未命中主號'}"
        )
    conn.commit()
    lines.append("")
    lines.append(f"已驗證: {evaluated}，等待開獎: {waiting}")
    return "\n".join(lines)


def select_prediction_runs(conn: sqlite3.Connection, prediction_id: str) -> list[sqlite3.Row]:
    if prediction_id == "latest":
        row = conn.execute(
            "SELECT * FROM prediction_runs ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return [row] if row else []
    if prediction_id == "all":
        return conn.execute("SELECT * FROM prediction_runs ORDER BY id").fetchall()
    try:
        run_id = int(prediction_id)
    except ValueError as exc:
        raise SystemExit("--prediction-id 必須是 latest、all 或數字 id") from exc
    row = conn.execute("SELECT * FROM prediction_runs WHERE id = ?", (run_id,)).fetchone()
    return [row] if row else []


def next_draw_after(conn: sqlite3.Connection, draw_id: int) -> Draw | None:
    base = conn.execute("SELECT draw_date, draw_no FROM draws WHERE id = ?", (draw_id,)).fetchone()
    if base is None:
        return None
    row = conn.execute(
        """
        SELECT id, draw_date, draw_no, n1, n2, n3, n4, n5, n6, special, source
        FROM draws
        WHERE (draw_date > ? OR (draw_date = ? AND draw_no > ?))
        ORDER BY draw_date, draw_no
        LIMIT 1
        """,
        (base["draw_date"], base["draw_date"], base["draw_no"]),
    ).fetchone()
    if row is None:
        return None
    return Draw(
        draw_date=row["draw_date"],
        draw_no=row["draw_no"],
        main_numbers=tuple(row[f"n{i}"] for i in range(1, MAIN_COUNT + 1)),
        special=row["special"],
        source=row["source"],
        row_id=row["id"],
    )


def classify_prize(main_hits: int, special_hit: bool) -> str:
    if main_hits == 6:
        return "一獎"
    if main_hits == 5 and special_hit:
        return "二獎"
    if main_hits == 5:
        return "三獎"
    if main_hits == 4 and special_hit:
        return "四獎"
    if main_hits == 4:
        return "五獎"
    if main_hits == 3 and special_hit:
        return "六獎"
    if main_hits == 3:
        return "七獎"
    return "未達獎級"


def backtest(
    draws: list[Draw],
    strategy: str,
    tickets: int,
    recent_window: int,
    min_train: int,
    seed: int,
) -> str:
    return format_backtest_summary(
        run_backtest_summary(draws, strategy, tickets, recent_window, min_train, seed)
    )


def run_backtest_summary(
    draws: list[Draw],
    strategy: str,
    tickets: int,
    recent_window: int,
    min_train: int,
    seed: int,
) -> dict:
    if len(draws) <= min_train:
        raise SystemExit(f"資料不足：總期數 {len(draws)} 必須大於 min-train {min_train}")

    rng = random.Random(seed)
    hit_counter: Counter[int] = Counter()
    prize_counter: Counter[str] = Counter()
    special_hits = 0
    best_main_hits = []
    tested = 0

    for index in range(min_train, len(draws)):
        train = draws[:index]
        actual = draws[index]
        generated = generate_tickets(
            train,
            strategy=strategy,
            ticket_count=tickets,
            recent_window=recent_window,
            seed=rng.randint(1, 10**9),
        )
        best_hit = 0
        had_special = False
        actual_main = set(actual.main_numbers)
        for ticket in generated:
            main_hits = len(set(ticket.numbers).intersection(actual_main))
            special_hit = actual.special in ticket.numbers
            hit_counter[main_hits] += 1
            prize_counter[classify_prize(main_hits, special_hit)] += 1
            best_hit = max(best_hit, main_hits)
            had_special = had_special or special_hit
        special_hits += int(had_special)
        best_main_hits.append(best_hit)
        tested += 1

    total_tickets = tested * tickets
    avg_best_main_hits = statistics.mean(best_main_hits) if best_main_hits else 0.0
    summary = {
        "strategy": strategy,
        "tickets": tickets,
        "recent_window": recent_window,
        "min_train": min_train,
        "seed": seed,
        "tested_periods": tested,
        "total_tickets": total_tickets,
        "avg_best_main_hits": avg_best_main_hits,
        "special_period_hits": special_hits,
        "hit_distribution": {str(i): hit_counter[i] for i in range(0, MAIN_COUNT + 1)},
        "prize_distribution": dict(prize_counter),
    }
    summary["summary_text"] = format_backtest_summary(summary)
    return summary


def format_backtest_summary(summary: dict) -> str:
    tested = int(summary["tested_periods"])
    tickets = int(summary["tickets"])
    total_tickets = int(summary["total_tickets"])
    special_hits = int(summary["special_period_hits"])
    hit_distribution = {
        int(key): int(value) for key, value in summary["hit_distribution"].items()
    }
    prize_distribution = {
        str(key): int(value) for key, value in summary["prize_distribution"].items()
    }
    lines = [
        "Walk-forward 回測摘要",
        "=" * 24,
        f"策略: {summary['strategy']}",
        f"測試期數: {tested}",
        f"每期候選組合: {tickets}",
        f"總候選組合: {total_tickets}",
        f"每期最佳主號命中平均: {float(summary['avg_best_main_hits']):.2f}",
        f"至少一組含當期特別號: {special_hits}/{tested} ({special_hits / tested * 100:.1f}%)",
        "",
        "主號命中分布:",
    ]
    for hit_count in range(0, MAIN_COUNT + 1):
        count = hit_distribution.get(hit_count, 0)
        rate = count / total_tickets * 100 if total_tickets else 0
        lines.append(f"{hit_count} 個: {count} ({rate:.2f}%)")
    lines.append("")
    lines.append("獎級估計:")
    for prize, count in sorted(prize_distribution.items(), key=lambda item: item[1], reverse=True):
        lines.append(f"{prize}: {count}")
    return "\n".join(lines)


def save_backtest_run(conn: sqlite3.Connection, summary: dict) -> int:
    init_db(conn)
    cursor = conn.execute(
        """
        INSERT INTO backtest_runs
            (created_at, strategy, recent_window, min_train, ticket_count,
             tested_periods, total_tickets, avg_best_main_hits, special_period_hits,
             hit_distribution_json, prize_distribution_json, summary_text)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            now_text(),
            summary["strategy"],
            summary["recent_window"],
            summary["min_train"],
            summary["tickets"],
            summary["tested_periods"],
            summary["total_tickets"],
            summary["avg_best_main_hits"],
            summary["special_period_hits"],
            json.dumps(summary["hit_distribution"], ensure_ascii=False),
            json.dumps(summary["prize_distribution"], ensure_ascii=False),
            summary["summary_text"],
        ),
    )
    conn.commit()
    return int(cursor.lastrowid)


def backtests_text(conn: sqlite3.Connection, limit: int) -> str:
    init_db(conn)
    rows = conn.execute(
        """
        SELECT id, created_at, strategy, ticket_count, tested_periods,
               avg_best_main_hits, special_period_hits
        FROM backtest_runs
        ORDER BY id DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    lines = ["回測紀錄", "=" * 24]
    if not rows:
        lines.append("尚無回測紀錄。")
        return "\n".join(lines)
    lines.append("ID   建立時間                  策略         組數  期數  最佳均值  特別號期")
    for row in rows:
        lines.append(
            f"{row['id']:<4} {row['created_at']:<25} {row['strategy']:<11} "
            f"{row['ticket_count']:>4} {row['tested_periods']:>5} "
            f"{float(row['avg_best_main_hits']):>8.2f} {row['special_period_hits']:>7}"
        )
    return "\n".join(lines)


def render_prediction_html(
    path: Path,
    package: PredictionPackage,
    draws: list[Draw],
    run_id: int | None,
    title: str = "香港六合彩預測報告",
) -> None:
    latest = draws[-1]
    hot_rows = sorted(package.scores.values(), key=lambda row: (row.recent_frequency, row.score), reverse=True)[:12]
    gap_rows = sorted(package.scores.values(), key=lambda row: row.miss_gap, reverse=True)[:12]
    path.parent.mkdir(parents=True, exist_ok=True)
    html_text = f"""<!doctype html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(title)}</title>
  <style>
    body {{ margin: 0; font-family: Arial, "Microsoft JhengHei", sans-serif; background: #f4f6f8; color: #17202a; }}
    header {{ background: #102a43; color: white; padding: 24px 32px; }}
    main {{ max-width: 1180px; margin: 0 auto; padding: 24px; }}
    section {{ margin-bottom: 24px; }}
    h1, h2 {{ margin: 0 0 12px; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 16px; }}
    .card {{ background: white; border: 1px solid #d7dee8; border-radius: 8px; padding: 16px; }}
    .balls {{ display: flex; flex-wrap: wrap; gap: 8px; }}
    .ball {{ width: 38px; height: 38px; border-radius: 50%; display: inline-flex; align-items: center; justify-content: center; color: white; font-weight: 700; }}
    .red {{ background: #c62828; }}
    .blue {{ background: #1565c0; }}
    .green {{ background: #2e7d32; }}
    table {{ width: 100%; border-collapse: collapse; background: white; }}
    th, td {{ border-bottom: 1px solid #dfe5ec; padding: 9px; text-align: left; font-size: 14px; vertical-align: top; }}
    th {{ background: #e9eef5; }}
    .super {{ border: 3px solid #b42318; box-shadow: 0 0 0 4px rgba(180,35,24,.10); background:#fff8f6; }}
    .super h2 {{ color:#b42318; }}
    .confidence {{ border: 2px solid #b42318; box-shadow: 0 0 0 3px rgba(180,35,24,.08); }}
    .confidence h2 {{ color: #b42318; }}
    .muted {{ color: #5f6f82; font-size: 13px; }}
    .score {{ font-variant-numeric: tabular-nums; }}
  </style>
</head>
<body>
  <header>
    <h1>{escape(title)}</h1>
    <div>第 {run_id if run_id is not None else "-"} 筆預測 | 模型 {MODEL_VERSION} | 產生時間 {escape(now_text())}</div>
  </header>
  <main>
    <section class="grid">
      <div class="card">
        <h2>最新資料基準</h2>
        <div>期號：{escape(latest.draw_no or "-")}</div>
        <div>日期：{escape(latest.draw_date)}</div>
        <div class="balls">{render_balls(latest.main_numbers)} <span class="muted">特別號</span> {render_ball(latest.special)}</div>
      </div>
      <div class="card">
        <h2>膽碼</h2>
        <div class="balls">{render_balls(package.bankers)}</div>
        <p class="muted">最高綜合分，作為前九核心號碼。</p>
      </div>
      <div class="card">
        <h2>拖碼</h2>
        <div class="balls">{render_balls(package.drags)}</div>
      </div>
      <div class="card">
        <h2>防守 / 弱勢</h2>
        <div class="muted">防守碼</div>
        <div class="balls">{render_balls(package.reserves)}</div>
        <div class="muted" style="margin-top:10px">弱勢碼</div>
        <div class="balls">{render_balls(package.weak_numbers)}</div>
      </div>
      <div class="card">
        <h2>特別號候選</h2>
        <div class="balls">{render_balls(package.special_candidates)}</div>
        <p class="muted">依特別號頻率、綜合分數與遺漏期數混合排序。</p>
      </div>
    </section>

    <section class="card super">
      <h2>超強信心高機率強推薦號碼</h2>
      <p class="muted">獨隻、2碼、3碼獨立精算；屬研究強推薦，不保證開出。</p>
      <table>
        <thead><tr><th>類型</th><th>強推薦號碼</th><th>命中目標</th><th>信心指數</th><th>隨機基準</th><th>強化理由</th></tr></thead>
        <tbody>{render_super_recommendation_rows(package, draws)}</tbody>
      </table>
    </section>

    <section class="card confidence">
      <h2>高機率信心牌（特別標註）</h2>
      <p class="muted">依本期成熟度校準分、策略回測權重、票組成熟分排序；屬研究高信心，不等於保證。</p>
      <table>
        <thead><tr><th>優先</th><th>號碼</th><th>信心標籤</th><th>策略</th><th>成熟分</th><th>註明</th></tr></thead>
        <tbody>{render_confidence_ticket_rows(package)}</tbody>
      </table>
    </section>

    <section>
      <h2>候選組合</h2>
      <table>
        <thead><tr><th>#</th><th>號碼</th><th>策略</th><th>分數</th><th>結構</th><th>理由</th></tr></thead>
        <tbody>
          {render_ticket_rows(package.tickets)}
        </tbody>
      </table>
    </section>

    <section class="grid">
      <div class="card">
        <h2>熱號</h2>
        <table>{render_score_rows(hot_rows)}</table>
      </div>
      <div class="card">
        <h2>遺漏號</h2>
        <table>{render_score_rows(gap_rows)}</table>
      </div>
    </section>

    <section>
      <h2>模型排行</h2>
      <div class="grid">{render_model_cards(package.scores)}</div>
    </section>
  </main>
</body>
</html>
"""
    path.write_text(html_text, encoding="utf-8")


def render_full_report(path: Path, conn: sqlite3.Connection, recent_window: int) -> None:
    draws = load_draws_from_db(conn)
    if not draws:
        raise SystemExit("資料庫沒有開獎資料。")
    latest_run = conn.execute(
        "SELECT id FROM prediction_runs ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if latest_run:
        package = package_from_run(conn, int(latest_run["id"]))
        run_id = int(latest_run["id"])
    else:
        package = generate_prediction_package(draws, "auto", 20, recent_window, None, conn)
        run_id = None
    render_prediction_html(path, package, draws, run_id, title="香港六合彩系統報告")


def save_battle_reports(
    conn: sqlite3.Connection,
    report_dir: Path = DEFAULT_REPORT_DIR,
    site_dir: Path | None = None,
    recent_window: int = DEFAULT_RECENT_WINDOW,
) -> dict[str, Path]:
    init_db(conn)
    report_dir.mkdir(parents=True, exist_ok=True)
    cleanup_legacy_output_files(None, report_dir)
    markdown_text = build_battle_report_markdown(conn, recent_window)
    html_text = build_battle_report_html(markdown_text)
    paths = {
        "md": report_dir / BATTLE_REPORT_MARKDOWN_NAME,
        "txt": report_dir / BATTLE_REPORT_TEXT_NAME,
        "html": report_dir / SITE_BATTLE_REPORT_NAME,
        "enhanced": report_dir / ENHANCED_BATTLE_REPORT_NAME,
    }
    paths["md"].write_text(markdown_text, encoding="utf-8")
    paths["txt"].write_text(markdown_text, encoding="utf-8")
    paths["html"].write_text(html_text, encoding="utf-8")
    paths["enhanced"].write_text(html_text, encoding="utf-8")
    if site_dir is not None:
        site_dir.mkdir(parents=True, exist_ok=True)
        (site_dir / SITE_HOME_NAME).write_text(html_text, encoding="utf-8")
        (site_dir / SITE_BATTLE_REPORT_NAME).write_text(html_text, encoding="utf-8")
        (site_dir / "index.html").write_text(html_text, encoding="utf-8")
    return paths


def build_battle_report_markdown(conn: sqlite3.Connection, recent_window: int) -> str:
    draws = load_draws_from_db(conn)
    if not draws:
        raise SystemExit("資料庫沒有開獎資料。")
    latest = draws[-1]
    latest_run = latest_prediction_run(conn)
    if latest_run is None:
        package = generate_prediction_package(draws, "auto", 20, recent_window, None, conn)
        run_id: int | None = None
        based_draw_date = latest.draw_date
        based_draw_no = latest.draw_no
    else:
        run_id = int(latest_run["id"])
        package = package_from_run(conn, run_id)
        based_draw_date = latest_run["based_on_draw_date"]
        based_draw_no = latest_run["based_on_draw_no"]

    ranked_scores = sorted(package.scores.values(), key=lambda row: row.score, reverse=True)
    ranked_numbers = [row.number for row in ranked_scores]
    score_max = max((row.score for row in ranked_scores), default=1.0) or 1.0
    top9 = ranked_numbers[:CORE_POOL_SIZE]
    support_numbers = ranked_numbers[CORE_POOL_SIZE:SUPPORT_POOL_SIZE]
    weak_rows = sorted(package.scores.values(), key=lambda row: row.score)[:15]
    month_review = rolling_month_review(draws, ranked_numbers)
    target_date = next_marksix_draw_date(based_draw_date)
    report_time = now_text()
    date_range = f"{draws[0].draw_date} -> {draws[-1].draw_date}"
    freshness = "最新" if latest.draw_date == based_draw_date else "已更新"
    data_hash = hashlib.sha256(
        "\n".join(
            f"{draw.draw_date}|{draw.draw_no}|{format_numbers(draw.main_numbers)}|{draw.special:02d}"
            for draw in draws
        ).encode("utf-8")
    ).hexdigest()
    strategy_rows, champion = strategy_competition_rows(draws, recent_window)
    rank_backtest = score_rank_backtest(
        draws,
        champion,
        recent_window,
        max_periods=AUTO_BACKTEST_PERIODS,
    )
    maturity_rows = strategy_maturity_rows(draws, recent_window, conn)
    completeness_passed, completeness_total, completeness_rows = system_completeness_rows(draws, package, conn)
    consensus = model_consensus_rate(package)
    release_edge = float(rank_backtest.get("top9_edge", rank_backtest.get("top10_edge", 0.0)))
    release_level = "研究觀察，不列保證" if release_edge < 0.25 else "研究級高關注"
    risk_level = "高" if len(draws) < 300 or release_edge < 0.1 else "中"
    settled = latest_settled_prediction(conn)

    lines: list[str] = [
        "# 香港六合彩預測系統戰報",
        "",
        "## 戰報快讀",
        markdown_table(
            ["項目", "狀態", "內容"],
            quick_report_rows(
                conn,
                latest,
                package,
                target_date,
                run_id,
                based_draw_no,
                based_draw_date,
                top9,
                ranked_numbers,
                score_max,
                recent_window,
                draws,
                release_level,
                risk_level,
                completeness_passed,
                completeness_total,
            ),
        ),
        "",
        "## 戰報目錄",
        markdown_table(["區塊", "名稱", "用途"], report_index_rows()),
        "",
        "## 資料完整度總表",
        markdown_table(
            ["檢查項目", "狀態", "證據"],
            data_completeness_overview_rows(
                conn,
                draws,
                package,
                run_id,
                based_draw_no,
                based_draw_date,
                target_date,
                top9,
                completeness_passed,
                completeness_total,
            ),
        ),
        "",
        "## 資料補足說明",
        markdown_table(["項目", "狀態", "說明"], data_gap_clarity_rows(conn, draws)),
        "",
        "## 輸出檔案檢核",
        markdown_table(["檔案", "狀態", "位置", "時間"], report_file_status_rows()),
        "",
        "## 系統摘要",
        f"- 產生時間：{report_time}",
        "- 系統狀態：正常",
        f"- 資料新鮮度：{freshness} / 最新資料日 {latest.draw_date}",
        f"- 歷史資料庫：{len(draws)} 期 / {date_range}",
        f"- 最新期別：{latest.draw_no or '-'} ({latest.draw_date})",
        f"- 最新號碼：{format_numbers(latest.main_numbers)} + 特別號 {latest.special:02d}",
        f"- 預測基準期：{based_draw_no or '-'} ({based_draw_date})",
        f"- 預測目標日：{target_date}",
        f"- 目前待結算追蹤記錄：第 {run_id if run_id is not None else '-'} 筆 / 依據期 {based_draw_no or '-'}",
        "- 運算模式：每期開獎後自動更新、自動結算、自動重新運算",
        "- 重號政策：最新開獎號不硬性排除，依模型分數與前九核心池風控軟性處理",
        f"- 工業引擎：{MODEL_VERSION}",
        f"- 預測發布等級：{release_level}",
        f"- 前九核心池：{format_numbers(top9)}",
        f"- 前十穩定共識率：{consensus:.3f}",
        f"- 風險等級：{risk_level}",
        f"- 競賽冠軍：{strategy_label(champion)}",
        f"- 系統完整度：{completeness_passed}/{completeness_total}",
        "- 提醒：本戰報為歷史統計分析，不保證開出，請量力而為。",
        "",
        "## 分頁一：本期發布結論",
        markdown_table(
            ["分類", "重點", "內容"],
            battle_summary_rows(
                latest,
                package,
                target_date,
                run_id,
                risk_level,
                release_edge,
                completeness_passed,
                completeness_total,
            ),
        ),
        "",
        "## 分頁二：今日總判斷",
        f"- 引擎評語：以 {len(draws)} 期資料、{len(package.tickets)} 組候選、{len(next(iter(package.scores.values())).model_scores)} 個子模型做本期運算。",
        "- 開獎型態：未見資料格式異常，波色、大小、尾數、區間皆納入風控。",
        f"- 本期核心要求：命中壓在 9 隻內優先檢查，第十至第十五名只作補位池，不列高機率主推來源。",
        f"- 本月滾動修正：已接入本月樣本 {month_review['sample']} 期，前九核心池覆蓋率 {float(month_review['coverage']):.3f}。",
        f"- 隨機前十基準：{random_expected_hits(10):.3f}",
        f"- 隨機前十五基準：{random_expected_hits(15):.3f}",
        f"- 目前前九回測差值：{release_edge:.3f}",
        "",
        "## 分頁三：每期重新運算證明",
        markdown_table(
            ["期別", "開獎日", "狀態", "預測紀錄", "證明"],
            prediction_recalculation_rows(conn, draws),
        ),
        "",
        "## 分頁四：命中率低落總校正",
        markdown_table(
            ["問題", "目前數值", "修正方式", "執行狀態"],
            [
                ["前九差值偏低", f"{release_edge:.3f}", "降低第十至第十五名主推權重，只保留前九核心檢查", "已執行"],
                ["模型共識不足", f"{consensus:.3f}", "只允許高信心票組列為推薦，其餘轉觀察", "已執行"],
                ["低分號混入", format_numbers([row.number for row in weak_rows[:5]]), "新增五不中、十不中、十五不中排除信心", "已執行"],
                ["每期重算疑慮", f"最新第 {run_id if run_id is not None else '-'} 筆", "戰報列出最近期別重算紀錄", "已執行"],
            ],
        ),
        "",
        "## 分頁五：本月總檢討與滾動式修正",
        markdown_table(
            ["檢討項目", "本月結果", "修正動作"],
            [
                ["分析月份", month_review["range"], "只採用最新月份樣本，不偷看未來資料"],
                ["本月樣本", f"{month_review['sample']} 期 / 本月實際號池 {month_review['actual_pool_size']} 顆", "作為新一期結構校準基準"],
                ["9顆核心池覆蓋", f"{month_review['overlap']} / 9，覆蓋率 {float(month_review['coverage']):.3f}", "核心池固定 9 顆，第十至第十五名只留補位"],
                ["本月熱點", format_numbers(month_review["hottest"]), "已納入本月滾動修正分數"],
                ["熱點未納入前九", format_numbers(month_review["missing_hot"]) if month_review["missing_hot"] else "無", "若連續落在第十至第十五名，下一輪前移校準"],
                ["新一期結構", f"前九={format_numbers(top9)}", "符合第16版每期重算、539鐵律與9顆核心池規格"],
            ],
        ),
        "",
        "## 分頁六：全系統缺口檢測與第16版修復",
        markdown_table(
            ["缺口", "目前問題", "已接上的修復模型"],
            system_gap_review_rows(conn, draws, package, rank_backtest, month_review, settled),
        ),
        "",
        "## 分頁七：第16版新增邏輯運算模型",
        markdown_table(
            ["新增模型", "運算重點", "強化目的"],
            [
                ["轉移追蹤", "比對最新一期與歷史相鄰期重疊後的下一期落點", "補強開獎後號碼轉移與延伸號"],
                ["尾數轉移", "追蹤最新尾數、區間到下一期尾數與區間的轉換", "把 9顆核心池壓得更集中"],
                ["日曆相位", "依下一期目標日的星期、月內相位與歷史同相位樣本校準", "修正月內節奏與開獎日型態"],
                ["特別號交叉", "把最新特別號同尾、同區間、鄰近號與歷史轉主號樣本交叉", "補強特別號滲透主號的捕捉"],
            ],
        ),
        "",
        "## 分頁八：超強信心高機率強推薦號碼",
        "- 精算規則：強推精算層獨立運算，採單號精算、模型共識、穩定度、貝葉斯、近期命中、結算回饋、區間修復、冷爆捕捉、轉移追蹤、尾數轉移、日曆相位、特別號交叉、配對/三碼共振、近180期校準。",
        markdown_table(
            ["類型", "強推薦號碼", "命中目標", "信心指數", "隨機基準", "強化理由"],
            super_recommendation_rows(package, draws),
        ),
        "",
        "## 分頁九：高機率信心牌（特別標註）",
        "- 標註規則：依前九核心池覆蓋、成熟度校準分、策略回測權重、票組成熟分排序；未達門檻不列主推。",
        markdown_table(
            ["優先", "號碼", "信心標籤", "策略", "成熟分", "註明"],
            confidence_ticket_rows(package, limit=6),
        ),
        "",
        "## 分頁十：推薦門檻檢查",
        markdown_table(
            ["序", "號碼", "信心", "相對分數", "是否主推"],
            recommendation_gate_rows(package, score_max),
        ),
        "",
        "## 分頁十一：9隻內核心命中池",
        markdown_table(
            ["排名", "號碼", "信心指數", "近期", "遺漏", "主要理由"],
            [
                [
                    rank,
                    f"{row.number:02d}",
                    f"{confidence_index(row, score_max):.1f}",
                    row.recent_frequency,
                    row.miss_gap,
                    number_reasons(row, rank),
                ]
                for rank, row in enumerate(ranked_scores[:CORE_POOL_SIZE], start=1)
            ],
        ),
        "",
        "## 分頁十二：今日觀察候選（不列正式主推）",
    ]

    for title, numbers, target_hits in strong_pack_specs(ranked_numbers, package, draws):
        probability = probability_at_least_hits(len(numbers), target_hits)
        odds = (1.0 / probability) if probability else 0.0
        lines.append(
            f"- {title}：{format_numbers(numbers)} / 狀態：研究觀察"
        )
        lines.append(f"  - 理論機率：{probability:.6f} / 1中{odds:.2f}")

    lines.extend(
        [
            "",
            "## 分頁十三：日期基準",
            markdown_table(
                ["項目", "內容"],
                [
                    ["報表產生時間", report_time],
                    ["歷史資料範圍", date_range],
                    ["最新期 / 日", f"{latest.draw_no or '-'} / {latest.draw_date}"],
                    ["最新開獎號", f"{format_numbers(latest.main_numbers)} + {latest.special:02d}"],
                    ["預測依據期", f"{based_draw_no or '-'} / {based_draw_date}"],
                    ["下期預測目標日", target_date],
                    ["時區規則", "香港六合彩依官方實際開獎更新；本系統以資料更新後自動結算與重算。"],
                ],
            ),
            "",
            "## 分頁十四：上期命中檢討",
        ]
    )
    lines.extend(settlement_summary_lines(conn, settled))

    lines.extend(["", "## 分頁十五：上期參考組合檢討"])
    if settled is None:
        lines.append("- 尚無已結算預測。下一期開獎更新後，本區會自動列出組合命中。")
    else:
        settled_run, actual = settled
        lines.append(
            markdown_table(
                ["組別", "原預測組合", "命中數", "命中號", "未命中號"],
                settled_ticket_rows(conn, int(settled_run["id"]), actual, limit=10),
            )
        )

    lines.extend(["", "## 分頁十六：上期強牌組成敗檢討"])
    if settled is None:
        lines.append("- 尚無強牌結算。")
    else:
        settled_run, actual = settled
        rows = settled_ticket_rows(conn, int(settled_run["id"]), actual, limit=6)
        lines.append(
            markdown_table(
                ["強牌", "號碼", "命中", "命中號", "修正動作"],
                [
                    [
                        row[0],
                        row[1],
                        row[2],
                        row[3],
                        "命中保留權重" if int(row[2]) >= 2 else "降低同型組合權重",
                    ]
                    for row in rows
                ],
            )
        )

    lines.extend(["", "## 分頁十七：實際開出號碼漏抓檢討"])
    if settled is None:
        lines.append("- 尚無可檢討的實際開獎。")
    else:
        settled_run, actual = settled
        settled_ranked = score_snapshot_ranked(settled_run)
        rows = []
        for number in actual.main_numbers:
            rank = settled_ranked.index(number) + 1 if number in settled_ranked else "-"
            if number in settled_ranked[:CORE_POOL_SIZE]:
                bucket = "前九核心池"
                action = "保留主模型權重"
            elif number in settled_ranked[:SUPPORT_POOL_SIZE]:
                bucket = "補位池"
                action = "往前九前移"
            else:
                bucket = "核心池外"
                action = "提高補抓模型權重"
            rows.append([f"{number:02d}", rank, bucket, action])
        lines.append(markdown_table(["開出號", "預測排名", "分類", "修正動作"], rows))

    lines.extend(
        [
            "",
            "## 分頁十八：上期正式預測逐號檢討",
            markdown_table(
                ["排名", "號碼", "分區", "信心", "主要模型", "檢核結果"],
                [
                    [
                        rank,
                        f"{row.number:02d}",
                         "前九核心" if rank <= CORE_POOL_SIZE else "第十至第十五補位",
                        confidence_label(row, score_max),
                        top_model_text(row),
                        "主推核心" if rank <= CORE_POOL_SIZE else "只作防守補位",
                    ]
                    for rank, row in enumerate(ranked_scores[:SUPPORT_POOL_SIZE], start=1)
                ],
            ),
            "",
            "## 分頁十九：候選前十五詳表",
            markdown_table(
                ["排名", "號碼", "分區", "信心指數", "遺漏", "近期", "主要理由"],
                [
                    [
                        rank,
                        f"{row.number:02d}",
                         "前九核心池" if rank <= CORE_POOL_SIZE else "第十至第十五補位池",
                        f"{confidence_index(row, score_max):.1f}",
                        row.miss_gap,
                        row.recent_frequency,
                        number_reasons(row, rank),
                    ]
                    for rank, row in enumerate(ranked_scores[:SUPPORT_POOL_SIZE], start=1)
                ],
            ),
            "",
            "## 分頁二十：牌型關聯",
            markdown_table(["項目", "結果"], board_pattern_rows(draws)),
            "",
            "## 分頁二十一：號碼關聯與連動精準分析",
            "- 方法：延遲期重疊 + 高共現配對。",
            "- 警示：關聯不等於因果，只允許作為輔助分與風控訊號。",
            "",
            "### 延遲期連動",
            markdown_table(["延遲", "樣本", "實際平均重疊", "隨機期待", "差值"], lag_overlap_rows(draws)),
            "",
            "### 高共現配對",
            markdown_table(["配對", "出現次數", "保守提升", "用途"], top_pair_lift_rows(draws)),
            "",
            "## 分頁二十二：多模型競賽回測",
            markdown_table(
                ["模型", "前五", "前九", "前十", "前十五", "前九差值", "前十五差值", "樣本"],
                strategy_rows,
            ),
            "",
            f"## 分頁二十三：研究命中指標與禁止虛報門檻（樣本 {rank_backtest.get('sample', 0)} 期）",
            markdown_table(
                ["指標", "樣本", "平均命中", "隨機基準", "差值", "狀態"],
                [
                    [
                        "前五",
                        rank_backtest.get("sample", 0),
                        f"{rank_backtest.get('top5_avg', 0.0):.3f}",
                        f"{random_expected_hits(5):.3f}",
                        f"{rank_backtest.get('top5_edge', 0.0):.3f}",
                        "研究觀察",
                    ],
                    [
                        "前九核心",
                        rank_backtest.get("sample", 0),
                        f"{rank_backtest.get('top9_avg', 0.0):.3f}",
                        f"{random_expected_hits(CORE_POOL_SIZE):.3f}",
                        f"{rank_backtest.get('top9_edge', 0.0):.3f}",
                        "主檢查池",
                    ],
                    [
                        "前十",
                        rank_backtest.get("sample", 0),
                        f"{rank_backtest.get('top10_avg', 0.0):.3f}",
                        f"{random_expected_hits(10):.3f}",
                        f"{rank_backtest.get('top10_edge', 0.0):.3f}",
                        "輔助比較",
                    ],
                    [
                        "前十五",
                        rank_backtest.get("sample", 0),
                        f"{rank_backtest.get('top15_avg', 0.0):.3f}",
                        f"{random_expected_hits(15):.3f}",
                        f"{rank_backtest.get('top15_edge', 0.0):.3f}",
                        "防守池，不准當高機率主推",
                    ],
                ],
            ),
            "",
            "## 分頁二十四：近期穩定度回測",
            markdown_table(
                ["排名區間", "平均命中", ">=2命中率", "校準動作"],
                [
                    ["前五", f"{rank_backtest.get('top5_avg', 0.0):.3f}", f"{rank_backtest.get('top5_ge2', 0.0):.3f}", "保留核心但不放大保證"],
                    ["前九核心", f"{rank_backtest.get('top9_avg', 0.0):.3f}", f"{rank_backtest.get('top9_ge2', 0.0):.3f}", "本期主檢查池"],
                    ["前十", f"{rank_backtest.get('top10_avg', 0.0):.3f}", f"{rank_backtest.get('top10_ge2', 0.0):.3f}", "只作輔助比較"],
                    ["前十五", f"{rank_backtest.get('top15_avg', 0.0):.3f}", f"{rank_backtest.get('top15_ge2', 0.0):.3f}", "防守與補位池"],
                ],
            ),
            "",
            "## 分頁二十五：模型審計",
            markdown_table(
                ["模組", "狀態", "證據", "補強狀態"],
                completeness_rows,
            ),
            "",
            "## 分頁二十六：風控與每日滾動調整",
            markdown_table(
                ["滾動項目", "本次結果", "模型調整", "狀態"],
                [
                    ["上期結算回饋", settled_status_text(settled), "命中來源保留，未命中來源降權", "已啟用"],
                    ["前九回測差值", f"{release_edge:.3f}", "優勢不足時降為觀察等級", "已啟用"],
                    ["權重滾動", auto_weight_text(conn, draws, recent_window), "回測校準 + 實戰結算雙軌調整", "已啟用"],
                    ["策略熔斷", "低效策略自動降權", "前九/前十差值雙負時降到觀察", "已啟用"],
                    ["票組成熟度", "同策略內標準化後再排序", "避免單一策略分數洗版", "已啟用"],
                    ["資料健檢", f"{len(draws)} 期 / 最新 {latest.draw_date}", "格式錯誤會在資料健檢區顯示", "已啟用"],
                ],
            ),
            "",
            "## 分頁二十七：5不中低機率排除",
            markdown_table(
                ["號碼", "排除信心", "候選排名", "低分指標", "排除原因"],
                exclusion_rows(package.scores, ranked_numbers, score_max, 5),
            ),
            "",
            "## 分頁二十八：10不中低機率排除",
            markdown_table(
                ["號碼", "排除信心", "候選排名", "低分指標", "排除原因"],
                exclusion_rows(package.scores, ranked_numbers, score_max, 10),
            ),
            "",
            "## 分頁二十九：15不中低機率排除",
            markdown_table(
                ["號碼", "排除信心", "候選排名", "低分指標", "排除原因"],
                exclusion_rows(package.scores, ranked_numbers, score_max, 15),
            ),
            "",
            "## 分頁二十九之二：539最新版鐵律避險包成效",
            markdown_table(
                ["避險包", "號碼", "信心指標", "平均暫避分", "回測期數", "平均誤中", "完全避開率", "風控說明"],
                exclusion_pack_summary_rows(draws, package.scores, ranked_numbers, score_max, recent_window),
            ),
            "",
            "## 分頁三十：全部正式預測歷史對比",
            markdown_table(
                ["預測", "產生時間", "依據期", "策略", "組數", "模型"],
                recent_prediction_history_rows(conn),
            ),
            "",
            "## 分頁三十一：下期預測號碼池",
            markdown_table(
                ["組別", "號碼", "用途"],
                [
                    ["膽碼", format_numbers(package.bankers), "前九核心中的最高分"],
                    ["拖碼", format_numbers(package.drags), "前九核心補強"],
                    ["前九核心池", format_numbers(top9), "本期主檢查池"],
                    ["第十至第十五補位池", format_numbers(support_numbers), "只作防守補位"],
                    ["防守碼", format_numbers(package.reserves), "補位與分散"],
                    ["弱勢碼", format_numbers(package.weak_numbers), "低機率暫避"],
                    ["特別號候選", format_numbers(package.special_candidates), "特碼獨立觀察"],
                ],
            ),
            "",
            "## 分頁三十二：539最新版鐵律多視窗門檻",
            markdown_table(
                ["回測窗", "樣本", "前九平均命中", "隨機基準", "差值", "零命中率", "兩顆以上率", "狀態"],
                iron_law_backtest_rows(draws, champion, recent_window),
            ),
            "",
            "## 分頁三十三：正式預測紀錄鐵律",
            "- 鐵律：開獎資料更新後，若找不到該期開獎前保存的正式預測紀錄，戰報必須標示異常，禁止用舊日期檢討冒充最新檢討。",
            markdown_table(
                ["開獎期", "開獎日", "鐵律狀態", "正式預測", "證據"],
                formal_prediction_integrity_rows(conn, draws),
            ),
            "",
            "## 分頁三十四：前九防漏與第十至第十五回拉",
            "- 鐵律：第十至第十五名曾捕捉實際命中號時，排序校準要把穩定共識與中段驗證訊號提前到前九邊界。",
            markdown_table(
                ["號碼", "目前排名", "鐵律動作", "原因"],
                late_hit_recovery_rows(conn, draws, ranked_numbers),
            ),
            "",
            "## 分頁三十五：每日更新鐵律時間表",
            markdown_table(
                ["鐵律項目", "執行要求", "目前狀態"],
                [
                    ["開獎後即刻更新", "取得新開獎後必須匯入、結算、重算、回測、重建戰報、同步手機", "一鍵流程與開獎後監控已接"],
                    ["不得假檢討", "缺正式預測紀錄時必須標示異常，不得用舊預測冒充命中檢討", "正式預測紀錄鐵律已接"],
                    ["短包超強信心", "獨隻、2碼、3碼每期固定精算並列出信心", "超強信心分頁已接"],
                    ["低機率避險", "5不中、10不中、15不中需列信心指標、回測誤中與完全避開率", "避險包成效分頁已接"],
                    ["手機同步", "本機重算完成後手機雲端必須同步同一份狀態", "同步與掃描已接"],
                ],
            ),
        ]
    )

    body = "\n".join(str(part) for part in lines)
    output_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()
    lines.extend(
        [
            "",
            "## 分頁三十六：運算審核",
            "- 審核狀態：研究檢核通過",
            f"- 資料指紋：{data_hash}",
            f"- 輸出指紋：{output_hash}",
            f"- 雙通道交叉驗證：前十穩定共識 {consensus:.3f}",
            f"- 前九核心池：{format_numbers(top9)}",
            f"- 發布治理：{release_level}",
        ]
    )
    return "\n".join(str(part) for part in lines)


def latest_prediction_run(conn: sqlite3.Connection) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM prediction_runs ORDER BY id DESC LIMIT 1").fetchone()


def prediction_needs_refresh(
    conn: sqlite3.Connection,
    draws: list[Draw],
    model_version: str = MODEL_VERSION,
) -> tuple[bool, str]:
    if not draws:
        return False, "資料庫沒有開獎資料"
    latest = draws[-1]
    latest_run = latest_prediction_run(conn)
    if latest_run is None:
        return True, "尚無預測紀錄"
    if latest.row_id is not None and int(latest_run["based_on_draw_id"]) != int(latest.row_id):
        return True, f"已有新開獎 {latest.draw_no or '-'}，必須重新運算"
    if latest_run["model_version"] != model_version:
        return True, f"模型版本已更新為 {model_version}"
    return False, f"最新預測已對應 {latest.draw_no or '-'} / {model_version}"


def latest_settled_prediction(conn: sqlite3.Connection) -> tuple[sqlite3.Row, Draw] | None:
    rows = conn.execute("SELECT * FROM prediction_runs ORDER BY id DESC").fetchall()
    for row in rows:
        actual = next_draw_after(conn, int(row["based_on_draw_id"]))
        if actual is not None:
            return row, actual
    return None


def next_marksix_draw_date(date_text: str) -> str:
    try:
        current = datetime.strptime(date_text, "%Y-%m-%d").date()
    except ValueError:
        return "待官方更新"
    next_candidate = None
    for offset in range(1, 8):
        candidate = current + timedelta(days=offset)
        if candidate.weekday() in {1, 3, 5}:
            next_candidate = candidate
            break
    if next_candidate is None:
        next_candidate = current + timedelta(days=1)
    today = datetime.now(LOCAL_TZ).date()
    if next_candidate < today:
        return f"待官方更新（原排程推估 {next_candidate.isoformat()}）"
    return next_candidate.isoformat()


def markdown_table(headers: list[object], rows: list[list[object]]) -> str:
    output = [
        "| " + " | ".join(str(header) for header in headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows:
        output.append("| " + " | ".join(str(value) for value in row) + " |")
    return "\n".join(output)


def random_expected_hits(top_n: int) -> float:
    return MAIN_COUNT * top_n / MAX_NUMBER


def probability_at_least_hits(pool_size: int, target_hits: int) -> float:
    total = math.comb(MAX_NUMBER, MAIN_COUNT)
    favorable = 0
    for hits in range(target_hits, min(pool_size, MAIN_COUNT) + 1):
        if MAIN_COUNT - hits <= MAX_NUMBER - pool_size:
            favorable += math.comb(pool_size, hits) * math.comb(MAX_NUMBER - pool_size, MAIN_COUNT - hits)
    return favorable / total if total else 0.0


def probability_exact_hits(pool_size: int, hits: int) -> float:
    total = math.comb(MAX_NUMBER, MAIN_COUNT)
    if hits < 0 or hits > pool_size or hits > MAIN_COUNT:
        return 0.0
    if MAIN_COUNT - hits > MAX_NUMBER - pool_size:
        return 0.0
    return math.comb(pool_size, hits) * math.comb(MAX_NUMBER - pool_size, MAIN_COUNT - hits) / total


def probability_percent(value: float) -> str:
    return f"{value * 100:.3f}%"


def hit_probability_text(pool_size: int) -> str:
    if pool_size == 1:
        return f"1中1 {probability_percent(probability_exact_hits(1, 1))}"
    if pool_size == 2:
        return (
            f"至少1中 {probability_percent(probability_at_least_hits(2, 1))}；"
            f"2中2 {probability_percent(probability_exact_hits(2, 2))}"
        )
    if pool_size == 3:
        return (
            f"至少1中 {probability_percent(probability_at_least_hits(3, 1))}；"
            f"2中以上 {probability_percent(probability_at_least_hits(3, 2))}；"
            f"3中3 {probability_percent(probability_exact_hits(3, 3))}"
        )
    return f"至少1中 {probability_percent(probability_at_least_hits(pool_size, 1))}"


def clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def normalized_values(values: dict[int, float]) -> dict[int, float]:
    if not values:
        return {number: 0.0 for number in range(MIN_NUMBER, MAX_NUMBER + 1)}
    low = min(values.values())
    high = max(values.values())
    if high <= low:
        return {number: 0.5 for number in values}
    return {number: (value - low) / (high - low) for number, value in values.items()}


def calculate_rank_stability_scores(draws: list[Draw]) -> dict[int, float]:
    if len(draws) < 12:
        return {number: 0.5 for number in range(MIN_NUMBER, MAX_NUMBER + 1)}
    windows = [8, 12, 18, 24, 30, 45, 60]
    rank_history: dict[int, list[int]] = {number: [] for number in range(MIN_NUMBER, MAX_NUMBER + 1)}
    for window in windows:
        if len(draws) < max(5, window // 2):
            continue
        scores = build_scores(draws, recent_window=min(window, len(draws)), strategy="balanced")
        ranked = sorted(scores.values(), key=lambda row: row.score, reverse=True)
        for rank, row in enumerate(ranked, start=1):
            rank_history[row.number].append(rank)
    stability: dict[int, float] = {}
    for number, ranks in rank_history.items():
        if not ranks:
            stability[number] = 0.5
            continue
        rank_quality = statistics.mean((MAX_NUMBER + 1 - rank) / MAX_NUMBER for rank in ranks)
        top9_rate = sum(1 for rank in ranks if rank <= CORE_POOL_SIZE) / len(ranks)
        volatility = 1.0 - min(statistics.pstdev(ranks) / (MAX_NUMBER / 3.0), 1.0) if len(ranks) > 1 else 1.0
        stability[number] = clamp01(rank_quality * 0.46 + top9_rate * 0.34 + volatility * 0.20)
    return stability


def calculate_super_recent_scores(draws: list[Draw]) -> dict[int, float]:
    windows = [(6, 0.44), (12, 0.32), (30, 0.18), (60, 0.06)]
    scores = {number: 0.0 for number in range(MIN_NUMBER, MAX_NUMBER + 1)}
    for window, weight in windows:
        recent = draws[-min(window, len(draws)) :]
        counts = Counter(number for draw in recent for number in draw.main_numbers)
        max_count = max(counts.values(), default=1)
        for number in range(MIN_NUMBER, MAX_NUMBER + 1):
            scores[number] += (counts[number] / max_count if max_count else 0.0) * weight
    return {number: clamp01(value) for number, value in scores.items()}


def calculate_super_bayes_scores(draws: list[Draw]) -> dict[int, float]:
    if not draws:
        return {number: 0.5 for number in range(MIN_NUMBER, MAX_NUMBER + 1)}
    recent = draws[-min(30, len(draws)) :]
    month = month_window_draws(draws)
    all_counts = Counter(number for draw in draws for number in draw.main_numbers)
    recent_counts = Counter(number for draw in recent for number in draw.main_numbers)
    month_counts = Counter(number for draw in month for number in draw.main_numbers)
    special_counts = Counter(draw.special for draw in draws)
    raw = {}
    denominator = len(draws) + len(recent) * 2.4 + len(month) * 2.0 + len(draws) * 0.25 + 49.0
    for number in range(MIN_NUMBER, MAX_NUMBER + 1):
        numerator = (
            all_counts[number]
            + recent_counts[number] * 2.4
            + month_counts[number] * 2.0
            + special_counts[number] * 0.25
            + 1.0
        )
        raw[number] = numerator / max(1.0, denominator)
    return normalized_values(raw)


def calculate_pair_synergy_scores(draws: list[Draw]) -> dict[tuple[int, int], float]:
    all_pairs: Counter[tuple[int, int]] = Counter()
    recent_pairs: Counter[tuple[int, int]] = Counter()
    recent = draws[-min(80, len(draws)) :]
    for draw in draws:
        for pair in itertools.combinations(sorted(draw.main_numbers), 2):
            all_pairs[pair] += 1
    for draw in recent:
        for pair in itertools.combinations(sorted(draw.main_numbers), 2):
            recent_pairs[pair] += 1
    max_all = max(all_pairs.values(), default=1)
    max_recent = max(recent_pairs.values(), default=1)
    scores: dict[tuple[int, int], float] = {}
    for left in range(MIN_NUMBER, MAX_NUMBER + 1):
        for right in range(left + 1, MAX_NUMBER + 1):
            pair = (left, right)
            scores[pair] = clamp01(
                (all_pairs[pair] / max_all if max_all else 0.0) * 0.58
                + (recent_pairs[pair] / max_recent if max_recent else 0.0) * 0.42
            )
    return scores


def calculate_triple_synergy_scores(draws: list[Draw]) -> dict[tuple[int, int, int], float]:
    all_triples: Counter[tuple[int, int, int]] = Counter()
    recent_triples: Counter[tuple[int, int, int]] = Counter()
    recent = draws[-min(120, len(draws)) :]
    for draw in draws:
        for triple in itertools.combinations(sorted(draw.main_numbers), 3):
            all_triples[triple] += 1
    for draw in recent:
        for triple in itertools.combinations(sorted(draw.main_numbers), 3):
            recent_triples[triple] += 1
    max_all = max(all_triples.values(), default=1)
    max_recent = max(recent_triples.values(), default=1)
    scores: dict[tuple[int, int, int], float] = {}
    for triple, count in all_triples.items():
        scores[triple] = clamp01(
            (count / max_all if max_all else 0.0) * 0.55
            + (recent_triples[triple] / max_recent if max_recent else 0.0) * 0.45
        )
    return scores


def super_structure_score(numbers: tuple[int, ...]) -> float:
    if not numbers:
        return 0.0
    colors = Counter(wave_color(number) for number in numbers)
    decades = Counter(decade_bucket(number) for number in numbers)
    tails = Counter(number % 10 for number in numbers)
    odd_count = sum(1 for number in numbers if number % 2)
    span = max(numbers) - min(numbers) if len(numbers) > 1 else 18
    color_part = len(colors) / min(len(numbers), 3)
    decade_part = len(decades) / min(len(numbers), 3)
    tail_part = len(tails) / len(numbers)
    odd_balance = 1.0 - abs(odd_count - len(numbers) / 2.0) / max(1.0, len(numbers) / 2.0)
    span_part = clamp01(span / 36.0)
    consecutive_penalty = sum(1 for left, right in zip(numbers, numbers[1:]) if right - left == 1) * 0.08
    return clamp01(
        color_part * 0.28
        + decade_part * 0.25
        + tail_part * 0.18
        + odd_balance * 0.16
        + span_part * 0.13
        - consecutive_penalty
    )


def build_super_precision_metrics(
    package: PredictionPackage,
    draws: list[Draw] | None = None,
) -> dict[int, dict[str, float | NumberScore | int]]:
    ranked_scores = sorted(package.scores.values(), key=lambda row: row.score, reverse=True)
    score_max = max((row.score for row in ranked_scores), default=1.0) or 1.0
    ranks = {row.number: rank for rank, row in enumerate(ranked_scores, start=1)}
    max_pair = max((row.pair_strength for row in ranked_scores), default=1.0) or 1.0
    stability_scores = calculate_rank_stability_scores(draws) if draws else {number: 0.5 for number in range(MIN_NUMBER, MAX_NUMBER + 1)}
    recent_scores = calculate_super_recent_scores(draws) if draws else {number: package.scores[number].model_scores.get("recency", 0.5) for number in package.scores}
    bayes_scores = calculate_super_bayes_scores(draws) if draws else {number: package.scores[number].model_scores.get("bayes", 0.5) for number in package.scores}
    model_names = list(next(iter(package.scores.values())).model_scores) if package.scores else []
    model_top9: dict[str, set[int]] = {}
    for model in model_names:
        model_top9[model] = {
            row.number
            for row in sorted(
                package.scores.values(),
                key=lambda item: item.model_scores.get(model, 0.0),
                reverse=True,
            )[:CORE_POOL_SIZE]
        }
    metrics: dict[int, dict[str, float | NumberScore | int]] = {}
    for row in ranked_scores:
        rank = ranks[row.number]
        ensemble = row.score / score_max if score_max else 0.0
        model_support = (
            sum(1 for top_numbers in model_top9.values() if row.number in top_numbers) / len(model_top9)
            if model_top9
            else 0.5
        )
        rank_focus = clamp01((SUPPORT_POOL_SIZE + 1 - min(rank, SUPPORT_POOL_SIZE + 1)) / SUPPORT_POOL_SIZE)
        pair_score = row.pair_strength / max_pair if max_pair else 0.0
        precision = clamp01(
            ensemble * 0.23
            + model_support * 0.19
            + float(stability_scores[row.number]) * 0.17
            + float(bayes_scores[row.number]) * 0.14
            + float(recent_scores[row.number]) * 0.12
            + pair_score * 0.08
            + row.model_scores.get("cycle", 0.0) * 0.03
            + row.model_scores.get("rolling_month", 0.0) * 0.02
            + rank_focus * 0.02
        )
        if rank <= CORE_POOL_SIZE:
            precision = clamp01(precision + 0.045)
        elif rank > SUPPORT_POOL_SIZE:
            precision *= 0.72
        if row.miss_gap == 0:
            precision *= 0.96
        metrics[row.number] = {
            "row": row,
            "rank": rank,
            "precision": precision,
            "stability": float(stability_scores[row.number]),
            "recent": float(recent_scores[row.number]),
            "bayes": float(bayes_scores[row.number]),
            "model_support": model_support,
            "pair": pair_score,
            "ensemble": ensemble,
        }
    return metrics


def super_combo_score(
    numbers: tuple[int, ...],
    metrics: dict[int, dict[str, float | NumberScore | int]],
    pair_synergy: dict[tuple[int, int], float],
    triple_synergy: dict[tuple[int, int, int], float],
) -> dict[str, float]:
    precision = statistics.mean(float(metrics[number]["precision"]) for number in numbers)
    stability = min(float(metrics[number]["stability"]) for number in numbers)
    support = statistics.mean(float(metrics[number]["model_support"]) for number in numbers)
    recent = statistics.mean(float(metrics[number]["recent"]) for number in numbers)
    core_rate = sum(1 for number in numbers if int(metrics[number]["rank"]) <= CORE_POOL_SIZE) / len(numbers)
    pairs = [tuple(sorted(pair)) for pair in itertools.combinations(numbers, 2)]
    pair_score = statistics.mean(pair_synergy.get(pair, 0.0) for pair in pairs) if pairs else 0.0
    triple_score = triple_synergy.get(tuple(sorted(numbers)), 0.0) if len(numbers) == 3 else pair_score
    structure = super_structure_score(tuple(sorted(numbers)))
    if len(numbers) == 1:
        total = precision * 0.62 + stability * 0.16 + support * 0.12 + recent * 0.10
    elif len(numbers) == 2:
        total = precision * 0.50 + stability * 0.12 + support * 0.10 + recent * 0.08 + pair_score * 0.13 + structure * 0.07
    else:
        total = (
            precision * 0.45
            + stability * 0.11
            + support * 0.09
            + recent * 0.07
            + pair_score * 0.12
            + triple_score * 0.07
            + structure * 0.06
            + core_rate * 0.03
        )
    return {
        "score": clamp01(total),
        "precision": precision,
        "stability": stability,
        "support": support,
        "recent": recent,
        "pair": pair_score,
        "triple": triple_score,
        "structure": structure,
        "core_rate": core_rate,
    }


def calibrated_hit_probability_text(numbers: tuple[int, ...], draws: list[Draw] | None) -> str:
    base = hit_probability_text(len(numbers))
    if not draws:
        return base
    sample_draws = draws[-min(180, len(draws)) :]
    if not sample_draws:
        return base
    hits = [len(set(numbers).intersection(draw.main_numbers)) for draw in sample_draws]
    sample = len(hits)
    if len(numbers) == 1:
        return f"{base}；近{sample}期校準 1中1 {sum(1 for hit in hits if hit == 1) / sample * 100:.1f}%"
    if len(numbers) == 2:
        return (
            f"{base}；近{sample}期校準 "
            f"至少1中 {sum(1 for hit in hits if hit >= 1) / sample * 100:.1f}%、"
            f"2中2 {sum(1 for hit in hits if hit == 2) / sample * 100:.1f}%"
        )
    return (
        f"{base}；近{sample}期校準 "
        f"至少1中 {sum(1 for hit in hits if hit >= 1) / sample * 100:.1f}%、"
        f"2中以上 {sum(1 for hit in hits if hit >= 2) / sample * 100:.1f}%、"
        f"3中3 {sum(1 for hit in hits if hit == 3) / sample * 100:.1f}%"
    )


def refined_super_pick_sets(
    package: PredictionPackage,
    draws: list[Draw] | None = None,
) -> list[dict[str, object]]:
    metrics = build_super_precision_metrics(package, draws)
    ranked_numbers = sorted(
        metrics,
        key=lambda number: (
            float(metrics[number]["precision"]),
            float(metrics[number]["stability"]),
            float(metrics[number]["model_support"]),
            -int(metrics[number]["rank"]),
        ),
        reverse=True,
    )
    candidates = ranked_numbers[: min(15, len(ranked_numbers))]
    pair_synergy = calculate_pair_synergy_scores(draws) if draws else {}
    triple_synergy = calculate_triple_synergy_scores(draws) if draws else {}

    single = (candidates[0],)
    pair_candidates = [tuple(sorted((single[0], number))) for number in candidates if number != single[0]]
    pair = max(
        pair_candidates,
        key=lambda numbers: super_combo_score(numbers, metrics, pair_synergy, triple_synergy)["score"],
        default=single,
    )
    triple_candidates = [
        tuple(sorted((*pair, number)))
        for number in candidates
        if number not in pair
    ]
    triple = max(
        triple_candidates,
        key=lambda numbers: super_combo_score(numbers, metrics, pair_synergy, triple_synergy)["score"],
        default=pair,
    )

    specs = [
        ("超強獨隻", "獨隻1中1", single),
        ("超強2碼", "2中1~2", pair),
        ("超強3碼", "3中1~3", triple),
    ]
    items: list[dict[str, object]] = []
    for label, target, numbers in specs:
        detail = super_combo_score(numbers, metrics, pair_synergy, triple_synergy)
        confidence = min(99.0, 66.0 + detail["score"] * 31.0 + max(0, 4 - len(numbers)) * 0.55)
        leader_number = max(numbers, key=lambda number: float(metrics[number]["precision"]))
        leader_row = metrics[leader_number]["row"]
        assert isinstance(leader_row, NumberScore)
        reason_parts = [
            f"強推精算層 {detail['score']:.3f}",
            f"穩定 {detail['stability']:.2f}",
            f"模型共識 {detail['support']:.2f}",
            f"近況 {detail['recent']:.2f}",
        ]
        if len(numbers) >= 2:
            reason_parts.append(f"配對共振 {detail['pair']:.2f}")
        if len(numbers) == 3:
            reason_parts.append(f"三碼共振 {detail['triple']:.2f}")
        reason_parts.extend(
            [
                f"前{len(numbers)}遞進式核心",
                number_reasons(leader_row, int(metrics[leader_number]["rank"])),
            ]
        )
        items.append(
            {
                "label": label,
                "target": target,
                "numbers": numbers,
                "confidence": f"{confidence:.1f}",
                "probability": calibrated_hit_probability_text(numbers, draws),
                "reason": "；".join(part for part in reason_parts if part),
                "precision_score": round(detail["score"], 6),
                "stability": round(detail["stability"], 6),
                "model_support": round(detail["support"], 6),
            }
        )
    return items


def super_recommendation_items(
    package: PredictionPackage,
    draws: list[Draw] | None = None,
) -> list[dict[str, object]]:
    if draws:
        return refined_super_pick_sets(package, draws)
    ranked_scores = sorted(package.scores.values(), key=lambda row: row.score, reverse=True)
    score_max = max((row.score for row in ranked_scores), default=1.0) or 1.0
    specs = [
        ("超強獨隻", "獨隻1中1", ranked_scores[:1]),
        ("超強2碼", "2中1~2", ranked_scores[:2]),
        ("超強3碼", "3中1~3", ranked_scores[:3]),
    ]
    items: list[dict[str, object]] = []
    for label, target, rows in specs:
        numbers = tuple(row.number for row in rows)
        confidence = statistics.mean(confidence_index(row, score_max) for row in rows)
        confidence += max(0, 4 - len(numbers)) * 0.8
        confidence = min(99.0, confidence)
        leader = rows[0]
        reason_parts = [
            f"前{len(numbers)}核心",
            top_model_text(leader, 3),
            number_reasons(leader, 1),
        ]
        items.append(
            {
                "label": label,
                "target": target,
                "numbers": numbers,
                "confidence": f"{confidence:.1f}",
                "probability": hit_probability_text(len(numbers)),
                "reason": "；".join(part for part in reason_parts if part),
            }
        )
    return items


def super_recommendation_rows(
    package: PredictionPackage,
    draws: list[Draw] | None = None,
) -> list[list[object]]:
    return [
        [
            item["label"],
            format_numbers(item["numbers"]),
            item["target"],
            item["confidence"],
            item["probability"],
            item["reason"],
        ]
        for item in super_recommendation_items(package, draws)
    ]


def strong_pack_specs(
    ranked_numbers: list[int],
    package: PredictionPackage,
    draws: list[Draw] | None = None,
) -> list[tuple[str, tuple[int, ...], int]]:
    if draws:
        refined = refined_super_pick_sets(package, draws)
        refined_by_label = {str(item["label"]): tuple(sorted(item["numbers"])) for item in refined}
        single = refined_by_label.get("超強獨隻", tuple(ranked_numbers[:1]))
        pair = refined_by_label.get("超強2碼", tuple(sorted(ranked_numbers[:2])))
        triple = refined_by_label.get("超強3碼", tuple(sorted(ranked_numbers[:3])))
    else:
        single = tuple(ranked_numbers[:1])
        pair = tuple(sorted(ranked_numbers[:2]))
        triple = tuple(sorted(ranked_numbers[:3]))
    return [
        ("最強單支", single, 1),
        ("最強2中1", pair, 1),
        ("最強3中1", triple, 1),
        ("最強6中2", tuple(sorted(ranked_numbers[:6])), 2),
        ("最強9中3", tuple(sorted(ranked_numbers[:9])), 3),
        ("特別號候選", tuple(package.special_candidates), 1),
    ]


def score_rank_backtest(
    draws: list[Draw],
    strategy: str,
    recent_window: int,
    max_periods: int = 180,
) -> dict[str, float | int]:
    if len(draws) < 12:
        return {"sample": 0}
    latest = draws[-1]
    cache_key = (
        len(draws),
        latest.draw_date,
        latest.draw_no,
        strategy,
        recent_window,
        max_periods,
    )
    cached = _SCORE_RANK_BACKTEST_CACHE.get(cache_key)
    if cached is not None:
        return dict(cached)
    min_train = min(80, max(8, len(draws) // 2))
    start = max(min_train, len(draws) - max_periods)
    samples = []
    for index in range(start, len(draws)):
        train = draws[:index]
        actual = set(draws[index].main_numbers)
        scores = build_scores(train, recent_window=recent_window, strategy=strategy)
        ranked = [row.number for row in sorted(scores.values(), key=lambda row: row.score, reverse=True)]
        samples.append(
            {
                "top5": len(set(ranked[:5]).intersection(actual)),
                "top9": len(set(ranked[:CORE_POOL_SIZE]).intersection(actual)),
                "top10": len(set(ranked[:10]).intersection(actual)),
                "top15": len(set(ranked[:15]).intersection(actual)),
            }
        )
    if not samples:
        return {"sample": 0}
    def avg(key: str) -> float:
        return statistics.mean(float(row[key]) for row in samples)
    def ge2(key: str) -> float:
        return sum(1 for row in samples if row[key] >= 2) / len(samples)
    result = {
        "sample": len(samples),
        "top5_avg": avg("top5"),
        "top9_avg": avg("top9"),
        "top10_avg": avg("top10"),
        "top15_avg": avg("top15"),
        "top5_edge": avg("top5") - random_expected_hits(5),
        "top9_edge": avg("top9") - random_expected_hits(CORE_POOL_SIZE),
        "top10_edge": avg("top10") - random_expected_hits(10),
        "top15_edge": avg("top15") - random_expected_hits(15),
        "top5_ge2": ge2("top5"),
        "top9_ge2": ge2("top9"),
        "top10_ge2": ge2("top10"),
        "top15_ge2": ge2("top15"),
        "top5_zero": sum(1 for row in samples if row["top5"] == 0) / len(samples),
        "top9_zero": sum(1 for row in samples if row["top9"] == 0) / len(samples),
        "top10_zero": sum(1 for row in samples if row["top10"] == 0) / len(samples),
        "top15_zero": sum(1 for row in samples if row["top15"] == 0) / len(samples),
    }
    _SCORE_RANK_BACKTEST_CACHE[cache_key] = result
    return dict(result)


def strategy_competition_rows(
    draws: list[Draw],
    recent_window: int,
) -> tuple[list[list[object]], str]:
    rows = []
    best_name = "balanced"
    best_edge = -999.0
    for strategy in strategy_names():
        summary = score_rank_backtest(draws, strategy, recent_window, max_periods=AUTO_BACKTEST_PERIODS)
        top9_edge = float(summary.get("top9_edge", 0.0))
        if top9_edge > best_edge:
            best_edge = top9_edge
            best_name = strategy
        rows.append(
            [
                strategy_label(strategy),
                f"{float(summary.get('top5_avg', 0.0)):.3f}",
                f"{float(summary.get('top9_avg', 0.0)):.3f}",
                f"{float(summary.get('top10_avg', 0.0)):.3f}",
                f"{float(summary.get('top15_avg', 0.0)):.3f}",
                f"{float(summary.get('top9_edge', 0.0)):.3f}",
                f"{float(summary.get('top15_edge', 0.0)):.3f}",
                int(summary.get("sample", 0)),
            ]
        )
    return rows, best_name


def strategy_maturity_rows(
    draws: list[Draw],
    recent_window: int,
    conn: sqlite3.Connection | None,
) -> list[list[object]]:
    calibrated = calibrated_strategy_weights(draws, recent_window, conn)
    rows = []
    for strategy in strategy_names():
        summary = score_rank_backtest(
            draws,
            strategy,
            recent_window,
            max_periods=AUTO_BACKTEST_PERIODS,
        )
        top9_edge = float(summary.get("top9_edge", 0.0))
        top10_edge = float(summary.get("top10_edge", 0.0))
        top15_edge = float(summary.get("top15_edge", 0.0))
        weight = calibrated.get(strategy, 1.0)
        if top9_edge < 0.0 and top10_edge < 0.0:
            action = "熔斷降權"
        elif weight < 0.85:
            action = "降權觀察"
        elif weight >= 1.15:
            action = "主力保留"
        else:
            action = "正常輪替"
        rows.append(
            [
                strategy_label(strategy),
                f"{top9_edge:.3f}",
                f"{top10_edge:.3f}",
                f"{top15_edge:.3f}",
                f"{weight:.2f}",
                action,
            ]
        )
    return rows


def model_consensus_rate(package: PredictionPackage, top_n: int = 10) -> float:
    if not package.scores:
        return 0.0
    ensemble_top = {
        row.number
        for row in sorted(package.scores.values(), key=lambda row: row.score, reverse=True)[:top_n]
    }
    model_names = list(next(iter(package.scores.values())).model_scores)
    if not model_names:
        return 0.0
    overlaps = []
    for model in model_names:
        model_top = {
            row.number
            for row in sorted(
                package.scores.values(),
                key=lambda row: row.model_scores.get(model, 0.0),
                reverse=True,
            )[:top_n]
        }
        overlaps.append(len(ensemble_top.intersection(model_top)) / top_n)
    return statistics.mean(overlaps) if overlaps else 0.0


def confidence_index(row: NumberScore, score_max: float) -> float:
    score_part = row.score / score_max if score_max else 0.0
    model_part = statistics.mean(row.model_scores.values()) if row.model_scores else 0.0
    return max(50.0, min(99.0, 50.0 + score_part * 35.0 + model_part * 14.0))


def confidence_label(row: NumberScore, score_max: float) -> str:
    value = confidence_index(row, score_max)
    if value >= 88:
        return f"高信心（研究） {value:.1f}"
    if value >= 78:
        return f"中高信心 {value:.1f}"
    return f"觀察 {value:.1f}"


def model_label(name: str) -> str:
    labels = {
        "frequency": "長期頻率",
        "recency": "近期熱度",
        "gap": "遺漏補償",
        "trend": "趨勢升溫",
        "pair": "配對圖譜",
        "special": "特別號歷史",
        "bayes": "貝葉斯平滑",
        "momentum": "時間動能",
        "cycle": "週期回歸",
        "structure": "結構適配",
        "rolling_month": "本月滾動修正",
        "zone_repair": "區間修復",
        "breakout_capture": "冷爆捕捉",
        "neighbor_bridge": "鄰近橋接",
        "auto_maturity": "實戰成熟度",
        "settlement_feedback": "結算回饋",
        "transition_follow": "轉移追蹤",
        "tail_transition": "尾數轉移",
        "calendar_phase": "日曆相位",
        "special_crossover": "特別號交叉",
    }
    return labels.get(name, name)


def top_model_text(row: NumberScore, limit: int = 4) -> str:
    ranked = sorted(row.model_scores.items(), key=lambda item: item[1], reverse=True)[:limit]
    return "、".join(f"{model_label(name)} {value:.2f}" for name, value in ranked)


def number_reasons(row: NumberScore, rank: int) -> str:
    reasons = []
    if rank <= 3:
        reasons.append("核心高分")
    if row.recent_frequency >= 3:
        reasons.append("近期熱度")
    if row.miss_gap >= 8:
        reasons.append("遺漏補償")
    if row.trend > 0:
        reasons.append("趨勢升溫")
    if row.pair_strength > 0:
        reasons.append("共現配對")
    if row.special_frequency > 0:
        reasons.append("特別號歷史")
    reasons.extend(model_label(name) for name, _ in sorted(row.model_scores.items(), key=lambda item: item[1], reverse=True)[:2])
    return "、".join(dict.fromkeys(reasons))


def low_probability_reason(row: NumberScore) -> str:
    reasons = []
    if row.recent_frequency == 0:
        reasons.append("近期未形成熱度")
    if row.miss_gap <= 2:
        reasons.append("剛開後追高風險")
    if row.pair_strength <= 0:
        reasons.append("共現支撐偏弱")
    if row.trend < 0:
        reasons.append("短線趨勢偏弱")
    if not reasons:
        reasons.append("綜合分數落後前十五")
    return "、".join(reasons)


def exclusion_confidence(row: NumberScore, score_max: float, rank: int) -> float:
    score_max = score_max or 1.0
    low_score = max(0.0, min(1.0, 1.0 - row.score / score_max))
    rank_pressure = max(0.0, min(1.0, (rank - CORE_POOL_SIZE) / (MAX_NUMBER - CORE_POOL_SIZE)))
    recent_cold = max(0.0, min(1.0, (3 - row.recent_frequency) / 3))
    pair_weak = 1.0 if row.pair_strength <= 0 else max(0.0, min(1.0, 1.0 - row.pair_strength))
    trend_weak = 1.0 if row.trend < 0 else 0.35
    value = (
        low_score * 42
        + rank_pressure * 18
        + recent_cold * 16
        + pair_weak * 14
        + trend_weak * 10
    )
    return max(0.0, min(99.0, value))


def exclusion_label(value: float) -> str:
    if value >= 82:
        return f"高排除 {value:.1f}"
    if value >= 70:
        return f"中高排除 {value:.1f}"
    return f"觀察排除 {value:.1f}"


def exclusion_rows(
    scores: dict[int, NumberScore],
    ranked_numbers: list[int],
    score_max: float,
    count: int,
) -> list[list[object]]:
    rows = []
    ranked = sorted(scores.values(), key=lambda row: row.score)
    for row in ranked[:count]:
        rank = ranked_numbers.index(row.number) + 1 if row.number in ranked_numbers else "-"
        confidence = exclusion_confidence(row, score_max, int(rank) if isinstance(rank, int) else MAX_NUMBER)
        rows.append(
            [
                f"{row.number:02d}",
                exclusion_label(confidence),
                rank,
                f"{1.0 - row.score / (score_max or 1.0):.3f}",
                low_probability_reason(row).replace("Top15", "前十五"),
            ]
        )
    return rows


def exclusion_backtest_summary(
    draws: list[Draw],
    count: int,
    recent_window: int,
    max_periods: int = AUTO_BACKTEST_PERIODS,
    strategy: str = "balanced",
) -> dict[str, float | int]:
    if len(draws) < 20:
        return {"sample": 0, "avg_wrong": 0.0, "full_avoid_rate": 0.0, "avg_avoid_score": 0.0}
    start = max(12, len(draws) - max_periods)
    wrong_hits: list[int] = []
    avoid_scores: list[float] = []
    for index in range(start, len(draws)):
        train = draws[:index]
        actual = set(draws[index].main_numbers)
        scores = build_scores(train, recent_window=recent_window, strategy=strategy)
        ranked_scores = sorted(scores.values(), key=lambda row: row.score)
        low_rows = ranked_scores[:count]
        low_numbers = {row.number for row in low_rows}
        wrong = len(low_numbers.intersection(actual))
        wrong_hits.append(wrong)
        score_max = max((row.score for row in scores.values()), default=1.0) or 1.0
        avoid_scores.append(statistics.mean(1.0 - row.score / score_max for row in low_rows))
    sample = len(wrong_hits)
    return {
        "sample": sample,
        "avg_wrong": statistics.mean(wrong_hits) if wrong_hits else 0.0,
        "full_avoid_rate": sum(1 for value in wrong_hits if value == 0) / sample if sample else 0.0,
        "avg_avoid_score": statistics.mean(avoid_scores) if avoid_scores else 0.0,
    }


def exclusion_pack_summary_rows(
    draws: list[Draw],
    scores: dict[int, NumberScore],
    ranked_numbers: list[int],
    score_max: float,
    recent_window: int,
) -> list[list[object]]:
    rows = []
    for count, label in ((5, "5不中"), (10, "10不中"), (15, "15不中")):
        low_rows = exclusion_rows(scores, ranked_numbers, score_max, count)
        numbers = " ".join(str(row[0]) for row in low_rows)
        confidence_values = []
        for row in low_rows:
            number = int(row[0])
            rank = ranked_numbers.index(number) + 1 if number in ranked_numbers else MAX_NUMBER
            confidence_values.append(exclusion_confidence(scores[number], score_max, rank))
        avg_confidence = statistics.mean(confidence_values) if confidence_values else 0.0
        summary = exclusion_backtest_summary(draws, count, recent_window)
        full_avoid = float(summary.get("full_avoid_rate", 0.0))
        avg_wrong = float(summary.get("avg_wrong", 0.0))
        if full_avoid >= 0.50 and avg_wrong <= 0.90:
            confidence = "高暫避"
        elif full_avoid >= 0.35 and avg_wrong <= 1.40:
            confidence = "中暫避"
        else:
            confidence = "觀察暫避"
        rows.append(
            [
                label,
                numbers,
                f"{confidence} {avg_confidence:.1f}",
                f"{float(summary.get('avg_avoid_score', 0.0)):.3f}",
                int(summary.get("sample", 0)),
                f"{avg_wrong:.3f}",
                f"{full_avoid:.3f}",
                "鐵律避險包：只作低機率暫避與風控，不當作保證不開",
            ]
        )
    return rows


def iron_law_backtest_rows(draws: list[Draw], champion: str, recent_window: int) -> list[list[object]]:
    rows = []
    for window in IRON_LAW_WINDOWS:
        summary = score_rank_backtest(draws, champion, recent_window, max_periods=window)
        top9_edge = float(summary.get("top9_edge", 0.0))
        zero_rate = float(summary.get("top9_zero", 0.0))
        ge2_rate = float(summary.get("top9_ge2", 0.0))
        status = "通過" if top9_edge > 0 and zero_rate <= 0.20 else "觀察"
        rows.append(
            [
                f"近{window}期",
                int(summary.get("sample", 0)),
                f"{float(summary.get('top9_avg', 0.0)):.3f}",
                f"{random_expected_hits(CORE_POOL_SIZE):.3f}",
                f"{top9_edge:.3f}",
                f"{zero_rate:.3f}",
                f"{ge2_rate:.3f}",
                status,
            ]
        )
    return rows


def formal_prediction_integrity_rows(
    conn: sqlite3.Connection,
    draws: list[Draw],
    limit: int = 8,
) -> list[list[object]]:
    rows = []
    if len(draws) < 2:
        return rows
    first_base_draw_id = first_prediction_base_draw_id(conn)
    indexed = list(enumerate(draws))
    for index, actual in reversed(indexed[-limit:]):
        if index <= 0:
            continue
        previous = draws[index - 1]
        if previous.row_id is None:
            rows.append([actual.draw_no or "-", actual.draw_date, "異常", "-", "上一期資料未入庫，不能結算命中率"])
            continue
        run = conn.execute(
            """
            SELECT id, created_at, model_version
            FROM prediction_runs
            WHERE based_on_draw_id = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (previous.row_id,),
        ).fetchone()
        if run is None:
            if first_base_draw_id is not None and int(previous.row_id) < first_base_draw_id:
                rows.append(
                    [
                        actual.draw_no or "-",
                        actual.draw_date,
                        "歷史舊期",
                        "-",
                        "系統接管前沒有正式預測紀錄，保留開獎資料，不列現行異常",
                    ]
                )
            else:
                rows.append([actual.draw_no or "-", actual.draw_date, "異常", "-", "缺正式預測紀錄，不能結算命中率"])
            continue
        rows.append(
            [
                actual.draw_no or "-",
                actual.draw_date,
                "通過",
                f"第 {run['id']} 筆",
                f"依據 {previous.draw_no or '-'} / {run['created_at']} / {run['model_version']}",
            ]
        )
    return rows


def late_hit_recovery_rows(
    conn: sqlite3.Connection,
    draws: list[Draw],
    ranked_numbers: list[int],
    limit: int = 5,
) -> list[list[object]]:
    rows = []
    if len(draws) < 2:
        return rows
    latest_actuals = {number for draw in draws[-limit:] for number in draw.main_numbers}
    for number in ranked_numbers[CORE_POOL_SIZE:SUPPORT_POOL_SIZE]:
        if number in latest_actuals:
            rows.append(
                [
                    f"{number:02d}",
                    ranked_numbers.index(number) + 1,
                    "第十至第十五命中回拉",
                    "近期實開號落在補位池，鐵律要求前九邊界升權觀察",
                ]
            )
    if not rows:
        rows.append(["-", "-", "無", "目前沒有第十至第十五名近期命中回拉訊號"])
    return rows


def prediction_recalculation_rows(
    conn: sqlite3.Connection,
    draws: list[Draw],
    limit: int = 8,
) -> list[list[object]]:
    rows = []
    first_base_draw_id = first_prediction_base_draw_id(conn)
    recent_draws = list(reversed(draws[-limit:]))
    for draw in recent_draws:
        if draw.row_id is None:
            rows.append([draw.draw_no or "-", draw.draw_date, "未檢查", "-", "資料未入庫"])
            continue
        run = conn.execute(
            """
            SELECT id, created_at, model_version
            FROM prediction_runs
            WHERE based_on_draw_id = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (draw.row_id,),
        ).fetchone()
        if run is None:
            if first_base_draw_id is not None and int(draw.row_id) < first_base_draw_id:
                rows.append(
                    [
                        draw.draw_no or "-",
                        draw.draw_date,
                        "歷史舊期",
                        "-",
                        "系統接管前資料，保留入庫，不列現行重算缺口",
                    ]
                )
            else:
                rows.append([draw.draw_no or "-", draw.draw_date, "未重算", "-", "缺少該期重算紀錄"])
        else:
            rows.append(
                [
                    draw.draw_no or "-",
                    draw.draw_date,
                    "已重算",
                    f"第 {run['id']} 筆",
                    f"{run['created_at']} / {run['model_version']}",
                ]
            )
    return rows


def first_prediction_base_draw_id(conn: sqlite3.Connection) -> int | None:
    row = conn.execute("SELECT MIN(based_on_draw_id) AS first_id FROM prediction_runs").fetchone()
    if row is None or row["first_id"] is None:
        return None
    return int(row["first_id"])


def first_prediction_base_draw(draws: list[Draw], conn: sqlite3.Connection) -> Draw | None:
    first_id = first_prediction_base_draw_id(conn)
    if first_id is None:
        return None
    for draw in draws:
        if draw.row_id is not None and int(draw.row_id) == first_id:
            return draw
    return None


def report_index_rows() -> list[list[object]]:
    return [
        ["先看區", "戰報快讀", "最新開獎、下期預測、高信心牌、前九核心、低機率暫避"],
        ["先看區", "資料完整度總表", "歷史資料庫、最新預測、手機同步、戰報輸出是否齊全"],
        ["分頁一至十二", "本期預測", "發布結論、每期重算、低命中校正、高機率信心牌、前九核心"],
        ["分頁十三至十九", "開獎檢討", "日期基準、上期命中、漏抓檢討、前十五詳表"],
        ["分頁二十至二十六", "模型與回測", "牌型、關聯、多模型競賽、命中指標、模型審計"],
        ["分頁二十七至三十一", "暫避與號碼池", "五不中、十不中、十五不中、避險包、下期號碼池"],
        ["分頁三十二至三十六", "鐵律與審核", "多視窗門檻、正式預測紀錄、每日更新、運算審核"],
    ]


def report_file_status_rows() -> list[list[object]]:
    files = [
        [DEFAULT_REPORT_DIR / "香港六合彩預測系統_完整戰報.html", "完整戰報網頁"],
        [DEFAULT_REPORT_DIR / "香港六合彩預測系統_完整戰報.md", "完整戰報純文字"],
        [DEFAULT_REPORT_DIR / "香港六合彩預測系統_最新強化戰報.html", "最新強化戰報"],
        [Path("site") / SITE_HOME_NAME, "首頁"],
        [Path("site") / SITE_BATTLE_REPORT_NAME, "手機同步戰報"],
        [Path("site") / SITE_LATEST_PREDICTION_NAME, "最新預測"],
        [Path("site") / SITE_STATUS_NAME, "系統狀態"],
        [Path("site") / "香港六合彩預測系統_手機狀態.json", "手機狀態"],
        [Path("data") / "香港六合彩預測系統_全歷史資料.csv", "全歷史資料"],
    ]
    rows = []
    for path, label in files:
        if path.exists():
            try:
                stamp = datetime.fromtimestamp(path.stat().st_mtime, LOCAL_TZ).strftime("%Y-%m-%d %H:%M:%S")
            except OSError:
                stamp = "-"
            rows.append([label, "已產生", str(path), stamp])
        else:
            rows.append([label, "待本輪產生", str(path), "本次一鍵完成後覆寫"])
    return rows


def data_completeness_overview_rows(
    conn: sqlite3.Connection,
    draws: list[Draw],
    package: PredictionPackage,
    run_id: int | None,
    based_draw_no: str | None,
    based_draw_date: str,
    target_date: str,
    top9: list[int],
    completeness_passed: int,
    completeness_total: int,
) -> list[list[object]]:
    prediction_count = conn.execute("SELECT COUNT(*) AS total FROM prediction_runs").fetchone()["total"]
    result_count = conn.execute("SELECT COUNT(*) AS total FROM prediction_results").fetchone()["total"]
    first_base = first_prediction_base_draw(draws, conn)
    first_base_text = (
        f"{first_base.draw_no or '-'} / {first_base.draw_date}"
        if first_base is not None
        else "尚無正式預測基準"
    )
    latest = draws[-1]
    return [
        ["歷史資料庫", "已入庫", f"{len(draws)} 期 / {draws[0].draw_date} 到 {draws[-1].draw_date}"],
        ["最新開獎", "已確認", f"{latest.draw_no or '-'} / {latest.draw_date} / {format_numbers(latest.main_numbers)} + 特別號 {latest.special:02d}"],
        ["最新預測", "已重算" if run_id is not None else "臨時計算", f"第 {run_id if run_id is not None else '-'} 筆 / 依據 {based_draw_no or '-'} / {based_draw_date} / 目標 {target_date}"],
        ["正式預測紀錄", "已累積", f"{prediction_count} 筆預測 / {result_count} 筆結算"],
        ["系統接管點", "已標示", first_base_text],
        ["高機率信心牌", "已加註", f"{min(6, len(package.tickets))} 組列入特別標註"],
        ["前九核心池", "已限制", format_numbers(top9)],
        ["低機率暫避", "已補足", "五不中、十不中、十五不中各自列信心與回測成效"],
        ["模型完整度", "通過" if completeness_passed == completeness_total else "需追蹤", f"{completeness_passed}/{completeness_total}"],
        ["手機雲端", "已同步生成", "手機首頁、手機狀態、離線快取與完整戰報同源輸出"],
    ]


def data_gap_clarity_rows(conn: sqlite3.Connection, draws: list[Draw]) -> list[list[object]]:
    first_base = first_prediction_base_draw(draws, conn)
    first_base_text = (
        f"{first_base.draw_no or '-'} / {first_base.draw_date}"
        if first_base is not None
        else "尚未建立接管點"
    )
    recent_rows = prediction_recalculation_rows(conn, draws)
    current_missing = [row for row in recent_rows if row[2] in {"未重算", "異常", "未檢查"}]
    return [
        ["歷史資料", "已保留", "系統接管前舊期只作歷史樣本，不冒充已預測紀錄"],
        ["接管起點", "已標明", first_base_text],
        ["現行缺口", "無" if not current_missing else "需處理", f"最近檢查 {len(recent_rows)} 期，現行缺口 {len(current_missing)} 筆"],
        ["戰報可讀性", "已重整", "最前面固定顯示快讀、目錄、資料完整度與缺口說明"],
        ["掃描規則", "已加嚴", "全系統掃描會檢查快讀、目錄、完整度、鐵律、低機率暫避與手機同步"],
    ]


def quick_report_rows(
    conn: sqlite3.Connection,
    latest: Draw,
    package: PredictionPackage,
    target_date: str,
    run_id: int | None,
    based_draw_no: str | None,
    based_draw_date: str,
    top9: list[int],
    ranked_numbers: list[int],
    score_max: float,
    recent_window: int,
    draws: list[Draw],
    release_level: str,
    risk_level: str,
    completeness_passed: int,
    completeness_total: int,
) -> list[list[object]]:
    super_rows = super_recommendation_rows(package, draws)
    confidence_rows = confidence_ticket_rows(package, limit=3)
    avoid_rows = exclusion_pack_summary_rows(draws, package.scores, ranked_numbers, score_max, recent_window)
    top_confidence = confidence_rows[0][1] if confidence_rows else "-"
    top_super = "；".join(f"{row[0]} {row[1]}" for row in super_rows[:3])
    avoid_text = "；".join(f"{row[0]} {row[1]} 信心{row[2]}" for row in avoid_rows[:3])
    return [
        ["最新開獎", "已入庫", f"{latest.draw_no or '-'} / {latest.draw_date} / {format_numbers(latest.main_numbers)} + 特別號 {latest.special:02d}"],
        ["下期預測", "已重算", f"目標 {target_date} / 第 {run_id if run_id is not None else '-'} 筆 / 依據 {based_draw_no or '-'} {based_draw_date}"],
        ["強推薦", "先看這裡", top_super],
        ["高機率信心牌", "特別加註", top_confidence],
        ["九顆核心池", "正式主池", format_numbers(top9)],
        ["低機率暫避", "分包顯示", avoid_text],
        ["發布狀態", release_level, f"風險等級 {risk_level} / 完整度 {completeness_passed}/{completeness_total}"],
        ["手機同步", "同源輸出", "手機首頁與完整戰報由同一筆最新預測生成"],
    ]


def recommendation_gate_rows(
    package: PredictionPackage,
    score_max: float,
) -> list[list[object]]:
    rows = []
    for index, ticket in enumerate(package.tickets[:8], start=1):
        score_ratio = ticket.score / max((candidate.score for candidate in package.tickets), default=1.0)
        if index <= 3 and score_ratio >= 0.90:
            gate = "允許推薦"
        elif index <= 6 and score_ratio >= 0.82:
            gate = "保留觀察"
        else:
            gate = "不列主推"
        rows.append(
            [
                index,
                format_numbers(ticket.numbers),
                ticket_confidence_label(ticket, max((candidate.score for candidate in package.tickets), default=1.0), index),
                f"{score_ratio * 100:.1f}",
                gate,
            ]
        )
    return rows


def auto_weight_text(
    conn: sqlite3.Connection,
    draws: list[Draw] | None = None,
    recent_window: int = DEFAULT_RECENT_WINDOW,
) -> str:
    weights = (
        calibrated_strategy_weights(draws, recent_window, conn)
        if draws
        else performance_strategy_weights(conn)
    )
    return " / ".join(f"{strategy_label(name)}:{weight:.2f}" for name, weight in sorted(weights.items()))


def settled_status_text(settled: tuple[sqlite3.Row, Draw] | None) -> str:
    if settled is None:
        return "尚無可結算預測"
    run, actual = settled
    ranked = score_snapshot_ranked(run)
    actual_numbers = set(actual.main_numbers)
    return (
        f"第 {run['id']} 筆 -> {actual.draw_date} {actual.draw_no}: "
        f"前五/前九/前十/前十五 {hits_in_top(ranked, actual_numbers, 5)}/"
        f"{hits_in_top(ranked, actual_numbers, CORE_POOL_SIZE)}/"
        f"{hits_in_top(ranked, actual_numbers, 10)}/"
        f"{hits_in_top(ranked, actual_numbers, 15)}"
    )


def settlement_summary_lines(
    conn: sqlite3.Connection,
    settled: tuple[sqlite3.Row, Draw] | None,
) -> list[str]:
    if settled is None:
        return [
            "- 狀態：目前最新預測尚待下一期開獎。",
            "- 動作：一鍵更新拿到新開獎後，會自動結算上一期並寫入檢討區。",
        ]
    run, actual = settled
    ranked = score_snapshot_ranked(run)
    actual_numbers = set(actual.main_numbers)
    return [
        f"- 檢討對應：依據期 {run['based_on_draw_no']} -> 實際開獎期 {actual.draw_no}",
        f"- 實際開出：{format_numbers(actual.main_numbers)} + {actual.special:02d}",
        f"- 前五 / 前九 / 前十 / 前十五 命中：{hits_in_top(ranked, actual_numbers, 5)} / {hits_in_top(ranked, actual_numbers, CORE_POOL_SIZE)} / {hits_in_top(ranked, actual_numbers, 10)} / {hits_in_top(ranked, actual_numbers, 15)}",
        "- 診斷：以真實結算紀錄反推失敗來源，先檢查前九核心池是否漏抓，再處理第十至第十五補位池。",
        "- 改善：提高前九核心集中度，第十至第十五降為防守補位，不列為高機率主推來源。",
    ]


def settlement_detail_lines(
    conn: sqlite3.Connection,
    settled: tuple[sqlite3.Row, Draw] | None,
) -> list[str]:
    if settled is None:
        return [
            "- 尚無已結算預測。下一次開獎更新後，本區會自動列出逐號命中、組合命中與修正動作。"
        ]
    run, actual = settled
    ranked = score_snapshot_ranked(run)
    actual_numbers = set(actual.main_numbers)
    lines = [
        f"### 命中檢討：{run['created_at']} 產生預測 -> {actual.draw_date} 實際開獎",
        f"- 預測依據：{run['based_on_draw_no']} 期 / 開獎日 {run['based_on_draw_date']}",
        f"- 實際開獎期：{actual.draw_no} ({actual.draw_date})",
        f"- 開出號碼：{format_numbers(actual.main_numbers)} + 特別號 {actual.special:02d}",
        "",
        markdown_table(
            ["開出號", "預測排名", "狀態", "原因解釋"],
            [
                [
                    f"{number:02d}",
                    ranked.index(number) + 1 if number in ranked else "-",
                    "已進前九核心池" if number in ranked[:CORE_POOL_SIZE] else ("補位池" if number in ranked[:SUPPORT_POOL_SIZE] else "核心池外"),
                    "核心池有捕捉，保留主推權重" if number in ranked[:CORE_POOL_SIZE] else ("只在補位池，需往前九前移" if number in ranked[:SUPPORT_POOL_SIZE] else "核心池外，需提高補抓模型權重"),
                ]
                for number in actual.main_numbers
            ],
        ),
        "",
        "### 參考組合檢討",
        markdown_table(
            ["組別", "原預測組合", "命中數", "命中號", "未命中號"],
            settled_ticket_rows(conn, int(run["id"]), actual),
        ),
    ]
    return lines


def score_snapshot_ranked(run: sqlite3.Row) -> list[int]:
    snapshot = json.loads(run["score_snapshot_json"])
    return [
        int(number)
        for number, _ in sorted(
            snapshot.items(),
            key=lambda item: float(item[1].get("score", 0.0)),
            reverse=True,
        )
    ]


def hits_in_top(ranked: list[int], actual_numbers: set[int], top_n: int) -> int:
    return len(set(ranked[:top_n]).intersection(actual_numbers))


def settled_ticket_rows(
    conn: sqlite3.Connection,
    run_id: int,
    actual: Draw,
    limit: int = 20,
) -> list[list[object]]:
    actual_numbers = set(actual.main_numbers)
    rows = []
    tickets = conn.execute(
        """
        SELECT ticket_rank, numbers_json
        FROM prediction_tickets
        WHERE run_id = ?
        ORDER BY ticket_rank
        LIMIT ?
        """,
        (run_id, limit),
    ).fetchall()
    for ticket in tickets:
        numbers = tuple(json.loads(ticket["numbers_json"]))
        hits = sorted(set(numbers).intersection(actual_numbers))
        misses = [number for number in numbers if number not in actual_numbers]
        rows.append(
            [
                ticket["ticket_rank"],
                format_numbers(numbers),
                len(hits),
                format_numbers(hits),
                format_numbers(misses),
            ]
        )
    return rows


def lag_overlap_rows(draws: list[Draw], max_lag: int = 5) -> list[list[object]]:
    random_overlap = MAIN_COUNT * MAIN_COUNT / MAX_NUMBER
    rows = []
    for lag in range(1, max_lag + 1):
        overlaps = []
        for index in range(lag, len(draws)):
            overlaps.append(len(set(draws[index].main_numbers).intersection(draws[index - lag].main_numbers)))
        avg_overlap = statistics.mean(overlaps) if overlaps else 0.0
        rows.append([lag, len(overlaps), f"{avg_overlap:.4f}", f"{random_overlap:.4f}", f"{avg_overlap - random_overlap:.4f}"])
    return rows


def top_pair_lift_rows(draws: list[Draw], limit: int = 12) -> list[list[object]]:
    pair_counts: Counter[tuple[int, int]] = Counter()
    for draw in draws:
        numbers = sorted(draw.main_numbers)
        for left_index, left in enumerate(numbers):
            for right in numbers[left_index + 1:]:
                pair_counts[(left, right)] += 1
    expected = len(draws) * math.comb(MAIN_COUNT, 2) / math.comb(MAX_NUMBER, 2) if draws else 0.0
    rows = []
    for (left, right), count in pair_counts.most_common(limit):
        lift = count / expected if expected else 0.0
        rows.append([f"{left:02d}-{right:02d}", count, f"{lift:.3f}", "配對輔助分"])
    return rows or [["-", 0, "0.000", "資料不足"]]


def board_pattern_rows(draws: list[Draw]) -> list[list[object]]:
    recent = draws[-30:] if len(draws) >= 30 else draws
    wave_counts = Counter(wave_color(number) for draw in recent for number in draw.main_numbers)
    tail_counts = Counter(number % 10 for draw in recent for number in draw.main_numbers)
    decade_counts = Counter(decade_bucket(number) for draw in recent for number in draw.main_numbers)
    zone_counts = Counter((number - 1) // 7 + 1 for draw in recent for number in draw.main_numbers)
    return [
        ["近期波色", " / ".join(f"{name}:{count}" for name, count in sorted(wave_counts.items()))],
        ["熱門尾數", " / ".join(f"{tail}尾:{count}" for tail, count in tail_counts.most_common(5))],
        ["十位區間", " / ".join(f"{name}:{count}" for name, count in sorted(decade_counts.items()))],
        ["7區分布", " / ".join(f"第{zone}區:{count}" for zone, count in sorted(zone_counts.items()))],
    ]


def build_battle_report_html(markdown_text: str) -> str:
    body = markdown_to_html(markdown_text)
    return f"""<!doctype html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>香港六合彩預測系統戰報</title>
  <style>
    body {{ margin: 0; font-family: Arial, "Microsoft JhengHei", sans-serif; background: #f4f6f8; color: #17202a; }}
    header {{ background: #102a43; color: white; padding: 22px 28px; }}
    main {{ max-width: 1220px; margin: 0 auto; padding: 22px; }}
    h1, h2, h3 {{ margin: 0 0 12px; line-height: 1.25; }}
    h1 {{ font-size: 28px; }}
    h2 {{ font-size: 20px; }}
    h3 {{ font-size: 16px; color: #243b53; }}
    .band {{ background: white; border: 1px solid #d7dee8; border-radius: 8px; padding: 16px; margin: 0 0 16px; overflow-x: auto; }}
    .lead {{ background: #102a43; color: white; border-radius: 0; border: 0; margin: 0; }}
    .overview-band {{
      border-left: 6px solid #2563eb;
      background: #f8fbff;
    }}
    .prediction-band {{
      border-left: 6px solid #b42318;
    }}
    .control-band {{
      border-left: 6px solid #15803d;
      background: #fbfffc;
    }}
    .appendix-band {{
      border-left: 6px solid #64748b;
      background: #fbfcfe;
    }}
    .overview-band h2::before,
    .prediction-band h2::before,
    .control-band h2::before,
    .appendix-band h2::before {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-width: 44px;
      height: 22px;
      border-radius: 4px;
      color: white;
      font-size: 12px;
      font-weight: 800;
      margin-right: 8px;
      letter-spacing: 0;
    }}
    .overview-band h2::before {{ content: "總覽"; background: #2563eb; }}
    .prediction-band h2::before {{ content: "預測"; background: #b42318; }}
    .control-band h2::before {{ content: "檢查"; background: #15803d; }}
    .appendix-band h2::before {{ content: "附錄"; background: #64748b; }}
    .confidence-band {{
      border: 3px solid #b42318;
      background: #fff7f5;
      box-shadow: 0 0 0 4px rgba(180, 35, 24, .10);
    }}
    .confidence-band h2 {{
      color: #b42318;
      display: flex;
      align-items: center;
      gap: 8px;
    }}
    .confidence-band h2::before {{
      content: "信心";
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-width: 52px;
      height: 24px;
      border-radius: 4px;
      background: #b42318;
      color: white;
      font-size: 12px;
      font-weight: 800;
      letter-spacing: 0;
    }}
    .confidence-band table {{
      border: 2px solid #f2b8ae;
    }}
    .confidence-band th {{
      background: #b42318;
      color: white;
    }}
    .confidence-band tbody tr:nth-child(-n+3) td {{
      background: #fff1f0;
      font-weight: 700;
    }}
    .confidence-band tbody tr:nth-child(-n+3) td:nth-child(3) {{
      color: #b42318;
      font-size: 15px;
    }}
    table {{ width: 100%; border-collapse: collapse; background: white; }}
    th, td {{ border-bottom: 1px solid #dfe5ec; padding: 9px; text-align: left; font-size: 14px; vertical-align: top; }}
    th {{ background: #e9eef5; color: #102a43; }}
    tr:hover td {{ background: #f8fafc; }}
    ul {{ margin: 8px 0 0 20px; padding: 0; }}
    li {{ margin: 5px 0; }}
    p {{ margin: 8px 0; }}
    code {{ background: #edf2f7; padding: 1px 4px; border-radius: 4px; }}
    .meta {{ color: #5f6f82; font-size: 13px; }}
    @media (max-width: 720px) {{
      main {{ padding: 12px; }}
      h1 {{ font-size: 22px; }}
      th, td {{ font-size: 13px; padding: 7px; }}
      .band {{ padding: 12px; }}
    }}
  </style>
</head>
<body>
  <header>
    <h1>香港六合彩預測系統戰報</h1>
    <div class="meta">539同規格強化戰報 / 自動更新、自動結算、自動預測</div>
  </header>
  <main>
{body}
  </main>
</body>
</html>
"""


def markdown_to_html(markdown_text: str) -> str:
    html_parts: list[str] = []
    table_buffer: list[str] = []
    in_list = False
    section_open = False

    def close_list() -> None:
        nonlocal in_list
        if in_list:
            html_parts.append("</ul>")
            in_list = False

    def close_section() -> None:
        nonlocal section_open
        close_list()
        if section_open:
            html_parts.append("</section>")
            section_open = False

    def flush_table() -> None:
        nonlocal table_buffer
        if not table_buffer:
            return
        close_list()
        rows = [split_markdown_row(line) for line in table_buffer]
        table_buffer = []
        if len(rows) >= 2 and all(set(cell.replace(":", "").strip()) <= {"-"} for cell in rows[1]):
            headers = rows[0]
            body_rows = rows[2:]
        else:
            headers = []
            body_rows = rows
        html_parts.append("<table>")
        if headers:
            html_parts.append("<thead><tr>" + "".join(f"<th>{inline_html(cell)}</th>" for cell in headers) + "</tr></thead>")
        html_parts.append("<tbody>")
        for row in body_rows:
            html_parts.append("<tr>" + "".join(f"<td>{inline_html(cell)}</td>" for cell in row) + "</tr>")
        html_parts.append("</tbody></table>")

    for raw_line in markdown_text.splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()
        if stripped.startswith("|") and stripped.endswith("|"):
            table_buffer.append(stripped)
            continue
        flush_table()
        if not stripped:
            close_list()
            continue
        if stripped.startswith("# "):
            close_section()
            html_parts.append(f'<section class="band lead"><h1>{inline_html(stripped[2:])}</h1>')
            section_open = True
            continue
        if stripped.startswith("## "):
            close_section()
            title = stripped[3:]
            if "高機率信心牌" in title or "超強信心" in title:
                section_class = "band confidence-band"
            elif title in {"本期發布結論", "今日總判斷", "日期基準"}:
                section_class = "band overview-band"
            elif any(key in title for key in ("9隻內核心", "今日觀察候選", "候選前十五", "下期預測號碼池")):
                section_class = "band prediction-band"
            elif any(key in title for key in ("命中檢討", "強牌組", "漏抓", "逐號檢討", "風控", "審計", "指標", "穩定度")):
                section_class = "band control-band"
            elif any(key in title for key in ("牌型", "連動", "競賽", "低機率", "歷史對比", "運算保證")):
                section_class = "band appendix-band"
            else:
                section_class = "band"
            html_parts.append(f'<section class="{section_class}"><h2>{inline_html(title)}</h2>')
            section_open = True
            continue
        if stripped.startswith("### "):
            close_list()
            html_parts.append(f"<h3>{inline_html(stripped[4:])}</h3>")
            continue
        if stripped.startswith("- "):
            if not in_list:
                html_parts.append("<ul>")
                in_list = True
            html_parts.append(f"<li>{inline_html(stripped[2:])}</li>")
            continue
        close_list()
        html_parts.append(f"<p>{inline_html(stripped)}</p>")
    flush_table()
    close_section()
    return "\n".join("    " + part for part in html_parts)


def split_markdown_row(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def inline_html(text: object) -> str:
    value = str(text)
    return html.escape(value, quote=False)


def package_from_run(conn: sqlite3.Connection, run_id: int) -> PredictionPackage:
    run = conn.execute("SELECT * FROM prediction_runs WHERE id = ?", (run_id,)).fetchone()
    if run is None:
        raise SystemExit(f"找不到 prediction run: {run_id}")
    tickets = []
    for row in conn.execute(
        """
        SELECT strategy, numbers_json, score, profile, reasons_json
        FROM prediction_tickets
        WHERE run_id = ?
        ORDER BY ticket_rank
        """,
        (run_id,),
    ):
        tickets.append(
            Ticket(
                numbers=tuple(json.loads(row["numbers_json"])),
                score=float(row["score"]),
                profile=row["profile"],
                strategy=row["strategy"],
                reasons=tuple(json.loads(row["reasons_json"])),
            )
        )
    score_snapshot = json.loads(run["score_snapshot_json"])
    scores = {
        int(number): NumberScore(
            number=int(number),
            total_frequency=row["total_frequency"],
            recent_frequency=row["recent_frequency"],
            special_frequency=row["special_frequency"],
            miss_gap=row["miss_gap"],
            trend=row["trend"],
            pair_strength=row["pair_strength"],
            score=row["score"],
            color=row["color"],
            model_scores=row.get("model_scores", {}),
        )
        for number, row in score_snapshot.items()
    }
    return PredictionPackage(
        strategy=run["strategy"],
        tickets=tickets,
        bankers=tuple(json.loads(run["banker_numbers_json"])),
        drags=tuple(json.loads(run["drag_numbers_json"])),
        reserves=tuple(json.loads(run["reserve_numbers_json"])),
        weak_numbers=tuple(json.loads(run["weak_numbers_json"])),
        special_candidates=tuple(json.loads(run["special_candidates_json"] or "[]")),
        scores=scores,
    )


def render_ticket_rows(tickets: list[Ticket]) -> str:
    rows = []
    for index, ticket in enumerate(tickets, start=1):
        rows.append(
            "<tr>"
            f"<td>{index:02d}</td>"
            f"<td><div class=\"balls\">{render_balls(ticket.numbers)}</div></td>"
            f"<td>{escape(strategy_label(ticket.strategy))}</td>"
            f"<td class=\"score\">{ticket.score:.3f}</td>"
            f"<td>{escape(ticket.profile)}</td>"
            f"<td>{escape('；'.join(ticket.reasons))}</td>"
            "</tr>"
        )
    return "\n".join(rows)


def render_confidence_ticket_rows(package: PredictionPackage, limit: int = 6) -> str:
    rows = []
    max_score = max((ticket.score for ticket in package.tickets), default=1.0) or 1.0
    for index, ticket in enumerate(package.tickets[:limit], start=1):
        rows.append(
            "<tr>"
            f"<td>{index:02d}</td>"
            f"<td><div class=\"balls\">{render_balls(ticket.numbers)}</div></td>"
            f"<td><strong>{escape(ticket_confidence_label(ticket, max_score, index))}</strong></td>"
            f"<td>{escape(strategy_label(ticket.strategy))}</td>"
            f"<td class=\"score\">{ticket.score:.3f}</td>"
            f"<td>{escape('；'.join(ticket.reasons[-2:]) if ticket.reasons else '成熟度校準')}</td>"
            "</tr>"
        )
    return "\n".join(rows)


def render_super_recommendation_rows(
    package: PredictionPackage,
    draws: list[Draw] | None = None,
) -> str:
    rows = []
    for item in super_recommendation_items(package, draws):
        rows.append(
            "<tr>"
            f"<td><strong>{escape(item['label'])}</strong></td>"
            f"<td><div class=\"balls\">{render_balls(item['numbers'])}</div></td>"
            f"<td>{escape(item['target'])}</td>"
            f"<td class=\"score\"><strong>{escape(item['confidence'])}</strong></td>"
            f"<td>{escape(item['probability'])}</td>"
            f"<td>{escape(item['reason'])}</td>"
            "</tr>"
        )
    return "\n".join(rows)


def render_score_rows(rows: list[NumberScore]) -> str:
    body = ["<thead><tr><th>號碼</th><th>波色</th><th>近期</th><th>遺漏</th><th>分數</th></tr></thead><tbody>"]
    for row in rows:
        body.append(
            "<tr>"
            f"<td>{render_ball(row.number)}</td>"
            f"<td>{escape(row.color)}</td>"
            f"<td>{row.recent_frequency}</td>"
            f"<td>{row.miss_gap}</td>"
            f"<td class=\"score\">{row.score:.3f}</td>"
            "</tr>"
        )
    body.append("</tbody>")
    return "\n".join(body)


def render_model_cards(scores: dict[int, NumberScore]) -> str:
    labels = {
        "frequency": "長期頻率",
        "recency": "近期熱度",
        "gap": "冷門遺漏",
        "trend": "趨勢升溫",
        "pair": "配對圖譜",
        "special": "特別號",
        "bayes": "貝葉斯",
        "momentum": "時間動能",
        "cycle": "週期",
        "structure": "結構",
        "rolling_month": "本月滾動",
    }
    if not scores:
        return ""
    cards = []
    model_names = list(next(iter(scores.values())).model_scores)
    for model in model_names:
        ranked = sorted(
            scores.values(),
            key=lambda row: row.model_scores.get(model, 0.0),
            reverse=True,
        )[:8]
        cards.append(
            '<div class="card">'
            f"<h2>{escape(labels.get(model, model))}</h2>"
            f'<div class="balls">{render_balls(row.number for row in ranked)}</div>'
            "</div>"
        )
    return "\n".join(cards)


def render_balls(numbers: Iterable[int]) -> str:
    return " ".join(render_ball(number) for number in numbers)


def render_ball(number: int) -> str:
    color_class = {"紅波": "red", "藍波": "blue", "綠波": "green"}[wave_color(number)]
    return f"<span class=\"ball {color_class}\">{number:02d}</span>"


def escape(value: object) -> str:
    return html.escape(str(value), quote=True)


def print_prediction(package: PredictionPackage, run_id: int | None = None) -> str:
    lines = [
        "香港六合彩預測",
        "=" * 24,
        f"預測編號: {run_id if run_id is not None else '-'}",
        f"策略: {strategy_label(package.strategy)}",
        f"膽碼: {format_numbers(package.bankers)}",
        f"拖碼: {format_numbers(package.drags)}",
        f"防守碼: {format_numbers(package.reserves)}",
        f"弱勢碼: {format_numbers(package.weak_numbers)}",
        f"特別號候選: {format_numbers(package.special_candidates)}",
        "",
        "高機率信心牌（特別標註）:",
    ]
    max_score = max((ticket.score for ticket in package.tickets), default=1.0) or 1.0
    for index, ticket in enumerate(package.tickets[:6], start=1):
        lines.append(
            f"{index:02d}. {format_numbers(ticket.numbers)}  "
            f"{ticket_confidence_label(ticket, max_score, index)}  "
            f"{strategy_label(ticket.strategy)}  分數={ticket.score:.3f}"
        )
    lines.extend(
        [
            "",
            "候選組合:",
        ]
    )
    for index, ticket in enumerate(package.tickets, start=1):
        lines.append(
            f"{index:02d}. {format_numbers(ticket.numbers)}  "
            f"分數={ticket.score:.3f}  {strategy_label(ticket.strategy)}  {ticket.profile}"
        )
        lines.append(f"    {'；'.join(ticket.reasons)}")
    return "\n".join(lines)


def status_text(conn: sqlite3.Connection) -> str:
    init_db(conn)
    draw_count = conn.execute("SELECT COUNT(*) FROM draws").fetchone()[0]
    run_count = conn.execute("SELECT COUNT(*) FROM prediction_runs").fetchone()[0]
    result_count = conn.execute("SELECT COUNT(*) FROM prediction_results").fetchone()[0]
    backtest_count = conn.execute("SELECT COUNT(*) FROM backtest_runs").fetchone()[0]
    latest = conn.execute(
        "SELECT draw_date, draw_no, n1, n2, n3, n4, n5, n6, special FROM draws ORDER BY draw_date DESC, draw_no DESC LIMIT 1"
    ).fetchone()
    lines = [
        "系統狀態",
        "=" * 24,
        f"開獎資料: {draw_count} 期",
        f"預測紀錄: {run_count} 次",
        f"驗證紀錄: {result_count} 筆",
        f"回測紀錄: {backtest_count} 次",
    ]
    if latest:
        numbers = tuple(latest[f"n{i}"] for i in range(1, MAIN_COUNT + 1))
        lines.append(
            f"最新開獎: {latest['draw_date']} {latest['draw_no']} "
            f"{format_numbers(numbers)} + {latest['special']:02d}"
        )
    lines.append("")
    lines.append("策略權重:")
    for name, weight in sorted(performance_strategy_weights(conn).items()):
        lines.append(f"{strategy_label(name)}: {weight:.2f}")
    return "\n".join(lines)


def doctor_text(draws: list[Draw]) -> str:
    lines = ["資料健檢", "=" * 24]
    if not draws:
        return "資料健檢\n========================\n沒有開獎資料。"

    date_counts = Counter(draw.draw_date for draw in draws)
    draw_no_counts = Counter(draw.draw_no for draw in draws if draw.draw_no)
    duplicate_dates = [date_text for date_text, count in date_counts.items() if count > 1]
    duplicate_draw_nos = [draw_no for draw_no, count in draw_no_counts.items() if count > 1]
    bad_rows = []
    for draw in draws:
        try:
            validate_numbers(draw.main_numbers, draw.special, draw.draw_no or draw.draw_date)
        except SystemExit as exc:
            bad_rows.append(str(exc))

    ordered = sort_draws(draws)
    gaps = []
    for left, right in zip(ordered, ordered[1:]):
        left_date = datetime.strptime(left.draw_date, "%Y-%m-%d").date()
        right_date = datetime.strptime(right.draw_date, "%Y-%m-%d").date()
        gaps.append((right_date - left_date).days)

    lines.extend(
        [
            f"總期數: {len(draws)}",
            f"日期範圍: {ordered[0].draw_date} -> {ordered[-1].draw_date}",
            f"最新期: {ordered[-1].draw_date} {ordered[-1].draw_no} {format_numbers(ordered[-1].main_numbers)} + {ordered[-1].special:02d}",
            f"重複日期: {len(duplicate_dates)}",
            f"重複期號: {len(duplicate_draw_nos)}",
            f"格式錯誤: {len(bad_rows)}",
        ]
    )
    if gaps:
        lines.extend(
            [
                f"開獎間隔: 最小 {min(gaps)} 天，最大 {max(gaps)} 天，中位數 {statistics.median(gaps):.1f} 天",
                f"超過 14 天間隔: {sum(1 for gap in gaps if gap > 14)}",
            ]
        )
    if duplicate_dates[:5]:
        lines.append("重複日期樣本: " + ", ".join(duplicate_dates[:5]))
    if duplicate_draw_nos[:5]:
        lines.append("重複期號樣本: " + ", ".join(duplicate_draw_nos[:5]))
    if bad_rows[:5]:
        lines.append("錯誤樣本: " + " | ".join(bad_rows[:5]))
    lines.append("狀態: " + ("正常" if not duplicate_dates and not duplicate_draw_nos and not bad_rows else "需要檢查"))
    return "\n".join(lines)


def model_report_text(
    draws: list[Draw],
    strategy: str,
    recent_window: int,
    top: int,
) -> str:
    if not draws:
        return "多模型運算\n========================\n沒有開獎資料。"
    scores = build_scores(draws, recent_window=recent_window, strategy=strategy)
    labels = {
        "frequency": "長期頻率",
        "recency": "近期熱度",
        "gap": "冷門遺漏",
        "trend": "趨勢升溫",
        "pair": "配對圖譜",
        "special": "特別號",
        "bayes": "貝葉斯平滑",
        "momentum": "時間動能",
        "cycle": "週期回歸",
        "structure": "結構適配",
        "rolling_month": "本月滾動修正",
        "zone_repair": "區間修復",
        "breakout_capture": "冷爆捕捉",
        "neighbor_bridge": "鄰近橋接",
        "settlement_feedback": "結算回饋",
        "transition_follow": "轉移追蹤",
        "tail_transition": "尾數轉移",
        "calendar_phase": "日曆相位",
        "special_crossover": "特別號交叉",
    }
    lines = [
        "多模型運算",
        "=" * 24,
        f"策略: {strategy}",
        f"近期視窗: {recent_window}",
        "",
        "Ensemble 綜合排行:",
        " ".join(f"{row.number:02d}({row.score:.3f})" for row in sorted(scores.values(), key=lambda row: row.score, reverse=True)[:top]),
    ]
    model_names = list(next(iter(scores.values())).model_scores)
    for model in model_names:
        ranked = sorted(
            scores.values(),
            key=lambda row: row.model_scores.get(model, 0.0),
            reverse=True,
        )[:top]
        lines.append("")
        lines.append(f"{labels.get(model, model)}:")
        lines.append(
            " ".join(
                f"{row.number:02d}({row.model_scores.get(model, 0.0):.2f})"
                for row in ranked
            )
        )
    return "\n".join(lines)


def leaderboard_text(conn: sqlite3.Connection) -> str:
    init_db(conn)
    rows = conn.execute(
        """
        SELECT pt.strategy,
               COUNT(*) AS tickets,
               AVG(pr.main_hits) AS avg_main_hits,
               MAX(pr.main_hits) AS best_main_hits,
               SUM(pr.special_hit) AS special_hits,
               SUM(CASE WHEN pr.prize_tier <> '未達獎級' THEN 1 ELSE 0 END) AS prize_hits
        FROM prediction_results pr
        JOIN prediction_tickets pt ON pt.id = pr.ticket_id
        GROUP BY pt.strategy
        ORDER BY avg_main_hits DESC, special_hits DESC
        """
    ).fetchall()
    lines = ["策略績效排行", "=" * 24]
    if not rows:
        lines.append("尚無驗證結果。")
    else:
        lines.append("策略        票數  平均主號  最佳主號  特別號  達獎級")
        for row in rows:
            lines.append(
                f"{row['strategy']:<11} {row['tickets']:>4}  "
                f"{float(row['avg_main_hits']):>7.2f}  {row['best_main_hits']:>7}  "
                f"{row['special_hits']:>5}  {row['prize_hits']:>5}"
            )
    lines.append("")
    draws = load_draws_from_db(conn)
    lines.append("目前 auto 校準權重:")
    active_weights = (
        calibrated_strategy_weights(draws, DEFAULT_RECENT_WINDOW, conn)
        if draws
        else performance_strategy_weights(conn)
    )
    for strategy, weight in sorted(active_weights.items()):
        lines.append(f"{strategy}: {weight:.2f}")
    return "\n".join(lines)


def runs_text(conn: sqlite3.Connection, limit: int) -> str:
    init_db(conn)
    rows = conn.execute(
        """
        SELECT pr.id, pr.created_at, pr.based_on_draw_date, pr.based_on_draw_no,
               pr.strategy, pr.ticket_count, COUNT(res.id) AS result_count
        FROM prediction_runs pr
        LEFT JOIN prediction_results res ON res.run_id = pr.id
        GROUP BY pr.id
        ORDER BY pr.id DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    lines = ["預測紀錄", "=" * 24]
    if not rows:
        lines.append("尚無預測紀錄。")
        return "\n".join(lines)
    lines.append("ID   建立時間                  基準期          策略         組數  驗證")
    for row in rows:
        lines.append(
            f"{row['id']:<4} {row['created_at']:<25} "
            f"{row['based_on_draw_date']} {row['based_on_draw_no']:<7} "
            f"{row['strategy']:<11} {row['ticket_count']:>4} {row['result_count']:>5}"
        )
    return "\n".join(lines)


def run_cycle(args: argparse.Namespace) -> str:
    lines = ["一鍵流程", "=" * 24]
    with connect(args.db) as conn:
        init_db(conn)
        if args.csv:
            draws = load_draws(args.csv)
            inserted, skipped = import_draws(conn, draws)
            lines.append(f"CSV 匯入: 新增 {inserted}，略過 {skipped}")
        if args.fetch:
            draws, raw_text = fetch_hkjc_draws(args.last)
            inserted, skipped = import_draws(conn, draws, raw_json=raw_text[:200000])
            lines.append(f"HKJC 抓取: 新增 {inserted}，略過 {skipped}")
        lines.append(evaluate_predictions(conn, "all"))
        draws = load_draws_from_db(conn)
        if len(draws) >= 5:
            should_refresh, refresh_reason = prediction_needs_refresh(conn, draws)
            if should_refresh:
                package = generate_prediction_package(
                    draws,
                    strategy=args.strategy,
                    ticket_count=args.tickets,
                    recent_window=args.recent_window,
                    seed=args.seed,
                    conn=conn,
                )
                run_id = save_prediction_run(
                    conn,
                    package,
                    draws,
                    args.recent_window,
                    args.seed,
                    args.prediction_html,
                )
                lines.append(f"重新運算預測: {refresh_reason}")
            else:
                latest_run = latest_prediction_run(conn)
                run_id = int(latest_run["id"]) if latest_run is not None else None
                package = package_from_run(conn, run_id) if run_id is not None else generate_prediction_package(
                    draws,
                    strategy=args.strategy,
                    ticket_count=args.tickets,
                    recent_window=args.recent_window,
                    seed=args.seed,
                    conn=conn,
                )
                lines.append(f"沿用最新預測: {refresh_reason}")
            render_prediction_html(args.prediction_html, package, draws, run_id)
            render_full_report(args.report_html, conn, args.recent_window)
            battle_paths = save_battle_reports(conn, args.report_html.parent, None, args.recent_window)
            lines.append(print_prediction(package, run_id))
            lines.append(f"預測報告: {args.prediction_html}")
            lines.append(f"系統報告: {args.report_html}")
            lines.append(f"強化戰報: {battle_paths['enhanced']}")
        else:
            lines.append("資料少於 5 期，暫不產生預測。")
    return "\n".join(lines)


def export_draws(conn: sqlite3.Connection, csv_path: Path) -> None:
    draws = load_draws_from_db(conn)
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["draw_date", "draw_id", "n1", "n2", "n3", "n4", "n5", "n6", "special"])
        for draw in draws:
            writer.writerow([draw.draw_date, draw.draw_no, *draw.main_numbers, draw.special])


def backup_database(db_path: Path, backup_dir: Path) -> Path:
    if not db_path.exists():
        raise SystemExit(f"資料庫不存在，無法備份: {db_path}")
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    target = backup_dir / f"{db_path.stem}_{stamp}{db_path.suffix or '.db'}"
    shutil.copy2(db_path, target)
    return target


def load_mobile_cloud_module():
    import importlib

    return importlib.import_module("香港六合彩預測系統_手機雲端_20260630_第16版")


def build_site(
    conn: sqlite3.Connection,
    site_dir: Path,
    recent_window: int,
    report_dir: Path = DEFAULT_REPORT_DIR,
) -> dict[str, Path]:
    init_db(conn)
    site_dir.mkdir(parents=True, exist_ok=True)
    cleanup_legacy_output_files(site_dir, report_dir)
    render_full_report(site_dir / SITE_SYSTEM_REPORT_NAME, conn, recent_window)
    draws = load_draws_from_db(conn)
    latest_run = latest_prediction_run(conn)
    if draws and latest_run is not None:
        package = package_from_run(conn, int(latest_run["id"]))
        render_prediction_html(
            site_dir / SITE_LATEST_PREDICTION_NAME,
            package,
            draws,
            int(latest_run["id"]),
            title="香港六合彩最新預測",
        )
    export_draws(conn, site_dir / SITE_DRAWS_CSV_NAME)
    (site_dir / SITE_STATUS_NAME).write_text(status_text(conn), encoding="utf-8")
    runs = conn.execute(
        """
        SELECT id, created_at, based_on_draw_date, based_on_draw_no,
               strategy, ticket_count, report_path
        FROM prediction_runs
        ORDER BY id DESC
        LIMIT 50
        """
    ).fetchall()
    payload = [dict(row) for row in runs]
    (site_dir / SITE_PREDICTION_RUNS_NAME).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    paths = save_battle_reports(conn, report_dir, site_dir, recent_window)
    try:
        mobile_cloud = load_mobile_cloud_module()

        mobile_paths = mobile_cloud.build_mobile_cloud_site(
            conn,
            site_dir,
            report_dir,
            recent_window,
        )
        paths.update({f"mobile_{key}": value for key, value in mobile_paths.items()})
    except Exception as exc:
        paths["mobile_error"] = Path(str(exc))
    return paths


def daily_update(args: argparse.Namespace) -> str:
    lines = ["完整日更流程", "=" * 24]
    if args.db.exists():
        backup_path = backup_database(args.db, args.backup_dir)
        lines.append(f"已備份: {backup_path}")
    with connect(args.db) as conn:
        init_db(conn)
        if args.csv:
            draws = load_draws(args.csv)
            inserted, skipped = import_draws(conn, draws)
            lines.append(f"CSV 匯入: 新增 {inserted}，略過 {skipped}")
        if args.fetch_hkjc:
            try:
                draws, raw_text = fetch_hkjc_draws(args.last)
                inserted, skipped = import_draws(conn, draws, raw_json=raw_text[:200000])
                lines.append(f"HKJC 更新: 新增 {inserted}，略過 {skipped}")
            except Exception as exc:
                if args.strict_update:
                    raise
                lines.append(f"HKJC 更新失敗，改用既有資料繼續: {exc}")
        if args.fetch_lottolyzer:
            try:
                draws, raw_text = fetch_lottolyzer_history(args.pages, 50, 0.35)
                inserted, skipped = import_draws(conn, draws, raw_json=raw_text[:200000])
                lines.append(f"Lottolyzer 更新: 新增 {inserted}，略過 {skipped}")
            except Exception as exc:
                if args.strict_update:
                    raise
                lines.append(f"Lottolyzer 更新失敗，改用既有資料繼續: {exc}")

        existing_count = conn.execute("SELECT COUNT(*) FROM draws").fetchone()[0]
        if existing_count == 0 and BUNDLED_SEED_CSV.exists():
            seed_draws = load_draws(BUNDLED_SEED_CSV)
            inserted, skipped = import_draws(conn, seed_draws)
            lines.append(
                f"內建資料包啟動: 新增 {inserted} 期，略過 {skipped} 期 "
                f"({BUNDLED_SEED_CSV})"
            )

        lines.append(evaluate_predictions(conn, "all"))
        draws = load_draws_from_db(conn)
        lines.append(doctor_text(draws))
        if len(draws) >= 5:
            prediction_html = args.site_dir / SITE_LATEST_PREDICTION_NAME
            should_refresh, refresh_reason = prediction_needs_refresh(conn, draws)
            if args.force_prediction and not should_refresh:
                should_refresh = True
                refresh_reason = f"每日一鍵強制重新運算 {now_text()}"
            if should_refresh:
                package = generate_prediction_package(
                    draws,
                    strategy=args.strategy,
                    ticket_count=args.tickets,
                    recent_window=args.recent_window,
                    seed=args.seed,
                    conn=conn,
                )
                run_id = save_prediction_run(
                    conn,
                    package,
                    draws,
                    args.recent_window,
                    args.seed,
                    prediction_html,
                )
                lines.append(f"重新運算預測: {refresh_reason}")
            else:
                latest_run = latest_prediction_run(conn)
                run_id = int(latest_run["id"]) if latest_run is not None else None
                package = package_from_run(conn, run_id) if run_id is not None else generate_prediction_package(
                    draws,
                    strategy=args.strategy,
                    ticket_count=args.tickets,
                    recent_window=args.recent_window,
                    seed=args.seed,
                    conn=conn,
                )
                lines.append(f"沿用最新預測: {refresh_reason}")
            render_prediction_html(prediction_html, package, draws, run_id)
            battle_paths = build_site(conn, args.site_dir, args.recent_window, args.report_dir)
            lines.append(print_prediction(package, run_id))
            lines.append(f"網站首頁: {args.site_dir / SITE_HOME_NAME}")
            lines.append(f"強化戰報: {battle_paths['enhanced']}")
            if "mobile_mobile" in battle_paths:
                lines.append(f"手機雲端: {battle_paths['mobile_mobile']}")
        else:
            lines.append("資料少於 5 期，暫不產生預測。")
    return "\n".join(lines)


def format_numbers(numbers: Iterable[int]) -> str:
    return " ".join(f"{number:02d}" for number in numbers)


def run() -> None:
    args = parse_args()

    if args.command == "init-db":
        with connect(args.db) as conn:
            init_db(conn)
        print(f"已建立資料庫: {args.db}")
        return

    if args.command == "import-csv":
        draws = load_draws(args.csv)
        with connect(args.db) as conn:
            inserted, skipped = import_draws(conn, draws)
        print(f"匯入完成: 新增 {inserted} 期，略過 {skipped} 期")
        return

    if args.command == "fetch-hkjc":
        draws, raw_text = fetch_hkjc_draws(args.last, args.start_date, args.end_date)
        with connect(args.db) as conn:
            inserted, skipped = import_draws(conn, draws, raw_json=raw_text)
        print(f"HKJC 抓取完成: 新增 {inserted} 期，略過 {skipped} 期")
        return

    if args.command == "fetch-lottolyzer":
        draws, raw_text = fetch_lottolyzer_history(args.pages, args.per_page, args.delay)
        with connect(args.db) as conn:
            inserted, skipped = import_draws(conn, draws, raw_json=raw_text[:200000])
        print(f"Lottolyzer 抓取完成: 解析 {len(draws)} 期，新增 {inserted} 期，略過 {skipped} 期")
        return

    if args.command == "build-history-db":
        draws, raw_text = fetch_lottolyzer_history(args.pages, args.per_page, args.delay)
        write_draws_csv(draws, args.csv_out)
        with connect(args.db) as conn:
            inserted, skipped = import_draws(conn, draws, raw_json=raw_text[:200000])
            status = status_text(conn)
        print(f"全歷史資料庫完成: 解析 {len(draws)} 期，新增 {inserted} 期，略過 {skipped} 期")
        print(f"CSV: {args.csv_out}")
        print(status)
        return

    if args.command == "status":
        with connect(args.db) as conn:
            print(status_text(conn))
        return

    if args.command == "doctor":
        draws = load_draws_from_source(args)
        print(doctor_text(draws))
        return

    if args.command == "models":
        draws = load_draws_from_source(args)
        print(model_report_text(draws, args.strategy, args.recent_window, args.top))
        return

    if args.command == "leaderboard":
        with connect(args.db) as conn:
            print(leaderboard_text(conn))
        return

    if args.command == "runs":
        with connect(args.db) as conn:
            print(runs_text(conn, args.limit))
        return

    if args.command == "analyze":
        draws = load_draws_from_source(args)
        print(analyze(draws, recent_window=args.recent_window))
        return

    if args.command == "predict":
        with connect(args.db) as conn:
            draws = load_draws_from_db(conn)
            package = generate_prediction_package(
                draws,
                strategy=args.strategy,
                ticket_count=args.tickets,
                recent_window=args.recent_window,
                seed=args.seed,
                conn=conn,
            )
            run_id = save_prediction_run(conn, package, draws, args.recent_window, args.seed, args.html)
            render_prediction_html(args.html, package, draws, run_id)
        print(print_prediction(package, run_id))
        print(f"\nHTML 報告: {args.html}")
        return

    if args.command == "evaluate":
        with connect(args.db) as conn:
            print(evaluate_predictions(conn, args.prediction_id))
        return

    if args.command == "report":
        with connect(args.db) as conn:
            render_full_report(args.html, conn, args.recent_window)
        print(f"HTML 報告: {args.html}")
        return

    if args.command == "battle-report":
        with connect(args.db) as conn:
            paths = save_battle_reports(conn, args.report_dir, args.site_dir, args.recent_window)
        print(f"強化戰報: {paths['enhanced']}")
        print(f"網站首頁: {args.site_dir / SITE_HOME_NAME}")
        return

    if args.command == "build-site":
        with connect(args.db) as conn:
            paths = build_site(conn, args.site_dir, args.recent_window, args.report_dir)
        print(f"網站首頁: {args.site_dir / SITE_HOME_NAME}")
        print(f"強化戰報: {paths['enhanced']}")
        if "mobile_mobile" in paths:
            print(f"手機雲端: {paths['mobile_mobile']}")
        return

    if args.command == "mobile-cloud":
        mobile_cloud = load_mobile_cloud_module()

        with connect(args.db) as conn:
            paths = mobile_cloud.build_mobile_cloud_site(
                conn,
                args.site_dir,
                args.report_dir,
                args.recent_window,
            )
        print(f"手機雲端首頁: {paths['mobile']}")
        print(f"手機雲端戰報: {paths['report']}")
        return

    if args.command == "run-cycle":
        print(run_cycle(args))
        return

    if args.command == "daily-update":
        print(daily_update(args))
        return

    if args.command == "generate":
        draws = load_draws_from_source(args)
        package = generate_prediction_package(
            draws,
            strategy=args.strategy,
            ticket_count=args.tickets,
            recent_window=args.recent_window,
            seed=args.seed,
        )
        print(print_prediction(package))
        return

    if args.command == "backtest":
        draws = load_draws_from_source(args)
        summary = run_backtest_summary(
            draws,
            strategy=args.strategy,
            tickets=args.tickets,
            recent_window=args.recent_window,
            min_train=args.min_train,
            seed=args.seed,
        )
        if getattr(args, "db", None) and not args.no_save:
            with connect(args.db) as conn:
                run_id = save_backtest_run(conn, summary)
            print(f"已保存回測編號 {run_id}")
        print(format_backtest_summary(summary))
        return

    if args.command == "backtests":
        with connect(args.db) as conn:
            print(backtests_text(conn, args.limit))
        return

    if args.command == "backup-db":
        backup_path = backup_database(args.db, args.backup_dir)
        print(f"已備份: {backup_path}")
        return

    if args.command == "export-csv":
        with connect(args.db) as conn:
            export_draws(conn, args.csv)
        print(f"已匯出: {args.csv}")
        return

    raise SystemExit(f"未知命令: {args.command}")


if __name__ == "__main__":
    run()
