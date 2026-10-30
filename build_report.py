"""
每日外資「產業資金輪動」報告 v2
- 每日抓證交所外資買賣超 (T86)、收盤價 (MI_INDEX)、產業別 (OpenAPI)
- 以「個股」為單位累積到 data/stock_daily.csv（金額單位：億元，股數 x 收盤價估算）
- 產生 docs/index.html：內嵌資料，圖表、產業明細、自選股都在瀏覽器裡計算
"""
import csv
import json
import os
import time
import urllib.parse
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from datetime import datetime, timedelta, timezone

import requests

HEADERS = {"User-Agent": "Mozilla/5.0"}
DATA_FILE = "data/stock_daily.csv"
SKIP_FILE = "data/skip_dates.txt"      # 已確認沒開市的日期（假日）
MAP_FILE = "data/industry_map.json"    # 產業別快取
# ===== 半導體區塊設定：（區塊名稱, {股票代號: 名稱}），要增減個股直接改這裡 =====
BLOCKS = [
    ("晶圓代工", {"2330": "台積電", "2303": "聯電", "5347": "世界先進", "6770": "力積電"}),
    ("IC設計", {"2454": "聯發科", "3034": "聯詠", "2379": "瑞昱", "2458": "義隆", "5269": "祥碩", "4966": "譜瑞-KY"}),
    ("ASIC/IP", {"3443": "創意", "3661": "世芯-KY", "3035": "智原", "3529": "力旺", "6643": "M31"}),
    ("記憶體", {"2408": "南亞科", "2344": "華邦電", "2337": "旺宏", "8299": "群聯"}),
    ("封測", {"3711": "日月光投控", "6239": "力成", "2449": "京元電子", "3264": "欣銓", "6257": "矽格"}),
    ("設備/廠務", {"3131": "弘塑", "3583": "辛耘", "3680": "家登", "6187": "萬潤", "6196": "帆宣", "2404": "漢唐"}),
    ("矽晶圓/材料", {"6488": "環球晶", "5483": "中美晶", "3532": "台勝科", "5434": "崇越"}),
    ("載板", {"3037": "欣興", "8046": "南電", "3189": "景碩"}),
    ("功率/類比", {"3707": "漢磊", "3016": "嘉晶", "5425": "台半", "2481": "強茂", "8255": "朋程"}),
]

# ===== 新聞設定：只抓半導體與金融股，近 3 天 =====
NEWS_QUERIES = {
    "semi": "半導體 when:3d",
    "fin": "(金融股 OR 金控 OR 銀行股) when:3d",
}


def fetch_news(query, limit=12):
    url = ("https://news.google.com/rss/search?q=" + urllib.parse.quote(query)
           + "&hl=zh-TW&gl=TW&ceid=TW:zh-Hant")
    r = requests.get(url, headers=HEADERS, timeout=20)
    r.raise_for_status()
    items, seen = [], set()
    for it in ET.fromstring(r.content).iter("item"):
        title = (it.findtext("title") or "").strip()
        src_el = it.find("source")
        src = (src_el.text or "").strip() if src_el is not None else ""
        if src and title.endswith(" - " + src):
            title = title[:-(len(src) + 3)]
        if not title or title in seen:
            continue
        seen.add(title)
        try:
            dt = parsedate_to_datetime(it.findtext("pubDate")).astimezone(timezone(timedelta(hours=8)))
            d, ts = dt.strftime("%m/%d %H:%M"), dt.timestamp()
        except Exception:
            d, ts = "", 0
        items.append({"t": title, "u": (it.findtext("link") or "").strip(), "s": src, "d": d, "ts": ts})
    items.sort(key=lambda x: -x["ts"])
    return [{k: v for k, v in x.items() if k != "ts"} for x in items[:limit]]


def fetch_all_news():
    out = {}
    for key, q in NEWS_QUERIES.items():
        try:
            out[key] = fetch_news(q)
        except Exception as e:  # 新聞抓不到不應該讓整個網頁更新失敗
            print("news failed:", key, e)
            out[key] = []
    return out
