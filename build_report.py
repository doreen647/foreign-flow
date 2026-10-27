"""
每日外資「產業資金輪動」報告
- 從證交所抓三大法人買賣超 (T86)、收盤價 (MI_INDEX)、產業別 (OpenAPI)
- 外資買賣超股數 x 收盤價 = 估算買賣超金額（億元）
- 按產業加總，累積到 data/industry_daily.csv
- 產生 docs/index.html
"""
import csv
import json
import os
import time
from datetime import datetime, timedelta, timezone
from html import escape

import requests

HEADERS = {"User-Agent": "Mozilla/5.0"}
DATA_FILE = "data/industry_daily.csv"
SKIP_FILE = "data/skip_dates.txt"      # 已確認沒開市的日期（假日）
MAP_FILE = "data/industry_map.json"    # 產業別快取
OUT_FILE = "docs/index.html"
BACKFILL_DAYS = 45                     # 第一次執行回補的天數
SLEEP = 3                              # 每次請求間隔秒數，避免被證交所擋

INDUSTRY_NAMES = {
    "01": "水泥工業", "02": "食品工業", "03": "塑膠工業", "04": "紡織纖維",
    "05": "電機機械", "06": "電器電纜", "08": "玻璃陶瓷", "09": "造紙工業",
    "10": "鋼鐵工業", "11": "橡膠工業", "12": "汽車工業", "14": "建材營造",
    "15": "航運業", "16": "觀光餐旅", "17": "金融保險", "18": "貿易百貨",
    "20": "其他", "21": "化學工業", "22": "生技醫療", "23": "油電燃氣",
    "24": "半導體(不含台積電)", "25": "電腦及週邊", "26": "光電業",
    "27": "通信網路", "28": "電子零組件", "29": "電子通路", "30": "資訊服務",
    "31": "其他電子", "35": "綠能環保", "36": "數位雲端", "37": "運動休閒",
    "38": "居家生活",
}


def num(s):
    try:
        return float(str(s).replace(",", "").strip())
    except Exception:
        return None


def get_json(url, params=None, retries=3):
    for _ in range(retries):
        try:
            r = requests.get(url, params=params, headers=HEADERS, timeout=30)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            print("  請求失敗，重試中：", e)
            time.sleep(5)
    return None


def load_industry_map():
    """回傳 {股票代號: 產業名稱}"""
    j = get_json("https://openapi.twse.com.tw/v1/opendata/t187ap03_L")
    if j:
        m = {}
        for row in j:
            code = str(row.get("公司代號", "")).strip()
            ind = str(row.get("產業別", "")).strip()
            if code:
                m[code] = INDUSTRY_NAMES.get(ind, ind or "其他")
        os.makedirs("data", exist_ok=True)
        with open(MAP_FILE, "w", encoding="utf-8") as f:
            json.dump(m, f, ensure_ascii=False)
        return m
    if os.path.exists(MAP_FILE):
        with open(MAP_FILE, encoding="utf-8") as f:
            return json.load(f)
    raise RuntimeError("抓不到產業別資料，也沒有快取")


def fetch_foreign(d):
    """回傳 {代號: 外資買賣超股數}，若當天沒資料回傳 None"""
    j = get_json(
        "https://www.twse.com.tw/rwd/zh/fund/T86",
        {"date": d.strftime("%Y%m%d"), "selectType": "ALLBUT0999", "response": "json"},
    )
    if not j or j.get("stat") != "OK":
        return None
    fields = j["fields"]
    i_code = fields.index("證券代號")
    i_main = next((i for i, f in enumerate(fields)
                   if "外陸資買賣超" in f or "外資及陸資買賣超" in f), None)
    if i_main is None:
        print("  找不到外資買賣超欄位：", fields)
        return None
    i_dealer = None
    if "不含外資自營商" in fields[i_main]:
        i_dealer = next((i for i, f in enumerate(fields) if "外資自營商買賣超" in f), None)
    out = {}
    for row in j["data"]:
        v = num(row[i_main]) or 0
        if i_dealer is not None:
            v += num(row[i_dealer]) or 0
        out[row[i_code].strip()] = v
    return out


