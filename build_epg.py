import gzip
import os
import re
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

# 複数ソースから不足チャンネルを自動補完する優先順
SOURCES = [
    "https://raw.githubusercontent.com/dbghelp/JCOM-TV-EPG/refs/heads/main/jcom.xml",
    "https://animenosekai.github.io/japanterebi-xmltv/guide.xml",
    "https://epgshare01.online/epgshare01/epg_ripper_JP1.xml.gz",
    "https://epgshare01.online/epgshare01/epg_ripper_JP2.xml.gz",
    "https://free-epg.de/api/epg/jp.xml.gz",
]

# BS・CS・専門チャンネルを確実に弾く除外フィルタ
EXCLUDE_RE = re.compile(
    r"(bs日テレ|bs朝日|bs-tbs|bs\s*tbs|bsテレ東|bsフジ|bsよしもと|bs10|bs11|bs12|"
    r"nhk\s*bs|bsp|bs4k|news24|日テレnews|tbs\s*news|テレ朝チャンネル|日テレプラス|"
    r"フジテレビ(one|two|next)|時代劇|wowow|スカパー|j\s*sports|animax|at-x|"
    r"ショップ|通販|ワンセグ|mx\s*2|mx2|サブ)",
    re.IGNORECASE,
)

# 地上波チャンネル判定パターン（上から順に判定）
TARGET_CHANNELS = [
    ("NHK総合",     r"(nhk.?g([^a-z]|$)|nhk.?general|joak|nhk総合|ｎｈｋ総合|nhk東京.*総合)"),
    ("NHK Eテレ",   r"(nhk.?e([^a-z]|$)|nhk.?educational|joab|eテレ|ＮＨＫＥテレ|nhk.*教育)"),
    ("日本テレビ",   r"(nippon.?tv|^ntv([^a-z]|$)|\bntv\b|ntv\.jp|joax|日本テレビ|日テレ)"),
    ("テレビ朝日",   r"(tv.?asahi|tvasahi|joex|テレビ朝日|テレ朝)"),
    ("TBS",         r"(^tbs([^a-z]|$)|\btbs\b|tbs\.jp|jorx|tbsテレビ|ｔｂｓ)"),
    ("テレビ東京",   r"(tv.?tokyo|tvtokyo|jotx|テレビ東京|テレ東)"),
    ("フジテレビ",   r"(fuji.?tv|fujitv|jocx|フジテレビ|フジ)"),
    ("TOKYO MX",    r"(tokyo.?mx|tokyomx|jomx|ｔｏｋｙｏ\s*ｍｘ)"),
    ("MBS毎日放送", r"(^mbs|\bmbs\b|mainichi|jooy|毎日放送)"),
    ("ABCテレビ",   r"(abc.?tv|asahi.?broadcasting|jonr|朝日放送)"),
    ("カンテレ",    r"(kansai|ktv|jodx|関西テレビ|カンテレ)"),
    ("読売テレビ",  r"(yomiuri|ytv|joix|読売テレビ)"),
    ("テレビ大阪",  r"(tv.?osaka|tvo|jobh|テレビ大阪)"),
]

def fetch_xml_bytes(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; MyTvEpgBuilder/2.0)"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        raw = resp.read()
    if raw[:2] == b"\x1f\x8b" or url.endswith(".gz"):
        raw = gzip.decompress(raw)
    # XMLとして不正な制御文字を除去
    return re.sub(rb"[\x00-\x08\x0b\x0c\x0e-\x1f]", b"", raw)

def match_standard_name(text: str):
    norm = text.lower().strip()
    if EXCLUDE_RE.search(norm):
        return None
    for std_name, pattern in TARGET_CHANNELS:
        if re.search(pattern, norm):
            return std_name
    return None

