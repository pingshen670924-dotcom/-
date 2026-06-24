from __future__ import annotations

import argparse
import html
import json
from pathlib import Path

import importlib
m = importlib.import_module("香港六合彩預測系統_20260624_第8版")


MOBILE_HTML = "香港六合彩預測系統_手機首頁.html"
MOBILE_REPORT = "香港六合彩預測系統_手機雲端.html"
MOBILE_STATUS = "香港六合彩預測系統_手機狀態.json"
MOBILE_MANIFEST = "香港六合彩預測系統_手機設定.json"
MOBILE_SERVICE_WORKER = "香港六合彩預測系統_離線快取.js"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="香港六合彩預測系統")
    parser.add_argument("--db", type=Path, default=Path("香港六合彩預測系統.db"))
    parser.add_argument("--site-dir", type=Path, default=Path("site"))
    parser.add_argument("--report-dir", type=Path, default=Path("reports"))
    parser.add_argument("--recent-window", type=int, default=m.DEFAULT_RECENT_WINDOW)
    return parser.parse_args()


def build_mobile_cloud_site(
    conn,
    site_dir: Path,
    report_dir: Path,
    recent_window: int = m.DEFAULT_RECENT_WINDOW,
) -> dict[str, Path]:
    site_dir.mkdir(parents=True, exist_ok=True)
    report_dir.mkdir(parents=True, exist_ok=True)
    payload = build_payload(conn, recent_window)
    site_html = render_mobile_html(payload, asset_prefix="./", pwa=True)
    report_html = render_mobile_html(payload, asset_prefix="../site/", pwa=False)
    paths = {
        "mobile": site_dir / MOBILE_HTML,
        "status": site_dir / MOBILE_STATUS,
        "manifest": site_dir / MOBILE_MANIFEST,
        "service_worker": site_dir / MOBILE_SERVICE_WORKER,
        "report": report_dir / MOBILE_REPORT,
    }
    paths["mobile"].write_text(site_html, encoding="utf-8")
    paths["status"].write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    paths["manifest"].write_text(json.dumps(manifest(), ensure_ascii=False, indent=2), encoding="utf-8")
    paths["service_worker"].write_text(service_worker(), encoding="utf-8")
    paths["report"].write_text(report_html, encoding="utf-8")
    return paths