def fetch_prices(d):
    """回傳 {代號: 收盤價}"""
    j = get_json(
        "https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX",
        {"date": d.strftime("%Y%m%d"), "type": "ALLBUT0999", "response": "json"},
    )
    if not j or j.get("stat") != "OK":
        return None
    prices = {}
    for t in j.get("tables", []):
        fields = t.get("fields", [])
        if "證券代號" in fields and "收盤價" in fields:
            ic, ip = fields.index("證券代號"), fields.index("收盤價")
            for row in t["data"]:
                p = num(row[ip])
                if p:
                    prices[row[ic].strip()] = p
    return prices or None


def aggregate(d, imap):
    time.sleep(SLEEP)
    foreign = fetch_foreign(d)
    if foreign is None:
        return None
    time.sleep(SLEEP)
    prices = fetch_prices(d)
    if prices is None:
        return None
    agg = {}
    for code, shares in foreign.items():
        price = prices.get(code)
        if price is None:
            continue
        if code == "2330":
            ind = "台積電"
        elif code in imap:
            ind = imap[code]
        else:
            ind = "ETF/其他"
        a = agg.setdefault(ind, [0.0, 0, 0])
        a[0] += shares * price / 1e8   # 億元
        if shares > 0:
            a[1] += 1
        elif shares < 0:
            a[2] += 1
    return agg


def read_history():
    rows = []
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                rows.append({"date": r["date"], "industry": r["industry"],
                             "net": float(r["net"]), "up": int(r["up"]), "down": int(r["down"])})
    return rows


def write_history(rows):
    os.makedirs("data", exist_ok=True)
    rows.sort(key=lambda r: (r["date"], r["industry"]))
    with open(DATA_FILE, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["date", "industry", "net", "up", "down"])
        w.writeheader()
        for r in rows:
            w.writerow({**r, "net": round(r["net"], 4)})


def update_data():
    imap = load_industry_map()
    rows = read_history()
    have = {r["date"] for r in rows}
    skip = set()
    if os.path.exists(SKIP_FILE):
        skip = set(open(SKIP_FILE).read().split())

    today = (datetime.now(timezone.utc) + timedelta(hours=8)).date()
    for back in range(BACKFILL_DAYS, -1, -1):
        d = today - timedelta(days=back)
        key = d.isoformat()
        if d.weekday() >= 5 or key in have or key in skip:
            continue
        print("抓取", key)
        agg = aggregate(d, imap)
        if agg is None:
            print("  無資料（可能是假日或尚未公布）")
            if back >= 2:               # 最近兩天先不標記，下次再試
                skip.add(key)
            continue
        for ind, (net, up, down) in agg.items():
            rows.append({"date": key, "industry": ind, "net": net, "up": up, "down": down})
        have.add(key)

    write_history(rows)
    os.makedirs("data", exist_ok=True)
    with open(SKIP_FILE, "w") as f:
        f.write("\n".join(sorted(skip)))
    return rows


def fmt(v):
    return f"{v:+,.1f}"


def color(v):
    # 台灣習慣：紅=買超，綠=賣超
    return "#d93025" if v > 0 else ("#188038" if v < 0 else "#666")