OUT_FILE = "docs/index.html"
BACKFILL_DAYS = 90                     # 第一次執行回補的日曆天數（約 60 個交易日）
KEEP_DAYS = 60                         # 網頁內嵌最近幾個交易日
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
    "38": "居家生活","19": "綜合企業", "32": "文化創意", "33": "農業科技", 
    "34": "電子商務", "91": "存託憑證",
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
    """回傳 {代號: (名稱, 外資買賣超股數)}，當天沒資料回傳 None"""
    j = get_json(
        "https://www.twse.com.tw/rwd/zh/fund/T86",
        {"date": d.strftime("%Y%m%d"), "selectType": "ALLBUT0999", "response": "json"},
    )
    if not j or j.get("stat") != "OK":
        return None
    fields = j["fields"]
    i_code = fields.index("證券代號")
    i_name = fields.index("證券名稱") if "證券名稱" in fields else None
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
        name = row[i_name].strip() if i_name is not None else row[i_code].strip()
        out[row[i_code].strip()] = (name, v)
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


def fetch_day(d, imap):
    """回傳當天各股列表，沒資料回傳 None"""
    time.sleep(SLEEP)
    foreign = fetch_foreign(d)
    if foreign is None:
        return None
    time.sleep(SLEEP)
    prices = fetch_prices(d)
    if prices is None:
        return None
    out = []
    for code, (name, shares) in foreign.items():
        price = prices.get(code)
        if price is None or shares == 0:
            continue
        if code == "2330":
            ind = "台積電"
        else:
            ind = imap.get(code, "ETF/其他")
        out.append({"date": d.isoformat(), "code": code, "name": name,
                    "industry": ind, "net": round(shares * price / 1e8, 4)})
    return out


def read_history():
    rows = []
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                rows.append({"date": r["date"], "code": r["code"], "name": r["name"],
                             "industry": r["industry"], "net": float(r["net"])})
    return rows


def write_history(rows):
    os.makedirs("data", exist_ok=True)
    rows.sort(key=lambda r: (r["date"], r["code"]))
    with open(DATA_FILE, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["date", "code", "name", "industry", "net"])
        w.writeheader()
        w.writerows(rows)


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
        day = fetch_day(d, imap)
        if day is None:
            print("  無資料（可能是假日或尚未公布）")
            if back >= 2:               # 最近兩天先不標記，下次再試
                skip.add(key)
            continue
        rows.extend(day)
        have.add(key)

    write_history(rows)
    os.makedirs("data", exist_ok=True)
    with open(SKIP_FILE, "w") as f:
        f.write("\n".join(sorted(skip)))
    return rows