def build_payload(conn, recent_window: int) -> dict:
    m.init_db(conn)
    draws = m.load_draws_from_db(conn)
    if not draws:
        raise SystemExit("資料庫沒有開獎資料。")

    latest = draws[-1]
    latest_run = m.latest_prediction_run(conn)
    if latest_run is None:
        run_id = None
        package = m.generate_prediction_package(draws, "auto", 20, recent_window, None, conn)
        based_draw_no = latest.draw_no
        based_draw_date = latest.draw_date
        created_at = m.now_text()
    else:
        run_id = int(latest_run["id"])
        package = m.package_from_run(conn, run_id)
        based_draw_no = latest_run["based_on_draw_no"]
        based_draw_date = latest_run["based_on_draw_date"]
        created_at = latest_run["created_at"]

    ranked_scores = sorted(package.scores.values(), key=lambda row: row.score, reverse=True)
    ranked_numbers = [row.number for row in ranked_scores]
    max_ticket_score = max((ticket.score for ticket in package.tickets), default=1.0) or 1.0
    max_number_score = max((row.score for row in ranked_scores), default=1.0) or 1.0

    rank_backtest = m.score_rank_backtest(
        draws,
        "balanced",
        recent_window,
        max_periods=m.AUTO_BACKTEST_PERIODS,
    )
    completeness_passed, completeness_total, _ = m.system_completeness_rows(draws, package, conn)
    top9_edge = float(rank_backtest.get("top9_edge", 0.0))
    risk_level = "高" if len(draws) < 300 or top9_edge < 0.1 else "中"
    release_level = "研究觀察，不列保證" if top9_edge < 0.25 else "研究級高關注"
    settled = m.latest_settled_prediction(conn)

    return {
        "generated_at": m.now_text(),
        "model_version": m.MODEL_VERSION,
        "draw_count": len(draws),
        "date_range": f"{draws[0].draw_date} -> {draws[-1].draw_date}",
        "latest": {
            "draw_no": latest.draw_no,
            "date": latest.draw_date,
            "main_numbers": list(latest.main_numbers),
            "special": latest.special,
        },
        "prediction": {
            "run_id": run_id,
            "created_at": created_at,
            "based_on_draw_no": based_draw_no,
            "based_on_draw_date": based_draw_date,
            "target_date": m.next_marksix_draw_date(based_draw_date),
            "strategy": m.strategy_label(package.strategy),
            "ticket_count": len(package.tickets),
        },
        "system": {
            "status": "正常",
            "risk_level": risk_level,
            "release_level": release_level,
            "completeness": f"{completeness_passed}/{completeness_total}",
            "champion": "均衡",
            "auto_weights": m.auto_weight_text(conn, draws, recent_window),
            "settled_status": m.settled_status_text(settled),
            "consensus": round(m.model_consensus_rate(package), 3),
        },
        "accuracy": {
            "sample": int(rank_backtest.get("sample", 0)),
            "top5_avg": round(float(rank_backtest.get("top5_avg", 0.0)), 3),
            "top5_random": round(m.random_expected_hits(5), 3),
            "top5_edge": round(float(rank_backtest.get("top5_edge", 0.0)), 3),
            "top9_avg": round(float(rank_backtest.get("top9_avg", 0.0)), 3),
            "top9_random": round(m.random_expected_hits(m.CORE_POOL_SIZE), 3),
            "top9_edge": round(top9_edge, 3),
            "top10_avg": round(float(rank_backtest.get("top10_avg", 0.0)), 3),
            "top10_random": round(m.random_expected_hits(10), 3),
            "top10_edge": round(float(rank_backtest.get("top10_edge", 0.0)), 3),
            "top15_avg": round(float(rank_backtest.get("top15_avg", 0.0)), 3),
            "top15_random": round(m.random_expected_hits(15), 3),
            "top15_edge": round(float(rank_backtest.get("top15_edge", 0.0)), 3),
        },
        "confidence_tickets": [
            {
                "rank": rank,
                "numbers": list(ticket.numbers),
                "label": m.ticket_confidence_label(ticket, max_ticket_score, rank),
                "strategy": m.strategy_label(ticket.strategy),
                "score": round(ticket.score, 3),
                "reasons": "；".join(ticket.reasons[-2:]) if ticket.reasons else "成熟度校準",
            }
            for rank, ticket in enumerate(package.tickets[:6], start=1)
        ],
        "super_picks": [
            {
                "label": item["label"],
                "target": item["target"],
                "numbers": list(item["numbers"]),
                "confidence": item["confidence"],
                "probability": item["probability"],
                "reason": item["reason"],
            }
            for item in m.super_recommendation_items(package)
        ],
        "core_numbers": [
            {
                "rank": rank,
                "number": row.number,
                "confidence": m.confidence_label(row, max_number_score),
                "score": round(row.score, 3),
                "reason": m.number_reasons(row, rank),
            }
            for rank, row in enumerate(ranked_scores[:m.CORE_POOL_SIZE], start=1)
        ],
        "top_numbers": [
            {
                "rank": rank,
                "number": row.number,
                "bucket": "9隻內核心" if rank <= m.CORE_POOL_SIZE else "10-15補位",
                "confidence": m.confidence_label(row, max_number_score),
                "score": round(row.score, 3),
                "reason": m.number_reasons(row, rank),
            }
            for rank, row in enumerate(ranked_scores[:15], start=1)
        ],
        "strong_packs": [
            {
                "title": title,
                "numbers": list(numbers),
                "target_hits": target_hits,
                "probability": round(m.probability_at_least_hits(len(numbers), target_hits), 6),
            }
            for title, numbers, target_hits in m.strong_pack_specs(ranked_numbers, package)
        ],
        "links": {
            "battle_report": m.SITE_BATTLE_REPORT_NAME,
            "prediction_report": m.SITE_LATEST_PREDICTION_NAME,
            "system_report": m.SITE_SYSTEM_REPORT_NAME,
            "draws_csv": m.SITE_DRAWS_CSV_NAME,
        },
    }


