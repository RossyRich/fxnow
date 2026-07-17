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
    {"symbol": "USDJPY", "label": "ドル/円",   "query": "ドル円",   "yahoo": "USDJPY=X", "digits": 2},
    {"symbol": "EURUSD", "label": "ユーロ/ドル", "query": "ユーロドル", "yahoo": "EURUSD=X", "digits": 4},
    {"symbol": "GBPUSD", "label": "ポンド/ドル", "query": "ポンドドル", "yahoo": "GBPUSD=X", "digits": 4},
    {"symbol": "AUDUSD", "label": "豪ドル/ドル", "query": "豪ドル",   "yahoo": "AUDUSD=X", "digits": 4},
]

CAL_CURRENCIES = {"USD", "JPY", "EUR", "GBP", "AUD"}


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


# ---------- 5. 経済指標カレンダー（ForexFactory） ----------

def get_calendar():
    items = []
    try:
        data = json.loads(fetch("https://nfs.faireconomy.media/ff_calendar_thisweek.json"))
        now = datetime.now(JST)
        end = now + timedelta(hours=48)
        for ev in data:
            if ev.get("country") not in CAL_CURRENCIES:
                continue
            if ev.get("impact") not in ("High", "Medium"):
                continue
            try:
                dt = datetime.fromisoformat(ev["date"]).astimezone(JST)
            except Exception:
                continue
            if dt < now - timedelta(hours=3) or dt > end:
                continue
            items.append({
                "ts": dt.timestamp(),
                "time": dt.strftime("%m/%d %H:%M"),
                "country": ev.get("country", ""),
                "title": ev.get("title", ""),
                "impact": ev.get("impact", ""),
                "forecast": ev.get("forecast", ""),
                "previous": ev.get("previous", ""),
            })
        items.sort(key=lambda x: x["ts"])
        for it in items:
            del it["ts"]
        log("calendar: %d items" % len(items))
    except Exception as e:
        log("calendar failed: %r" % e)
    return items[:20]


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


# ---------- 7. Reddit（ベストエフォート） ----------

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
    feed = feed[:60]

    data = {
        "updatedAt": datetime.now(JST).strftime("%Y-%m-%d %H:%M JST"),
        "pairs": pairs_out,
        "posts": feed,
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
    log("done. updatedAt=%s" % data["updatedAt"])


if __name__ == "__main__":
    main()
