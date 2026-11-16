import gzip
import os
import re
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

# 公開されている日本向けXMLTVソース候補（地上波が取れるまで順に試行）
SOURCES = [
    "https://animenosekai.github.io/japanterebi-xmltv/guide.xml",
    "https://epgshare01.online/epgshare01/epg_ripper_JP1.xml.gz",
    "https://free-epg.de/api/epg/jp.xml.gz",
    "https://iptv-epg.org/files/epg-jp.xml.gz",
]

# 地上波チャンネル定義（マッチパターン -> GAS側が必ず認識する日本語局名）
TARGET_CHANNELS = [
    ("NHK総合",     r"(nhk.?g|nhk.?general|joak|nhk総合|ＮＨＫ総合)"),
    ("NHK Eテレ",   r"(nhk.?e|educational|joab|eテレ|ＮＨＫＥテレ)"),
    ("日本テレビ",   r"(nippon.?tv|ntv|joax|日本テレビ|日テレ)"),
    ("テレビ朝日",   r"(tv.?asahi|tvasahi|joex|テレビ朝日|テレ朝)"),
    ("TBS",         r"(^tbs|\btbs\b|tbs\.jp|jorx|tbsテレビ|ｔｂｓ)"),
    ("テレビ東京",   r"(tv.?tokyo|tvtokyo|jotx|テレビ東京|テレ東)"),
    ("フジテレビ",   r"(fuji.?tv|fujitv|jocx|フジテレビ|フジ)"),
    ("TOKYO MX",    r"(tokyo.?mx|tokyomx|jomx|ｔｏｋｙｏ)"),
    ("MBS毎日放送", r"(mbs|mainichi|jooy|毎日放送)"),
    ("ABCテレビ",   r"(abc.?tv|asahi.?broadcasting|jonr|朝日放送)"),
    ("カンテレ",    r"(kansai|ktv|jodx|関西テレビ|カンテレ)"),
    ("読売テレビ",  r"(yomiuri|ytv|joix|読売テレビ)"),
    ("テレビ大阪",  r"(tv.?osaka|tvo|jobh|テレビ大阪)"),
]

def fetch_xml_bytes(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; MyTvEpgBuilder/1.1)"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        raw = resp.read()
    if raw[:2] == b"\x1f\x8b" or url.endswith(".gz"):
        return gzip.decompress(raw)
    return raw

def match_standard_name(text: str):
    norm = text.lower().strip()
    for std_name, pattern in TARGET_CHANNELS:
        if re.search(pattern, norm):
            return std_name
    return None

def build_from_xml(xml_bytes: bytes):
    root = ET.fromstring(xml_bytes)
    jst = timezone(timedelta(hours=9))
    now_jst = datetime.now(jst)
    min_dt = (now_jst - timedelta(days=1)).strftime("%Y%m%d000000")
    max_dt = (now_jst + timedelta(days=4)).strftime("%Y%m%d000000")

    id_to_std = {}
    kept_channels = {}

    for ch in root.findall("channel"):
        cid = ch.get("id", "")
        names = [dn.text or "" for dn in ch.findall("display-name")]
        combined = cid + " " + " ".join(names)
        std_name = match_standard_name(combined)
        if std_name:
            id_to_std[cid] = std_name
            if std_name not in kept_channels:
                new_ch = ET.Element("channel", id=std_name)
                dn = ET.SubElement(new_ch, "display-name", lang="ja")
                dn.text = std_name
                kept_channels[std_name] = new_ch

    out_root = ET.Element("tv", {"generator-info-name": "MyGasTvEpgLite"})
    for ch_el in kept_channels.values():
        out_root.append(ch_el)

    prog_count = 0
    for pg in root.findall("programme"):
        cid = pg.get("channel", "")
        std_name = id_to_std.get(cid)
        if not std_name:
            continue
        start_str = (pg.get("start", "") or "")[:14]
        if start_str and (start_str < min_dt or start_str > max_dt):
            continue
        pg.set("channel", std_name)
        out_root.append(pg)
        prog_count += 1

    return out_root, len(kept_channels), prog_count

def main():
    best_result = None
    for url in SOURCES:
        try:
            print(f"Trying: {url}")
            xml_bytes = fetch_xml_bytes(url)
            if len(xml_bytes) < 2000:
                continue
            out_root, ch_cnt, pg_cnt = build_from_xml(xml_bytes)
            print(f" -> Matched channels={ch_cnt}, programs={pg_cnt}")
            if ch_cnt >= 3 and pg_cnt > 0:
                best_result = (out_root, ch_cnt, pg_cnt, url)
                break
        except Exception as e:
            print(f" -> Skip {url}: {e}")

    if not best_result:
        raise RuntimeError("地上波番組データを含むEPGソースを取得できませんでした")

    out_root, ch_cnt, pg_cnt, used_url = best_result
    os.makedirs("dist", exist_ok=True)
    xml_out = ET.tostring(out_root, encoding="utf-8", xml_declaration=True)
    with gzip.open("dist/guide.xml.gz", "wb") as f:
        f.write(xml_out)
    with open("dist/index.html", "w", encoding="utf-8") as f:
        f.write(
            f"<h1>TV EPG Feed Ready</h1>"
            f"<p>Source: {used_url}</p>"
            f"<p>Channels: {ch_cnt}, Programs: {pg_cnt}</p>"
            f"<a href='guide.xml.gz'>guide.xml.gz</a>"
        )
    print(f"Success! Source={used_url}, Channels={ch_cnt}, Programs={pg_cnt}, Size={os.path.getsize('dist/guide.xml.gz')//1024} KB")

if __name__ == "__main__":
    main()