def render_mobile_html(payload: dict, asset_prefix: str, pwa: bool) -> str:
    latest = payload["latest"]
    prediction = payload["prediction"]
    system = payload["system"]
    accuracy = payload["accuracy"]
    links = payload["links"]
    latest_balls = balls(latest["main_numbers"]) + " " + ball(int(latest["special"]), special=True)
    manifest_link = f'<link rel="manifest" href="./{MOBILE_MANIFEST}">' if pwa else ""
    sw_script = (
        """
  <script>
    if ("serviceWorker" in navigator) {
      navigator.serviceWorker.register("./香港六合彩預測系統_離線快取.js").catch(() => {});
    }
  </script>
        """
        if pwa
        else ""
    )
    return f"""<!doctype html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
  <meta name="theme-color" content="#b42318">
  {manifest_link}
  <title>香港六合彩預測系統</title>
  <style>
    :root {{
      --red:#b42318; --blue:#2457a6; --green:#176b4d; --gold:#b7791f;
      --ink:#1f2328; --muted:#687076; --line:#ded8cf; --paper:#fffdf9; --wash:#f7f4ef;
    }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; background:var(--wash); color:var(--ink); font-family:"Microsoft JhengHei","Noto Sans TC",Arial,sans-serif; letter-spacing:0; }}
    a {{ color:inherit; text-decoration:none; }}
    .app {{ width:min(100%,720px); min-height:100vh; margin:0 auto; background:var(--paper); padding:14px 14px 84px; }}
    .hero,.section {{ border:1px solid var(--line); border-radius:8px; background:#fff; padding:14px; margin-bottom:12px; }}
    .hero {{ border-left:6px solid var(--red); padding:16px; }}
    .topline {{ display:flex; align-items:center; justify-content:space-between; gap:10px; color:var(--muted); font-size:12px; line-height:1.4; }}
    h1 {{ margin:8px 0 10px; font-size:26px; line-height:1.15; font-weight:800; }}
    h2 {{ margin:0 0 12px; font-size:18px; line-height:1.2; }}
    .badge {{ display:inline-flex; align-items:center; min-height:28px; padding:4px 9px; border:1px solid var(--line); border-radius:999px; background:#fff; color:var(--muted); font-size:12px; white-space:nowrap; }}
    .badge.hot {{ border-color:#f0b4ad; background:#fff1f0; color:var(--red); font-weight:800; }}
    .metrics {{ display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:10px; margin-top:12px; }}
    .metric {{ border:1px solid var(--line); border-radius:8px; padding:11px; background:#fff; min-width:0; }}
    .metric span,.meta,.note {{ color:var(--muted); font-size:12px; line-height:1.5; }}
    .metric strong {{ display:block; margin-top:3px; font-size:18px; line-height:1.25; overflow-wrap:anywhere; }}
    .balls,.draw-line,.pack-line {{ display:flex; flex-wrap:wrap; gap:8px; align-items:center; }}
    .ball {{ width:36px; height:36px; border-radius:50%; display:inline-flex; align-items:center; justify-content:center; color:#fff; font-size:15px; font-weight:800; flex:0 0 auto; }}
    .red {{ background:var(--red); }} .blue {{ background:var(--blue); }} .green {{ background:var(--green); }}
    .special {{ border:2px solid var(--gold); box-shadow:0 0 0 3px #fff7df; }}
    .ticket {{ border-top:1px solid var(--line); padding:12px 0; }}
    .ticket:first-of-type {{ border-top:0; padding-top:0; }}
    .ticket-head {{ display:flex; justify-content:space-between; align-items:center; gap:10px; margin-bottom:8px; }}
    .ticket-label {{ color:var(--red); font-weight:800; font-size:14px; line-height:1.3; overflow-wrap:anywhere; }}
    .super {{ border:3px solid var(--red); background:#fff8f6; box-shadow:0 0 0 4px rgba(180,35,24,.10); }}
    .super h2 {{ color:var(--red); }}
    .super-card {{ border-top:1px solid #f0b4ad; padding:12px 0; }}
    .super-card:first-of-type {{ border-top:0; padding-top:0; }}
    .super-label {{ color:var(--red); font-weight:900; font-size:15px; line-height:1.3; }}
    .number-grid {{ display:grid; grid-template-columns:repeat(5,minmax(0,1fr)); gap:8px; }}
    .number-cell {{ border:1px solid var(--line); border-radius:8px; padding:9px 5px; text-align:center; background:#fff; min-width:0; }}
    .number-cell .ball {{ margin:0 auto 5px; }}
    .number-cell small {{ color:var(--muted); font-size:11px; line-height:1.3; display:block; }}
    .accuracy-row {{ display:grid; grid-template-columns:64px 1fr auto; gap:10px; align-items:center; padding:9px 0; border-top:1px solid var(--line); }}
    .accuracy-row:first-child {{ border-top:0; }}
    .bar {{ height:8px; border-radius:999px; background:#eee7dc; overflow:hidden; }}
    .bar span {{ display:block; height:100%; width:var(--w); background:linear-gradient(90deg,var(--red),var(--gold),var(--green)); }}
    .link-grid {{ display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:10px; }}
    .action {{ border:1px solid var(--line); border-radius:8px; padding:12px; background:#fff; font-weight:800; text-align:center; min-height:46px; }}
    .action.primary {{ background:var(--red); border-color:var(--red); color:#fff; }}
    .bottom-nav {{ position:fixed; left:50%; bottom:0; transform:translateX(-50%); width:min(100%,720px); display:grid; grid-template-columns:repeat(4,1fr); gap:1px; background:var(--line); border-top:1px solid var(--line); padding-bottom:env(safe-area-inset-bottom); z-index:20; }}
    .bottom-nav a {{ background:#fff; min-height:58px; display:flex; align-items:center; justify-content:center; font-size:13px; font-weight:800; }}
    @media (max-width:390px) {{
      .app {{ padding-left:10px; padding-right:10px; }}
      h1 {{ font-size:23px; }}
      .metrics,.link-grid {{ grid-template-columns:1fr; }}
      .number-grid {{ grid-template-columns:repeat(4,minmax(0,1fr)); }}
      .ball {{ width:34px; height:34px; font-size:14px; }}
    }}
  </style>
</head>
<body>
  <main class="app">
    <section class="hero" id="top">
      <div class="topline"><span>手機獨立入口</span><span class="badge hot">{e(system["risk_level"])}風險</span></div>
      <h1>香港六合彩預測系統</h1>
      <div class="meta">產生 {e(payload["generated_at"])} / 模型 {e(payload["model_version"])}</div>
      <div class="metrics">
        <div class="metric"><span>最新期</span><strong>{e(latest["draw_no"])} / {e(latest["date"])}</strong></div>
        <div class="metric"><span>最新預測</span><strong>第 {prediction["run_id"] if prediction["run_id"] is not None else "-"} 筆</strong></div>
        <div class="metric"><span>預測目標</span><strong>{e(prediction["target_date"])}</strong></div>
        <div class="metric"><span>9隻內差值</span><strong>{accuracy["top9_edge"]:+.3f}</strong></div>
      </div>
    </section>

    <section class="section">
      <h2>最新開獎</h2>
      <div class="draw-line">{latest_balls}</div>
      <p class="note">歷史資料庫 {payload["draw_count"]} 期 / {e(payload["date_range"])}</p>
    </section>

    <section class="section" id="prediction">
      <h2>最新預測第 {prediction["run_id"] if prediction["run_id"] is not None else "-"} 筆</h2>
      <div class="metrics">
        <div class="metric"><span>預測基準期</span><strong>{e(prediction["based_on_draw_no"])} / {e(prediction["based_on_draw_date"])}</strong></div>
        <div class="metric"><span>產生時間</span><strong>{e(prediction["created_at"])}</strong></div>
        <div class="metric"><span>策略 / 組數</span><strong>{e(prediction["strategy"])} / {prediction["ticket_count"]} 組</strong></div>
        <div class="metric"><span>預測目標</span><strong>{e(prediction["target_date"])}</strong></div>
      </div>
      <p class="note">下方高機率信心牌、9隻內核心池、Top 15 補位池、核心模型全部使用這一筆最新預測。</p>
    </section>

    <section class="section" id="confidence">
      <h2>高機率信心牌</h2>
      {ticket_cards(payload["confidence_tickets"])}
    </section>

    <section class="section super" id="super">
      <h2>超強信心強推薦</h2>
      {super_pick_cards(payload["super_picks"])}
      <p class="note">獨隻、2碼、3碼獨立精算；屬研究強推薦，不保證開出。</p>
    </section>

    <section class="section" id="core">
      <h2>9隻內核心命中池</h2>
      <div class="number-grid">{number_grid(payload["core_numbers"])}</div>
      <p class="note">高機率先看這 9 隻，10-15 只作防守補位。</p>
    </section>

    <section class="section" id="numbers">
      <h2>Top 15 詳表</h2>
      <div class="number-grid">{number_grid(payload["top_numbers"])}</div>
    </section>

    <section class="section" id="accuracy">
      <h2>運算精準度</h2>
      {accuracy_row("Top5", accuracy["top5_avg"], accuracy["top5_random"], accuracy["top5_edge"])}
      {accuracy_row("9隻內", accuracy["top9_avg"], accuracy["top9_random"], accuracy["top9_edge"])}
      {accuracy_row("Top10", accuracy["top10_avg"], accuracy["top10_random"], accuracy["top10_edge"])}
      {accuracy_row("Top15", accuracy["top15_avg"], accuracy["top15_random"], accuracy["top15_edge"])}
      <p class="note">樣本 {accuracy["sample"]} 期 / 共識 {system["consensus"]:.3f} / {e(system["release_level"])}</p>
    </section>

    <section class="section">
      <h2>核心專用模型</h2>
      {strong_packs(payload["strong_packs"])}
    </section>

    <section class="section" id="system">
      <h2>系統狀態</h2>
      <div class="metrics">
        <div class="metric"><span>狀態</span><strong>{e(system["status"])}</strong></div>
        <div class="metric"><span>主力策略</span><strong>{e(system["champion"])}</strong></div>
        <div class="metric"><span>上期命中檢討</span><strong>{e(system["settled_status"])}</strong></div>
        <div class="metric"><span>權重</span><strong>{e(system["auto_weights"])}</strong></div>
      </div>
    </section>

    <section class="section" id="reports">
      <h2>戰報入口</h2>
      <div class="link-grid">
        <a class="action primary" href="{e(asset_prefix + links["battle_report"])}">完整戰報</a>
        <a class="action" href="{e(asset_prefix + links["prediction_report"])}">最新預測</a>
        <a class="action" href="{e(asset_prefix + links["system_report"])}">系統報告</a>
        <a class="action" href="{e(asset_prefix + links["draws_csv"])}">歷史資料</a>
      </div>
      <p class="note">本系統做統計、回測與紀錄管理，不保證開出。</p>
    </section>
  </main>
  <nav class="bottom-nav">
    <a href="#top">總覽</a>
    <a href="#super">強推</a>
    <a href="#core">核心</a>
    <a href="#reports">戰報</a>
  </nav>
  {sw_script}
</body>
</html>
"""