TEMPLATE = r"""<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>外資產業資金輪動</title>
<style>
body{font-family:-apple-system,"Noto Sans TC",sans-serif;max-width:800px;margin:0 auto;padding:16px;color:#222;line-height:1.5}
h1{font-size:1.3rem} h2{font-size:1.05rem;margin-top:1.8rem}
table{width:100%;border-collapse:collapse;font-size:.88rem}
th,td{padding:6px 4px;border-bottom:1px solid #eee;text-align:right}
th:first-child,td:first-child{text-align:left}
.note{color:#666;font-size:.8rem}
.wrap{overflow-x:auto}
.btn{border:1px solid #ccc;background:#fff;border-radius:16px;padding:4px 12px;margin:2px;font-size:.85rem;cursor:pointer}
.btn.on{background:#222;color:#fff;border-color:#222}
.chip{display:inline-block;border:2px solid;border-radius:14px;padding:2px 10px;margin:2px;font-size:.8rem;cursor:pointer;user-select:none}
.ind{cursor:pointer}
.ind:hover{background:#fafafa}
.detail td{background:#f8f8f8;text-align:left;font-size:.82rem;vertical-align:top}
.add{color:#1a73e8;cursor:pointer;margin-left:4px}
input{padding:6px 8px;border:1px solid #ccc;border-radius:6px;font-size:.9rem;width:12em}
.x{color:#999;cursor:pointer}
.tabs{display:flex;gap:4px;flex-wrap:wrap;position:sticky;top:0;background:#fff;padding:8px 0;z-index:5;border-bottom:1px solid #eee}
.tabs .btn{margin:0}
section{display:none}
section.on{display:block}
.blk{cursor:pointer}
.news{list-style:none;padding:0;margin:0}
.news li{padding:8px 0;border-bottom:1px solid #eee}
.news a{color:#222;text-decoration:none}
</style></head><body>
<h1>外資產業資金輪動</h1>
<p class="note" id="meta"></p>
<div class="tabs" id="tabs"></div>
<div id="winbtns" style="margin-top:8px"></div>

<section id="t-blocks">
<h2>半導體區塊：外資買賣超分布</h2>
<div id="blockchart"></div>
<div id="blockdetail"></div>
</section>

<section id="t-news">
<h2>半導體新聞</h2>
<div id="news-semi"></div>
<h2>金融股新聞</h2>
<div id="news-fin"></div>
<p class="note">標題與連結來自 Google 新聞，只列近 3 天，每次網頁更新時重新抓取。</p>
</section>

<section id="t-rotation">
<h2>近 5 日 vs 前 5 日：換邊的產業</h2>
<div id="flips"></div>

<h2>累積買賣超走勢</h2>
<div id="rangebtns"></div>
<div id="chips"></div>
<div id="chart"></div>
</section>

<section id="t-industry">
<h2>各產業外資淨買賣超（點一列看個股明細）</h2>
<div class="wrap"><table id="ind"></table></div>
</section>

<section id="t-watchlist">
<h2>我的自選股</h2>
<p class="note">自選股只存在你這個瀏覽器裡，換手機或電腦要重新加。只能加入近期外資有買賣紀錄的上市股票。</p>
<input id="q" list="stocklist" placeholder="輸入代號或名稱，如 2317">
<datalist id="stocklist"></datalist>
<button class="btn" id="addbtn">加入</button> <span class="note" id="msg"></span>
<div class="wrap"><table id="wl"></table></div>
</section>

<p class="note">金額單位：億元，以外資買賣超股數 × 收盤價估算，僅供參考。紅＝買超、綠＝賣超。資料來源：臺灣證券交易所。台積電獨立列出，避免半導體整體數字被單一權值股蓋過。</p>

<script>
const D = __DATA__;
const dates = D.dates, S = D.stocks, N = dates.length;
const codes = Object.keys(S);
const PAL = ['#d93025','#1a73e8','#188038','#f9ab00','#9334e6','#e8710a','#12b5cb','#c2185b','#5f6368','#7cb342'];
let win = 5, chartDays = 20, open = null;
let sel = null;

const sum = (v, a, b) => { let t = 0; for (let i = Math.max(0, a); i < b; i++) t += v[i]; return t; };
const fmt = x => (x > 0 ? '+' : '') + x.toLocaleString('en-US', {minimumFractionDigits: 1, maximumFractionDigits: 1});
const col = x => x > 0 ? '#d93025' : x < 0 ? '#188038' : '#666';
const esc = s => String(s).replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const $ = id => document.getElementById(id);

const inds = {};
for (const c of codes) {
  const s = S[c];
  const o = inds[s.i] || (inds[s.i] = new Array(N).fill(0));
  for (let i = 0; i < N; i++) o[i] += s.v[i];
}
const indNames = Object.keys(inds);

function btns(id, opts, cur, fn) {
  $(id).innerHTML = opts.map(o => `<button class="btn ${o[0] === cur ? 'on' : ''}" data-v="${o[0]}">${o[1]}</button>`).join('');
  $(id).querySelectorAll('button').forEach(b => b.onclick = () => fn(Number(b.dataset.v)));
}

function renderFlips() {
  if (N < 10) { $('flips').innerHTML = '<p class="note">累積資料不足 10 個交易日，暫無法比較。</p>'; return; }
  const f = [];
  for (const n of indNames) {
    const c = sum(inds[n], N - 5, N), p = sum(inds[n], N - 10, N - 5);
    if (c > 0 && p < 0) f.push([n, '轉買超', p, c]);
    else if (c < 0 && p > 0) f.push([n, '轉賣超', p, c]);
  }
  f.sort((a, b) => Math.abs(b[3] - b[2]) - Math.abs(a[3] - a[2]));
  $('flips').innerHTML = f.length
    ? '<ul>' + f.map(x => `<li><b>${esc(x[0])}</b>：${x[1]}（前 5 日 ${fmt(x[2])} → 近 5 日 ${fmt(x[3])}）</li>`).join('') + '</ul>'
    : '<p class="note">這一期沒有產業買賣方向反轉。</p>';
}

function renderChart() {
  const k = Math.min(chartDays, N), st = N - k;
  if (sel === null) {
    sel = new Set(indNames.slice().sort((a, b) => Math.abs(sum(inds[b], N - 20, N)) - Math.abs(sum(inds[a], N - 20, N))).slice(0, 5));
  }
  const order = indNames.slice().sort((a, b) => Math.abs(sum(inds[b], N - 20, N)) - Math.abs(sum(inds[a], N - 20, N)));
  const colorOf = {}; order.forEach((n, i) => colorOf[n] = PAL[i % PAL.length]);
  $('chips').innerHTML = order.map(n => {
    const on = sel.has(n), c = colorOf[n];
    return `<span class="chip" data-n="${esc(n)}" style="border-color:${c};${on ? `background:${c};color:#fff` : `color:${c}`}">${esc(n)}</span>`;
  }).join('');
  $('chips').querySelectorAll('.chip').forEach(e => e.onclick = () => {
    const n = e.dataset.n; sel.has(n) ? sel.delete(n) : sel.add(n); renderChart();
  });
  const series = [];
  for (const n of order) if (sel.has(n)) {
    let a = 0; const pts = [];
    for (let i = st; i < N; i++) { a += inds[n][i]; pts.push(a); }
    series.push([n, pts]);
  }
  const W = 700, H = 320, L = 52, R = 10, T = 10, B = 26;
  let lo = 0, hi = 0;
  series.forEach(s => s[1].forEach(v => { lo = Math.min(lo, v); hi = Math.max(hi, v); }));
  if (hi === lo) { hi += 1; lo -= 1; }
  const pad = (hi - lo) * 0.08; hi += pad; lo -= pad;
  const X = i => L + (k <= 1 ? 0 : i * (W - L - R) / (k - 1));
  const Y = v => T + (hi - v) * (H - T - B) / (hi - lo);
  let g = '';
  for (let i = 0; i <= 4; i++) {
    const v = lo + (hi - lo) * i / 4, y = Y(v);
    g += `<line x1="${L}" x2="${W - R}" y1="${y}" y2="${y}" stroke="#eee"/><text x="${L - 6}" y="${y + 4}" font-size="11" text-anchor="end" fill="#888">${Math.round(v).toLocaleString()}</text>`;
  }
  g += `<line x1="${L}" x2="${W - R}" y1="${Y(0)}" y2="${Y(0)}" stroke="#999" stroke-dasharray="4 3"/>`;
  for (const idx of [0, Math.floor((k - 1) / 2), k - 1]) {
    g += `<text x="${X(idx)}" y="${H - 8}" font-size="11" text-anchor="${idx === 0 ? 'start' : idx === k - 1 ? 'end' : 'middle'}" fill="#888">${dates[st + idx].slice(5)}</text>`;
  }
  for (const [n, pts] of series) {
    g += `<polyline fill="none" stroke="${colorOf[n]}" stroke-width="2" points="${pts.map((v, i) => X(i) + ',' + Y(v)).join(' ')}"/>`;
  }
  $('chart').innerHTML = `<svg viewBox="0 0 ${W} ${H}" style="width:100%;height:auto">${g}</svg><p class="note">每條線＝該產業在這段期間的外資累積淨買賣超（億元），起點為 0。點上方標籤可顯示／隱藏。</p>`;
}

function topStocks(ind, k) {
  const a = [];
  for (const c of codes) if (S[c].i === ind) {
    const t = sum(S[c].v, N - k, N);
    if (t) a.push([c, S[c].n, t]);
  }
  a.sort((x, y) => y[2] - x[2]);
  return {buy: a.filter(x => x[2] > 0).slice(0, 5), sell: a.filter(x => x[2] < 0).slice(-5).reverse()};
}

function stockList(arr) {
  if (!arr.length) return '<span class="note">無</span>';
  return arr.map(x => `${esc(x[1])}(${x[0]}) <span style="color:${col(x[2])}">${fmt(x[2])}</span><span class="add" data-c="${x[0]}" title="加入自選股">＋</span>`).join('<br>');
}

function renderInd() {
  const rows = indNames.map(n => [n, sum(inds[n], N - 1, N), sum(inds[n], N - 3, N), sum(inds[n], N - 5, N), sum(inds[n], N - 20, N)]);
  const key = win === 1 ? 1 : win === 3 ? 2 : win === 5 ? 3 : 4;
  rows.sort((a, b) => b[key] - a[key]);
  let h = '<tr><th>產業<th>1日</th><th>3日</th><th>5日</th><th>20日</th></tr>';
  for (const r of rows) {
    h += `<tr class="ind" data-n="${esc(r[0])}"><td>${open === r[0] ? '▾' : '▸'} ${esc(r[0])}</td>` +
      [1, 2, 3, 4].map(i => `<td style="color:${col(r[i])}">${fmt(r[i])}</td>`).join('') + '</tr>';
    if (open === r[0]) {
      const t = topStocks(r[0], win);
      h += `<tr class="detail"><td colspan="5"><b>近 ${win} 日外資買超前 5 名</b><br>${stockList(t.buy)}<br><br><b>近 ${win} 日外資賣超前 5 名</b><br>${stockList(t.sell)}</td></tr>`;
    }
  }
  $('ind').innerHTML = h;
  $('ind').querySelectorAll('tr.ind').forEach(e => e.onclick = () => { open = open === e.dataset.n ? null : e.dataset.n; renderInd(); });
  $('ind').querySelectorAll('.add').forEach(e => e.onclick = ev => { ev.stopPropagation(); addWl(e.dataset.c); });
}

let wl = [];
try { wl = JSON.parse(localStorage.getItem('wl') || '[]'); } catch (e) {}
function saveWl() { try { localStorage.setItem('wl', JSON.stringify(wl)); } catch (e) {} }

function addWl(code) {
  if (!S[code]) { $('msg').textContent = '找不到 ' + code; return; }
  if (wl.includes(code)) { $('msg').textContent = S[code].n + ' 已在清單中'; return; }
  wl.push(code); saveWl(); $('msg').textContent = '已加入 ' + S[code].n; renderWl();
}

function streak(v) {
  const last = v[N - 1];
  if (!last) return 0;
  const sg = last > 0 ? 1 : -1;
  let c = 0;
  for (let i = N - 1; i >= 0; i--) { if (v[i] * sg > 0) c++; else break; }
  return c * sg;
}

function spark(v) {
  const k = Math.min(20, N); let a = 0; const p = [];
  for (let i = N - k; i < N; i++) { a += v[i]; p.push(a); }
  const lo = Math.min(0, ...p), hi = Math.max(0, ...p), r = (hi - lo) || 1;
  const pts = p.map((x, i) => (i * 60 / Math.max(1, k - 1)) + ',' + (18 - (x - lo) / r * 16)).join(' ');
  return `<svg viewBox="0 0 60 20" width="60" height="20"><polyline fill="none" stroke="${col(a)}" stroke-width="1.5" points="${pts}"/></svg>`;
}

function renderWl() {
  if (!wl.length) { $('wl').innerHTML = '<tr><td class="note">還沒有自選股，上方輸入代號加入，或在產業明細按「＋」。</td></tr>'; return; }
  let h = '<tr><th>股票</th><th>產業</th><th>1日</th><th>3日</th><th>5日</th><th>20日</th><th>連續</th><th>走勢</th><th></th></tr>';
  for (const c of wl) {
    const s = S[c]; if (!s) continue;
    const st = streak(s.v);
    h += `<tr><td>${esc(s.n)} <span class="note">${c}</span></td><td>${esc(s.i)}</td>` +
      [1, 3, 5, 20].map(k => { const t = sum(s.v, N - k, N); return `<td style="color:${col(t)}">${fmt(t)}</td>`; }).join('') +
      `<td style="color:${col(st)}">${st === 0 ? '-' : (st > 0 ? '買' : '賣') + Math.abs(st) + '天'}</td><td>${spark(s.v)}</td><td><span class="x" data-c="${c}">✕</span></td></tr>`;
  }
  $('wl').innerHTML = h;
  $('wl').querySelectorAll('.x').forEach(e => e.onclick = () => { wl = wl.filter(x => x !== e.dataset.c); saveWl(); renderWl(); });
}

function parseInput(t) {
  t = t.trim(); if (!t) return null;
  const first = t.split(/\s+/)[0];
  if (S[first]) return first;
  return codes.find(c => S[c].n === t || S[c].n === t.split(/\s+/).slice(1).join('')) || first;
}

const BLK = D.blocks || [];
let selBlk = null, lastCols = 0;

function packDots(a) {
  const p = [];
  for (const d of a) {
    if (!p.length) { d.x = 0; d.y = 0; p.push(d); continue; }
    for (let s = 1; s < 8000; s++) {
      d.x = 0.08 * s * Math.cos(0.25 * s); d.y = 0.08 * s * Math.sin(0.25 * s);
      if (p.every(q => Math.hypot(q.x - d.x, q.y - d.y) >= q.r + d.r + 1.5)) break;
    }
    p.push(d);
  }
}

function renderBlocks() {
  const el = $('blockchart');
  if (!BLK.length) { el.innerHTML = '<p class="note">尚未設定區塊（data/semi_blocks.json）。</p>'; $('blockdetail').innerHTML = ''; return; }
  const cols = (window.innerWidth || 800) < 600 ? 2 : 3, CW = 240, CH = 250;
  lastCols = cols;
  const bl = BLK.map(b => {
    const dots = [], miss = [];
    for (const [c, nm] of Object.entries(b.stocks)) {
      if (S[c]) dots.push({c, n: S[c].n, v: sum(S[c].v, N - win, N)}); else miss.push(nm + '(' + c + ')');
    }
    return {name: b.name, dots, miss, net: dots.reduce((a, d) => a + d.v, 0)};
  });
  const maxAbs = Math.max(1e-9, ...bl.flatMap(b => b.dots.map(d => Math.abs(d.v))));
  const k = 27 / Math.sqrt(maxAbs);
  let maxR = 0;
  for (const b of bl) {
    b.dots.sort((x, y) => Math.abs(y.v) - Math.abs(x.v));
    b.dots.forEach(d => d.r = 3 + k * Math.sqrt(Math.abs(d.v)));
    packDots(b.dots);
    b.R = Math.max(10, ...b.dots.map(d => Math.hypot(d.x, d.y) + d.r)) + 6;
    maxR = Math.max(maxR, b.R);
  }
  const f = Math.min(1, 100 / maxR), rows = Math.ceil(bl.length / cols);
  let g = '';
  bl.forEach((b, i) => {
    const cx = (i % cols) * CW + CW / 2, oy = Math.floor(i / cols) * CH, cy = oy + 112, on = selBlk === b.name;
    g += `<g class="blk" data-b="${esc(b.name)}"><circle cx="${cx}" cy="${cy}" r="${b.R * f}" fill="${b.net >= 0 ? '#fdecea' : '#e6f4ea'}" stroke="${on ? '#222' : '#bbb'}" stroke-width="${on ? 2.5 : 1}"/>`;
    for (const d of b.dots) {
      const x = cx + d.x * f, y = cy + d.y * f, r = d.r * f;
      g += `<circle cx="${x}" cy="${y}" r="${r}" fill="${d.v > 0 ? '#d93025' : d.v < 0 ? '#188038' : '#bbb'}" fill-opacity=".8"><title>${esc(d.n)} ${fmt(d.v)}</title></circle>`;
      if (r >= 11) g += `<text x="${x}" y="${y + 4}" font-size="11" text-anchor="middle" fill="#fff" pointer-events="none">${esc(d.n.slice(0, 4))}</text>`;
    }
    g += `<text x="${cx}" y="${oy + 232}" font-size="14" text-anchor="middle"><tspan font-weight="bold">${esc(b.name)}</tspan><tspan dx="6" fill="${col(b.net)}">${fmt(b.net)}</tspan></text></g>`;
  });
  const miss = bl.flatMap(b => b.miss);
  el.innerHTML = `<svg viewBox="0 0 ${cols * CW} ${rows * CH}" style="width:100%;height:auto">${g}</svg>` +
    `<p class="note">圈圈＝區塊，點＝個股，點的大小代表外資買賣超金額（取開根號，只能粗略比較），紅＝買超、綠＝賣超，圈下數字為區塊合計（億元）。點區塊看明細。</p>` +
    (miss.length ? `<p class="note">資料中找不到、未列入（目前只涵蓋上市股票）：${esc(miss.join('、'))}</p>` : '');
  el.querySelectorAll('.blk').forEach(e => e.onclick = () => { selBlk = selBlk === e.dataset.b ? null : e.dataset.b; renderBlocks(); });
  const sb = bl.find(b => b.name === selBlk);
  if (!sb) { $('blockdetail').innerHTML = ''; return; }
  const ds = sb.dots.slice().sort((x, y) => y.v - x.v);
  $('blockdetail').innerHTML = `<h3 style="font-size:.95rem">${esc(sb.name)}　近 ${win} 日 ${fmt(sb.net)} 億</h3><table>` +
    ds.map(d => `<tr><td>${esc(d.n)} <span class="note">${d.c}</span></td><td style="color:${col(d.v)}">${fmt(d.v)}</td><td><span class="add" data-c="${d.c}" title="加入自選股">＋</span></td></tr>`).join('') + '</table>';
  $('blockdetail').querySelectorAll('.add').forEach(e => e.onclick = () => { addWl(e.dataset.c); e.textContent = '✓'; });
}

const TABS = [['blocks', '區塊圖'], ['news', '新聞'], ['rotation', '輪動'], ['industry', '產業表'], ['watchlist', '自選股']];
function showTab(id) {
  if (!TABS.some(t => t[0] === id)) id = 'blocks';
  $('tabs').innerHTML = TABS.map(t => `<button class="btn ${t[0] === id ? 'on' : ''}" data-t="${t[0]}">${t[1]}</button>`).join('');
  $('tabs').querySelectorAll('button').forEach(b => b.onclick = () => showTab(b.dataset.t));
  TABS.forEach(t => $('t-' + t[0]).classList.toggle('on', t[0] === id));
  $('winbtns').style.display = (id === 'blocks' || id === 'industry') ? '' : 'none';
  try { history.replaceState(null, '', '#' + id); } catch (e) {}
}
function renderNews() {
  const NW = D.news || {};
  const one = (id, arr) => {
    $(id).innerHTML = (arr && arr.length)
      ? '<ul class="news">' + arr.map(n => /^https?:\/\//.test(n.u) ? `<li><a href="${esc(n.u)}" target="_blank" rel="noopener noreferrer">${esc(n.t)}</a><br><span class="note">${esc(n.s)}　${esc(n.d)}</span></li>` : '').join('') + '</ul>'
      : '<p class="note">這次沒有抓到新聞，下次更新會再試。</p>';
  };
  one('news-semi', NW.semi); one('news-fin', NW.fin);
}

function setupTabs() {
  showTab(location.hash.slice(1));
  window.addEventListener('hashchange', () => showTab(location.hash.slice(1)));
  window.addEventListener('resize', () => { if (((window.innerWidth || 800) < 600 ? 2 : 3) !== lastCols) renderBlocks(); });
}

function init() {
  if (!N) { document.body.insertAdjacentHTML('beforeend', '<p>目前沒有資料</p>'); return; }
  $('meta').textContent = `資料日期：${dates[N - 1]}（顯示最近 ${N} 個交易日）`;
  $('stocklist').innerHTML = codes.slice().sort().map(c => `<option value="${c} ${esc(S[c].n)}">`).join('');
  btns('winbtns', [[1, '近1日'], [3, '近3日'], [5, '近5日'], [20, '近20日']], win, v => { win = v; init2(); });
  btns('rangebtns', [[20, '近20日'], [60, '全部']], chartDays, v => { chartDays = v; init2(); });
  $('addbtn').onclick = () => { const c = parseInput($('q').value); if (c) addWl(c); $('q').value = ''; };
  $('q').onkeydown = e => { if (e.key === 'Enter') $('addbtn').click(); };
  renderFlips(); renderChart(); renderInd(); renderWl(); renderBlocks(); renderNews(); setupTabs();
}
function init2() {
  btns('winbtns', [[1, '近1日'], [3, '近3日'], [5, '近5日'], [20, '近20日']], win, v => { win = v; init2(); });
  btns('rangebtns', [[20, '近20日'], [60, '全部']], chartDays, v => { chartDays = v; init2(); });
  renderChart(); renderInd(); renderBlocks();
}
init();
</script></body></html>
"""


def build_html(rows):
    dates = sorted({r["date"] for r in rows})[-KEEP_DAYS:]
    idx = {d: i for i, d in enumerate(dates)}
    stocks = {}
    for r in rows:
        i = idx.get(r["date"])
        if i is None:
            continue
        s = stocks.setdefault(r["code"], {"n": r["name"], "i": INDUSTRY_NAMES.get(r["industry"], r["industry"]), "v": [0] * len(dates)})
        s["v"][i] = round(s["v"][i] + r["net"], 3)
    blocks = [{"name": n, "stocks": s} for n, s in BLOCKS]
    news = fetch_all_news()
    payload = json.dumps({"dates": dates, "stocks": stocks, "blocks": blocks, "news": news}, ensure_ascii=False, separators=(",", ":"))
    payload = payload.replace("</", "<\\/")
    return TEMPLATE.replace("__DATA__", payload)


def main():
    rows = update_data()
    os.makedirs("docs", exist_ok=True)
    with open(OUT_FILE, "w", encoding="utf-8") as f:
        f.write(build_html(rows))
    print("完成，已產生", OUT_FILE)


if __name__ == "__main__":
    main()