def extract_channels_from_xml(xml_bytes: bytes, min_dt: str, max_dt: str):
    root = ET.fromstring(xml_bytes)
    id_to_std = {}
    for ch in root.findall("channel"):
        cid = ch.get("id", "")
        names = [dn.text or "" for dn in ch.findall("display-name")]
        std_name = match_standard_name(cid + " " + " ".join(names))
        if std_name:
            id_to_std[cid] = std_name

    per_channel_progs = {}
    for pg in root.findall("programme"):
        cid = pg.get("channel", "")
        std_name = id_to_std.get(cid)
        if not std_name:
            continue
        start_str = (pg.get("start", "") or "")[:14]
        if start_str and (start_str < min_dt or start_str > max_dt):
            continue
        pg.set("channel", std_name)
        per_channel_progs.setdefault(std_name, []).append(pg)

    return per_channel_progs

def main():
    jst = timezone(timedelta(hours=9))
    now_jst = datetime.now(jst)
    min_dt = (now_jst - timedelta(days=1)).strftime("%Y%m%d000000")
    max_dt = (now_jst + timedelta(days=4)).strftime("%Y%m%d000000")

    final_programs = {}  # std_name -> list of <programme>
    source_logs = []

    for url in SOURCES:
        try:
            print(f"Fetching: {url}")
            xml_bytes = fetch_xml_bytes(url)
            if len(xml_bytes) < 2000:
                continue
            extracted = extract_channels_from_xml(xml_bytes, min_dt, max_dt)
            added_chs = []
            for ch_name, progs in extracted.items():
                # まだ番組が確保できていない局だけを補完採用（重複混在を防止）
                if ch_name not in final_programs and len(progs) >= 3:
                    final_programs[ch_name] = progs
                    added_chs.append(f"{ch_name}({len(progs)})")
            source_logs.append(f"{url}: 採用 [{', '.join(added_chs) if added_chs else '新規なし'}]")
            print(f" -> Adopted: {added_chs}")
            # 東京主要8局がすべて揃ったら早期終了
            if all(k in final_programs for k in ["日本テレビ", "テレビ朝日", "TBS", "テレビ東京", "フジテレビ", "TOKYO MX"]):
                break
        except Exception as e:
            source_logs.append(f"{url}: ERROR {e}")
            print(f" -> Skip {url}: {e}")

    out_root = ET.Element("tv", {"generator-info-name": "MyGasTvEpgMerged"})
    total_progs = 0
    summary_rows = []
    for ch_name, _ in TARGET_CHANNELS:
        if ch_name not in final_programs:
            continue
        ch_el = ET.Element("channel", id=ch_name)
        dn = ET.SubElement(ch_el, "display-name", lang="ja")
        dn.text = ch_name
        out_root.append(ch_el)
        progs = final_programs[ch_name]
        for pg in progs:
            out_root.append(pg)
        total_progs += len(progs)
        summary_rows.append(f"<li><strong>{ch_name}</strong>: {len(progs)} 番組</li>")

    os.makedirs("dist", exist_ok=True)
    xml_out = ET.tostring(out_root, encoding="utf-8", xml_declaration=True)
    # 圧縮版(.xml.gz)と非圧縮版(.xml)の両方を出力
    with open("dist/guide.xml", "wb") as f:
        f.write(xml_out)
    with gzip.open("dist/guide.xml.gz", "wb") as f:
        f.write(xml_out)

    with open("dist/index.html", "w", encoding="utf-8") as f:
        f.write(
            f"<meta charset='utf-8'><h1>TV EPG Feed Ready</h1>"
            f"<p>Updated (JST): {now_jst.strftime('%Y-%m-%d %H:%M:%S')}</p>"
            f"<p>Total Channels: {len(final_programs)}, Total Programs: {total_progs}</p>"
            f"<ul>{''.join(summary_rows)}</ul>"
            f"<p><a href='guide.xml'>guide.xml</a> | <a href='guide.xml.gz'>guide.xml.gz</a></p>"
            f"<pre>{chr(10).join(source_logs)}</pre>"
        )
    print(f"Done! Channels={len(final_programs)}, Programs={total_progs}")

if __name__ == "__main__":
    main()