def manifest() -> dict:
    return {
        "name": "香港六合彩預測系統",
        "short_name": "香港六合彩預測系統",
        "start_url": f"./{MOBILE_HTML}",
        "scope": "./",
        "display": "standalone",
        "background_color": "#f7f4ef",
        "theme_color": "#b42318",
        "description": "香港六合彩預測系統手機獨立入口",
        "icons": [],
    }


def service_worker() -> str:
    return f"""const CACHE_NAME = "香港六合彩預測系統-20260624-v8";
const ASSETS = ["./{MOBILE_HTML}","./{MOBILE_STATUS}","./{m.SITE_BATTLE_REPORT_NAME}","./{m.SITE_LATEST_PREDICTION_NAME}","./{m.SITE_SYSTEM_REPORT_NAME}","./{m.SITE_DRAWS_CSV_NAME}"];
self.addEventListener("install", event => {{
  event.waitUntil(caches.open(CACHE_NAME).then(cache => cache.addAll(ASSETS)).catch(() => undefined));
  self.skipWaiting();
}});
self.addEventListener("activate", event => {{
  event.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(key => key !== CACHE_NAME).map(key => caches.delete(key)))));
  self.clients.claim();
}});
self.addEventListener("fetch", event => {{
  event.respondWith(fetch(event.request).then(response => {{
    const copy = response.clone();
    caches.open(CACHE_NAME).then(cache => cache.put(event.request, copy)).catch(() => undefined);
    return response;
  }}).catch(() => caches.match(event.request).then(cached => cached || caches.match("./{MOBILE_HTML}"))));
}});
"""


