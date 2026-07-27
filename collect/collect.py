#!/usr/bin/env python3
"""FXスカウト データ収集スクリプト（標準ライブラリのみ）

収集源:
  1. FXSSI current-ratios API  … 複数ブローカーのポジション比率（買い%）
  2. Yahoo!リアルタイム検索     … Xポスト（通貨ペアごとに検索、買い/売り判定）
  3. みんかぶFX ニュース        … 日本語為替ニュース
  4. ForexLive(investinglive)   … 英語ニュースRSS
  5. ForexFactory カレンダー    … 経済指標（今週分JSON）
  6. Yahoo Finance chart API    … 現在レートと前日比
  7. Reddit r/Forex             … ベストエフォート（403なら黙ってスキップ）

出力: リポジトリ直下の data.js と data.json
"""
import json
import os
import re
import sys
import urllib.request
import urllib.error
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from html import unescape

JST = timezone(timedelta(hours=9))
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

PAIRS = [
    {"symbol": "USDJPY", "label": "ドル/円",   "query": "ドル円",   "digits": 2},
    {"symbol": "EURUSD", "label": "ユーロ/ドル", "query": "ユーロドル", "digits": 4},
    {"symbol": "GBPUSD", "label": "ポンド/ドル", "query": "ポンドドル", "digits": 4},
    {"symbol": "AUDUSD", "label": "豪ドル/ドル", "query": "豪ドル",   "digits": 4},
    {"symbol": "EURJPY", "label": "ユーロ/円",  "query": "ユーロ円",  "digits": 2},
    {"symbol": "GBPJPY", "label": "ポンド/円",  "query": "ポンド円",  "digits": 2},
    {"symbol": "AUDJPY", "label": "豪ドル/円",  "query": "豪ドル円",  "digits": 2},
]