def build_html(rows):
    dates = sorted({r["date"] for r in rows})
    if not dates:
        return "<p>目前沒有資料</p>"
    inds = sorted({r["industry"] for r in rows})

    def total(ds, ind):
        s = set(ds)
        return sum(r["net"] for r in rows if r["industry"] == ind and r["date"] in s)

    w1, w5, w20 = dates[-1:], dates[-5:], dates[-20:]
    table = []
    for ind in inds:
        table.append((ind, total(w1, ind), total(w5, ind), total(w20, ind)))
    table.sort(key=lambda t: -t[2])
    max5 = max(abs(t[2]) for t in table) or 1

    # 輪動：近5日 vs 前5日
    flips = []
    if len(dates) >= 10:
        cur, prev = dates[-5:], dates[-10:-5]
        for ind in inds:
            c, p = total(cur, ind), total(prev, ind)
            if c > 0 > p:
                flips.append((ind, "轉買超", p, c))
            elif c < 0 < p:
                flips.append((ind, "轉賣超", p, c))
        flips.sort(key=lambda t: -abs(t[3] - t[2]))

    day_total = {d: sum(r["net"] for r in rows if r["date"] == d) for d in dates}

    h = []
    h.append(f"""<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>外資產業資金輪動</title>
<style>
body{{font-family:-apple-system,"Noto Sans TC",sans-serif;max-width:760px;margin:0 auto;padding:16px;color:#222;line-height:1.5}}
h1{{font-size:1.3rem}} h2{{font-size:1.05rem;margin-top:1.6rem}}
table{{width:100%;border-collapse:collapse;font-size:.9rem}}
th,td{{padding:6px 4px;border-bottom:1px solid #eee;text-align:right}}
th:first-child,td:first-child{{text-align:left}}
.bar{{height:8px;border-radius:4px;display:inline-block;vertical-align:middle}}
.note{{color:#666;font-size:.8rem}}
.wrap{{overflow-x:auto}}
</style></head><body>
<h1>外資產業資金輪動</h1>
<p class="note">資料日期：{dates[-1]}（共累積 {len(dates)} 個交易日）。金額單位：億元，以外資買賣超股數 × 收盤價估算，僅供參考。紅＝買超、綠＝賣超。</p>
""")

    h.append("<h2>近 5 日 vs 前 5 日：換邊的產業</h2>")
    if not dates[-10:-5]:
        h.append('<p class="note">累積資料不足 10 個交易日，暫無法比較。</p>')
    elif not flips:
        h.append('<p class="note">這一期沒有產業買賣方向反轉。</p>')
    else:
        h.append("<ul>")
        for ind, kind, p, c in flips:
            h.append(f'<li><b>{escape(ind)}</b>：{kind}（前 5 日 {fmt(p)} → 近 5 日 {fmt(c)}）</li>')
        h.append("</ul>")

    h.append('<h2>各產業外資淨買賣超</h2><div class="wrap"><table><tr><th>產業</th><th>1日</th><th>5日</th><th>20日</th><th></th></tr>')
    for ind, a, b, c in table:
        width = int(abs(b) / max5 * 80)
        h.append(
            f'<tr><td>{escape(ind)}</td>'
            f'<td style="color:{color(a)}">{fmt(a)}</td>'
            f'<td style="color:{color(b)}">{fmt(b)}</td>'
            f'<td style="color:{color(c)}">{fmt(c)}</td>'
            f'<td style="text-align:left"><span class="bar" style="width:{width}px;background:{color(b)}"></span></td></tr>'
        )
    h.append("</table></div>")

    h.append('<h2>外資每日合計買賣超</h2><div class="wrap"><table><tr><th>日期</th><th>合計（億）</th></tr>')
    for d in reversed(dates[-10:]):
        v = day_total[d]
        h.append(f'<tr><td>{d}</td><td style="color:{color(v)}">{fmt(v)}</td></tr>')
    h.append("</table></div>")

    h.append('<h2>最近一日各產業買超／賣超檔數</h2><div class="wrap"><table><tr><th>產業</th><th>買超檔數</th><th>賣超檔數</th></tr>')
    for r in sorted((r for r in rows if r["date"] == dates[-1]), key=lambda r: -r["net"]):
        h.append(f'<tr><td>{escape(r["industry"])}</td><td>{r["up"]}</td><td>{r["down"]}</td></tr>')
    h.append("</table></div>")

    h.append('<p class="note">資料來源：臺灣證券交易所。台積電獨立列出，避免半導體整體數字被單一權值股蓋過。</p></body></html>')
    return "".join(h)


def main():
    rows = update_data()
    os.makedirs("docs", exist_ok=True)
    with open(OUT_FILE, "w", encoding="utf-8") as f:
        f.write(build_html(rows))
    print("完成，已產生", OUT_FILE)


if __name__ == "__main__":
    main()