def ticket_cards(tickets: list[dict]) -> str:
    return "\n".join(
        '<div class="ticket">'
        '<div class="ticket-head">'
        f'<div class="ticket-label">{ticket["rank"]}. {e(ticket["label"])}</div>'
        f'<span class="badge">{e(ticket["strategy"])} / {ticket["score"]:.3f}</span>'
        '</div>'
        f'<div class="draw-line">{balls(ticket["numbers"])}</div>'
        f'<div class="meta">{e(ticket["reasons"])}</div>'
        '</div>'
        for ticket in tickets
    )


def super_pick_cards(picks: list[dict]) -> str:
    return "\n".join(
        '<div class="super-card">'
        '<div class="ticket-head">'
        f'<div class="super-label">{e(pick["label"])} / {e(pick["target"])}</div>'
        f'<span class="badge hot">信心 {e(pick["confidence"])}</span>'
        '</div>'
        f'<div class="draw-line">{balls(pick["numbers"])}</div>'
        f'<div class="meta">{e(pick["probability"])}</div>'
        f'<div class="meta">{e(pick["reason"])}</div>'
        '</div>'
        for pick in picks
    )


def number_grid(numbers: list[dict]) -> str:
    return "\n".join(
        '<div class="number-cell">'
        f'{ball(int(row["number"]))}'
        f'<small>#{row["rank"]} {e(row.get("bucket", "核心"))}</small>'
        f'<small>{e(row["confidence"])}</small>'
        '</div>'
        for row in numbers
    )