def fetch(url, timeout=25, headers=None):
    h = {"User-Agent": UA, "Accept-Language": "ja,en;q=0.8"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def log(msg):
    print("[collect] " + msg, file=sys.stderr)


# ---------- 1. FXSSI ポジション比率 ----------

def get_broker_ratios():
    """通貨ペアごとの買いポジション%（ブローカー加重平均）を返す"""
    out = {}
    try:
        d = json.loads(fetch("https://c.fxssi.com/api/current-ratios"))
        weights = d.get("broker_weights", {})
        brokers = d.get("brokers", {})
        for p in PAIRS:
            sym = p["symbol"]
            num = den = 0.0
            for broker, pairs in brokers.items():
                v = pairs.get(sym)
                if v is None:
                    continue
                try:
                    v = float(v)
                except ValueError:
                    continue
                w = float(weights.get(broker, 1))
                num += v * w
                den += w
            if den > 0:
                out[sym] = round(num / den, 1)
        log("FXSSI ratios: %s" % out)
    except Exception as e:
        log("FXSSI failed: %r" % e)
    return out


# ---------- 2. Yahoo!リアルタイム検索（Xポスト） ----------

BUY_WORDS = ["ロング", "買い", "買った", "買って", "押し目", "上目線", "上昇",
             "強気", "ロンガー", "買いエントリー", "買い増し", "L入", "上値",
             "上抜け", "買いポジ"]
SELL_WORDS = ["ショート", "売り", "売った", "売って", "戻り売り", "下目線",
              "下落", "弱気", "ショーター", "売りエントリー", "S入", "下値",
              "下抜け", "売りポジ"]
SPAM_WORDS = ["公式LINE", "プレゼント", "無料で受け取", "登録はこちら", "配信中",
              "サロン", "間違いない", "副業", "億り人", "勝率9", "モニター募集",
              "この人の投稿", "さんの投稿", "感謝しかない", "利確できるように",
              "教えてもら", "DMで", "プロフから"]
# 別ジャンルの銘柄名を羅列する宣伝ポスト検出用
OFFTOPIC_WORDS = ["ナスダック", "イーサリアム", "ビットコイン", "日経先物",
                  "日経平均", "FANG", "時価総額", "米国株", "アドバンテスト",
                  "信用取引", "レバレッジ", "韓国市場", "Gold", "ゴールド"]


def classify(text):
    if any(w in text for w in SPAM_WORDS):
        return "spam"
    if text.count("#") >= 6:
        return "spam"
    if sum(1 for w in OFFTOPIC_WORDS if w in text) >= 3:
        return "spam"
    buy = sum(text.count(w) for w in BUY_WORDS)
    sell = sum(text.count(w) for w in SELL_WORDS)
    if buy > sell:
        return "buy"
    if sell > buy:
        return "sell"
    return "neutral"


def get_x_posts(pair):
    """Yahoo!リアルタイム検索からXポストを取得して判定"""
    posts = []
    try:
        url = ("https://search.yahoo.co.jp/realtime/search?p=" +
               urllib.parse.quote(pair["query"]))
        html = fetch(url)
        m = re.search(
            r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
            html, re.S)
        if not m:
            log("yahoo rt: NEXT_DATA not found for %s" % pair["symbol"])
            return posts
        d = json.loads(m.group(1))
        entries = (d.get("props", {}).get("pageProps", {}).get("pageData", {})
                   .get("timeline", {}).get("entry", []))
        for e in entries:
            text = e.get("displayText") or ""
            # 検索語ハイライトのマーカーを除去
            text = text.replace("\tSTART\t", "").replace("\tEND\t", "")
            text = re.sub(r"\s+", " ", unescape(text)).strip()
            if not text:
                continue
            stance = classify(text)
            if stance == "spam":
                continue
            posts.append({
                "pair": pair["symbol"],
                "text": text[:300],
                "name": (e.get("name") or "")[:30],
                "screenName": e.get("screenName") or "",
                "url": (e.get("url") or "").split("?")[0],
                "time": int(e.get("createdAt") or 0),
                "stance": stance,
            })
        log("yahoo rt %s: %d posts" % (pair["symbol"], len(posts)))
    except Exception as e:
        log("yahoo rt failed for %s: %r" % (pair["symbol"], e))
    return posts


# ---------- 3. みんかぶFX ニュース ----------

def get_minkabu_news():
    items = []
    try:
        html = fetch("https://fx.minkabu.jp/news")
        blocks = re.findall(
            r'<a title="([^"]+)"[^>]*href="(/news/\d+)">.*?fc-sub">([^<]+)<',
            html, re.S)
        seen = set()
        for title, path, when in blocks:
            if path in seen:
                continue
            seen.add(path)
            items.append({
                "title": unescape(title).strip(),
                "url": "https://fx.minkabu.jp" + path,
                "time": when.strip(),
            })
        log("minkabu: %d items" % len(items))
    except Exception as e:
        log("minkabu failed: %r" % e)
    return items[:15]


# ---------- 4. ForexLive 英語ニュースRSS ----------

def get_forexlive():
    items = []
    try:
        xml_text = fetch("https://www.forexlive.com/feed/news")
        root = ET.fromstring(xml_text)
        for item in root.iter("item"):
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            pub = (item.findtext("pubDate") or "").strip()
            when = ""
            try:
                dt = datetime.strptime(pub[:25], "%a, %d %b %Y %H:%M:%S")
                # pubDateはGMT表記 → JSTへ
                dt = dt.replace(tzinfo=timezone.utc).astimezone(JST)
                when = dt.strftime("%m/%d %H:%M")
            except Exception:
                pass
            if title and link:
                items.append({"title": unescape(title), "url": link, "time": when})
            if len(items) >= 12:
                break
        log("forexlive: %d items" % len(items))
    except Exception as e:
        log("forexlive failed: %r" % e)
    return items


# ---------- 5. 経済指標カレンダー（外為どっとコム・本日分） ----------
# みんかぶは結果の反映が数時間遅れるため、発表数分で結果が入る外為どっとコムのAPIに変更(2026-07-20)

GAITAME_COUNTRY = {"JPY": "日", "USD": "米", "EUR": "欧", "GBP": "英", "AUD": "豪",
                   "NZD": "NZ", "CAD": "加", "CHF": "スイス", "DEM": "独", "GER": "独",
                   "FRF": "仏", "CNH": "中", "CNY": "中", "TRY": "トルコ",
                   "ZAR": "南ア", "HKD": "香港", "SGD": "シンガ", "KRW": "韓",
                   "INR": "印", "MXN": "メキシコ", "BRL": "ブラジル", "RUB": "露",
                   "NOK": "ノルウェー", "SEK": "スウェーデン", "PLZ": "ポーランド"}


def get_calendar():
    """外為どっとコムの経済指標APIから本日分を取得（日本語・結果は発表後すぐ反映）"""
    items = []
    try:
        today = datetime.now(JST).strftime("%Y%m%d")
        d = json.loads(fetch(
            "https://navi.gaitame.com/v3/info/indicators/calendar?from=%s&to=%s"
            % (today, today),
            headers={"Accept": "application/json",
                     "Referer": "https://www.gaitame.com/"}))
        for ev in d.get("data", []):
            try:
                imp = int(ev.get("importance") or 0)
            except ValueError:
                imp = 0
            prev = ev.get("last") or ""
            if ev.get("change"):
                prev += "（%s）" % ev["change"]   # 前回の修正値
            code = ev.get("country") or ""
            items.append({
                "time": ev.get("time") or "—",
                "country": GAITAME_COUNTRY.get(code, code),
                "title": ev.get("subject") or "",
                "stars": imp + 1,   # API: 0=低 1=中 2=高 → ★1/★2/★3
                "previous": prev,
                "forecast": ev.get("estimate") or "",
                "result": ev.get("result") or "",
            })
        log("calendar(gaitame): %d items" % len(items))
    except Exception as e:
        log("calendar failed: %r" % e)
    return items[:40]


# ---------- 6. 現在レート（GMOコイン 外国為替FX公開API） ----------

GMO_BASE = "https://forex-api.coin.z.com/public/v1"


def get_prices():
    """全ペアの現在レートと前日比を返す {symbol: {...}}"""
    out = {}
    try:
        tick = json.loads(fetch(GMO_BASE + "/ticker"))
        bids = {t["symbol"].replace("_", ""): float(t["bid"])
                for t in tick.get("data", [])}
    except Exception as e:
        log("gmo ticker failed: %r" % e)
        return out
    year = datetime.now(JST).strftime("%Y")
    for p in PAIRS:
        sym = p["symbol"]
        bid = bids.get(sym)
        if bid is None:
            continue
        prev = None
        try:
            gmo_sym = sym[:3] + "_" + sym[3:]
            k = json.loads(fetch(
                "%s/klines?symbol=%s&priceType=BID&interval=1day&date=%s"
                % (GMO_BASE, gmo_sym, year)))
            candles = k.get("data") or []
            # 最終行は当日の進行中ローソク → その1本前の終値が前日終値
            if len(candles) >= 2:
                prev = float(candles[-2]["close"])
        except Exception as e:
            log("gmo klines failed for %s: %r" % (sym, e))
        fmt = "%%.%df" % p["digits"]
        item = {"price": fmt % bid}
        if prev:
            item["change"] = ("%+" + fmt[1:]) % (bid - prev)
            item["changePct"] = "%+.2f%%" % ((bid - prev) / prev * 100)
        out[sym] = item
    log("prices: %s" % {k: v.get("price") for k, v in out.items()})
    return out


# ---------- 6b. 通貨指数（ドル指数・円指数を日足から自前計算） ----------
# 対象通貨に対する幾何平均で「その通貨の総合的な強さ」を出し、期初=100に正規化する。
# 株価指数と違い外部の有料データに依存しないので自由に公開できる。

IDX_DAYS = 90          # 表示する日数
IDX_BASKET_JPY = ["USD_JPY", "EUR_JPY", "GBP_JPY", "AUD_JPY",
                  "NZD_JPY", "CAD_JPY", "CHF_JPY"]
# ドル指数用: (ペア, USDが分子ならTrue)
IDX_BASKET_USD = [("USD_JPY", True), ("EUR_USD", False), ("GBP_USD", False),
                  ("AUD_USD", False), ("NZD_USD", False),
                  ("USD_CAD", True), ("USD_CHF", True)]


def _gmo_daily(sym, year):
    """{日付(YYYY-MM-DD): 終値} を返す"""
    out = {}
    k = json.loads(fetch("%s/klines?symbol=%s&priceType=BID&interval=1day&date=%s"
                         % (GMO_BASE, sym, year)))
    for c in k.get("data") or []:
        d = datetime.fromtimestamp(int(c["openTime"]) / 1000, JST).strftime("%Y-%m-%d")
        out[d] = float(c["close"])
    return out


def get_currency_index():
    """ドル指数・円指数の日足シリーズを返す"""
    year = datetime.now(JST).strftime("%Y")
    need = ["USD_JPY", "EUR_JPY", "GBP_JPY", "AUD_JPY", "NZD_JPY",
            "CAD_JPY", "CHF_JPY", "EUR_USD", "GBP_USD", "AUD_USD", "NZD_USD"]
    series = {}
    try:
        for s in need:
            series[s] = _gmo_daily(s, year)
        # クロスレートを合成（GMOに無い USD/CAD・USD/CHF）
        for base in ("CAD", "CHF"):
            src, dst = series[base + "_JPY"], {}
            for d, v in series["USD_JPY"].items():
                if d in src and src[d]:
                    dst[d] = v / src[d]
            series["USD_" + base] = dst
    except Exception as e:
        log("currency index failed: %r" % e)
        return None

    dates = sorted(set.intersection(*[set(series[s]) for s in series]))[-IDX_DAYS:]
    if len(dates) < 10:
        log("currency index: not enough days (%d)" % len(dates))
        return None

    def build(items):
        vals = []
        for d in dates:
            prod = 1.0
            for sym, numer in items:
                r = series[sym][d]
                prod *= r if numer else (1.0 / r)
            vals.append(prod ** (1.0 / len(items)))
        base = vals[0]
        return [round(v / base * 100, 2) for v in vals]

    usd = build(IDX_BASKET_USD)
    # 円指数は「円が分子」＝各クロス円の逆数
    jpy = build([(s, False) for s in IDX_BASKET_JPY])
    out = {
        "dates": dates,
        "usd": usd,
        "jpy": jpy,
        "usdChange": round(usd[-1] - usd[-2], 2) if len(usd) > 1 else 0,
        "jpyChange": round(jpy[-1] - jpy[-2], 2) if len(jpy) > 1 else 0,
    }
    log("currency index: %d days, USD=%.2f JPY=%.2f" % (len(dates), usd[-1], jpy[-1]))
    return out


# ---------- 7. OANDAオープンオーダー ----------

OO_BINS = 12      # 現値の上下それぞれのビン数
OO_RANGE = 0.02   # 表示レンジ = 現値±2%


def get_open_orders():
    """OANDAオーダーブックAPIから現値±2%の指値注文ヒストグラムを取得
    （X-OANDA-WIDGET-APIヘッダーが必須。widget.oanda.jpの公開ウィジェットと同じ経路）"""
    out = {}
    for p in PAIRS:
        try:
            inst = p["symbol"][:3] + "_" + p["symbol"][3:]
            d = json.loads(fetch(
                "https://widget.oanda.jp/api/order-book?instrument=%s&ago=0" % inst,
                headers={"X-OANDA-WIDGET-API": "order-book",
                         "Accept": "application/json"}))
            ob = d.get("orderBook") or {}
            price = float(ob.get("price") or 0)
            if not price:
                continue
            step = price * OO_RANGE / OO_BINS
            sells = [0.0] * (OO_BINS * 2)   # idx 0 = 最上位ビン（現値+2%側）
            buys = [0.0] * (OO_BINS * 2)
            for b in ob.get("buckets", []):
                bp = float(b.get("price") or 0)
                offset = bp - price
                idx = OO_BINS - 1 - int(offset // step)
                if 0 <= idx < OO_BINS * 2:
                    sells[idx] += float(b.get("shortCountPercent") or 0)
                    buys[idx] += float(b.get("longCountPercent") or 0)
            total = sum(sells) + sum(buys)
            if total <= 0:
                continue
            fmt = "%%.%df" % p["digits"]
            bins = []
            for i in range(OO_BINS * 2):
                # ビン中心のレートをラベルに、量は±2%内の注文全体に占める%に正規化
                center = price + (OO_BINS - 1 - i + 0.5) * step
                bins.append({"p": fmt % center,
                             "s": round(sells[i] / total * 100, 1),
                             "b": round(buys[i] / total * 100, 1)})
            snap = ob.get("lastupdate") or ob.get("snapshot") or ""
            m = re.search(r"(\d+)月(\d+)日\s*(\d+)時(\d+)分", snap)
            if m:
                snap = "%s/%s %s:%02d" % (m.group(1), m.group(2),
                                          int(m.group(3)), int(m.group(4)))
            out[p["symbol"]] = {
                "bins": bins,
                "price": fmt % price,
                "snapshot": snap,
            }
        except Exception as e:
            log("oanda orders failed %s: %r" % (p["symbol"], e))
    log("oanda orders: %s" % sorted(out.keys()))
    return out


# ---------- 8. Reddit（ベストエフォート） ----------

def get_reddit():
    items = []
    try:
        d = json.loads(fetch("https://www.reddit.com/r/Forex/hot.json?limit=15",
                             headers={"User-Agent": "fx-scout/1.0"}))
        for c in d["data"]["children"]:
            p = c["data"]
            if p.get("stickied"):
                continue
            items.append({
                "title": p.get("title", ""),
                "url": "https://www.reddit.com" + p.get("permalink", ""),
                "time": datetime.fromtimestamp(
                    p.get("created_utc", 0), JST).strftime("%m/%d %H:%M"),
                "ups": p.get("ups", 0),
            })
            if len(items) >= 10:
                break
        log("reddit: %d items" % len(items))
    except Exception as e:
        log("reddit skipped: %r" % e)
    return items


# ---------- main ----------

def main():
    ratios = get_broker_ratios()
    prices = get_prices()
    orders = get_open_orders()

    all_posts = []
    pairs_out = []
    for p in PAIRS:
        posts = get_x_posts(p)
        all_posts.extend(posts)
        buy = sum(1 for x in posts if x["stance"] == "buy")
        sell = sum(1 for x in posts if x["stance"] == "sell")
        neutral = sum(1 for x in posts if x["stance"] == "neutral")
        long_pct = ratios.get(p["symbol"])
        price = prices.get(p["symbol"])
        pairs_out.append({
            "symbol": p["symbol"],
            "label": p["label"],
            "sns": {"buy": buy, "sell": sell, "neutral": neutral},
            "broker": ({"long": long_pct, "short": round(100 - long_pct, 1)}
                       if long_pct is not None else None),
            "price": price,
            "orders": orders.get(p["symbol"]),
        })

    # 投稿フィードは新しい順・重複URL除去
    seen = set()
    feed = []
    for x in sorted(all_posts, key=lambda v: -v["time"]):
        if x["url"] in seen:
            continue
        seen.add(x["url"])
        x = dict(x)
        x["timeStr"] = datetime.fromtimestamp(x["time"], JST).strftime("%m/%d %H:%M")
        feed.append(x)
    feed = feed[:90]

    data = {
        "updatedAt": datetime.now(JST).strftime("%Y-%m-%d %H:%M JST"),
        "pairs": pairs_out,
        "posts": feed,
        "ccyIndex": get_currency_index(),
        "newsJp": get_minkabu_news(),
        "newsEn": get_forexlive(),
        "reddit": get_reddit(),
        "calendar": get_calendar(),
    }

    js = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    with open(os.path.join(ROOT, "data.json"), "w", encoding="utf-8") as f:
        f.write(js)
    with open(os.path.join(ROOT, "data.js"), "w", encoding="utf-8") as f:
        f.write("window.FX_DATA=" + js + ";")

    # 指標発表の3分前〜発表後20分（結果未反映の間）は hot.txt=1
    # （ワークフローがこれを見て収集間隔を60秒に短縮する）
    # 会見・演説など数値結果が出ないイベントは対象外
    no_result_words = ("会見", "演説", "講演", "証言", "発言", "休場")
    hot = "0"
    now = datetime.now(JST)
    for e in data["calendar"]:
        t = e.get("time") or ""
        if ":" not in t or e.get("result"):
            continue
        if any(w in (e.get("title") or "") for w in no_result_words):
            continue
        try:
            hh, mm = t.split(":")
            at = now.replace(hour=int(hh), minute=int(mm), second=0, microsecond=0)
        except ValueError:
            continue
        if timedelta(minutes=-3) <= now - at <= timedelta(minutes=20):
            hot = "1"
            break
    with open(os.path.join(ROOT, "hot.txt"), "w") as f:
        f.write(hot)
    log("done. updatedAt=%s hot=%s" % (data["updatedAt"], hot))


if __name__ == "__main__":
    main()