def accuracy_row(label: str, avg_hit: float, random_hit: float, edge: float) -> str:
    width = max(8.0, min(100.0, (avg_hit / 2.2) * 100.0))
    return (
        '<div class="accuracy-row">'
        f'<strong>{e(label)}</strong>'
        f'<div><div class="bar" style="--w:{width:.1f}%"><span></span></div>'
        f'<div class="meta">平均 {avg_hit:.3f} / 隨機 {random_hit:.3f}</div></div>'
        f'<span class="badge hot">{edge:+.3f}</span>'
        '</div>'
    )


def strong_packs(packs: list[dict]) -> str:
    return "\n".join(
        '<div class="ticket">'
        '<div class="ticket-head">'
        f'<div class="ticket-label">{e(pack["title"])} / 目標 {pack["target_hits"]} 中</div>'
        f'<span class="badge">P {pack["probability"]:.6f}</span>'
        '</div>'
        f'<div class="pack-line">{balls(pack["numbers"])}</div>'
        '</div>'
        for pack in packs
    )


def balls(numbers) -> str:
    return " ".join(ball(int(number)) for number in numbers)


def ball(number: int, special: bool = False) -> str:
    color_class = {"紅波": "red", "藍波": "blue", "綠波": "green"}[m.wave_color(number)]
    special_class = " special" if special else ""
    return f'<span class="ball {color_class}{special_class}">{number:02d}</span>'


def e(value) -> str:
    return html.escape(str(value), quote=True)


def main() -> None:
    args = parse_args()
    with m.connect(args.db) as conn:
        paths = build_mobile_cloud_site(conn, args.site_dir, args.report_dir, args.recent_window)
    print(f"手機雲端首頁: {paths['mobile']}")
    print(f"手機雲端戰報: {paths['report']}")


if __name__ == "__main__":
    main()

