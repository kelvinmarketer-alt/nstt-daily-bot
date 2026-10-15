#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Kéo SỐ ĐIỆN THOẠI MỚI trong ngày từ Pancake (pages.fm) cho 2 nguồn:
  - FB  (page quảng cáo)  -> ghi vào cột "SDT" khối SP trong "Báo Cáo Ads"
  - Zalo (nguồn tự nhiên) -> trả về để báo cáo Telegram (mục "nguồn số tự nhiên")

"SĐT mới trong ngày" = số điện thoại LẦN ĐẦU xuất hiện trong ngày (đã trừ các SĐT
đã thấy ở ngày trước). Để nhớ SĐT đã thấy, lưu HASH (không lưu số thật) trong state.json.

API: https://pages.fm/api/public_api/v1/pages/{page_id}/conversations
     ?page_access_token=...&since=<unix>&until=<unix>&page_number=<n>
Mỗi hội thoại có has_phone + recent_phone_numbers[].phone_number.
LƯU Ý: since/until của v1 lọc theo NGÀY TẠO hội thoại -> khách cũ nhắn lại (hội thoại tạo
từ trước) KHÔNG hiện. Pancake (giao diện) đếm theo hội thoại CÓ HOẠT ĐỘNG trong kỳ -> lấy thêm
danh sách v2 (mới cập nhật trước, lùi bằng last_conversation_id) để đếm đủ như Pancake.

4 dòng SĐT trong báo cáo (15/10/2026, mỗi kênh đếm 1 SĐT 1 lần trong kỳ):
  (1) SĐT mới lần đầu  = SĐT page FB trong kỳ, CHƯA từng thấy trước kỳ (từ BACKFILL_SINCE)
  (2) SĐT khách cũ     = SĐT page FB trong kỳ, ĐÃ thấy trước kỳ (khách cũ nhắn lại)
  (3) SĐT Zalo         = mọi SĐT kênh Zalo trong kỳ (mới + cũ), tách riêng
  (4) Tổng FB + Zalo   = (1) + (2) + (3)  -> khớp số SĐT Pancake cùng kỳ (2 kênh cộng lại)
"""
import os
import json
import base64
import hashlib
from datetime import datetime, timedelta, timezone

import requests
import gspread
from google.oauth2.service_account import Credentials

VN_TZ = timezone(timedelta(hours=7))
BASE = "https://pages.fm/api/public_api/v1/pages"
ADS_SHEET_ID = os.environ.get("ADS_SHEET_ID", "1zkiqyJCV88gszPncZgNFhNQRDP6fvhAWaZ5Sgb479_I")
SHEET_ADS = "Báo Cáo Ads"

FB_TOKEN = os.environ.get("PANCAKE_FB_TOKEN", "")
ZALO_TOKEN = os.environ.get("PANCAKE_ZALO_TOKEN", "")
# Nạp lịch sử SĐT trước ngày báo cáo (1 lần) để chỉ đếm SĐT THỰC SỰ mới về sau.
BACKFILL_SINCE = os.environ.get("PANCAKE_BACKFILL_SINCE", "2026-06-01")
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


def _page_id(tok):
    p = tok.split(".")[1]
    p += "=" * (-len(p) % 4)
    return json.loads(base64.urlsafe_b64decode(p))["id"]


def _norm_phone(s):
    d = "".join(ch for ch in str(s or "") if ch.isdigit())
    # bỏ +84 -> 0 để dedup nhất quán
    if d.startswith("84") and len(d) >= 11:
        d = "0" + d[2:]
    return d


def _hash(num):
    return hashlib.sha1(num.encode()).hexdigest()[:16]


def _day_bounds(day):
    s = datetime(day.year, day.month, day.day, 0, 0, 0, tzinfo=VN_TZ)
    e = s + timedelta(days=1) - timedelta(seconds=1)
    return int(s.timestamp()), int(e.timestamp())


_CHUNK = 25 * 86400  # API pages.fm trả rỗng nếu khoảng since/until quá rộng (>~1 tháng) -> chia nhỏ


def _api(tok, ver, params):
    """GET danh sách hội thoại (retry chống timeout lẻ). Trả JSON hoặc None."""
    for attempt in range(3):
        try:
            r = requests.get(f"{BASE.replace('/v1/', f'/v{ver}/')}/{_page_id(tok)}/conversations",
                             params={"page_access_token": tok, **params}, timeout=90)
            return r.json()
        except Exception as e:
            print(f"[pancake] v{ver} {params.get('page_number') or ''} lỗi (lần {attempt + 1}): {e}")
    return None


def _phones_of(c):
    out = set()
    if c.get("has_phone") or c.get("recent_phone_numbers"):
        for ph in (c.get("recent_phone_numbers") or []):
            num = _norm_phone(ph.get("phone_number") or ph.get("captured"))
            if len(num) >= 9:
                out.add(num)
    return out


def _collect_phones(tok, since, until):
    """Tập SĐT (đã chuẩn hoá) của hội thoại TẠO trong [since, until]. Chia cửa sổ ≤25 ngày."""
    phones = set()
    seg_start = since
    while seg_start <= until:
        seg_end = min(seg_start + _CHUNK, until)
        pn = 1
        while pn <= 400:  # chặn vô hạn
            js = _api(tok, 1, {"since": seg_start, "until": seg_end, "page_number": pn})
            if js is None:
                break
            convs = js.get("conversations", [])
            if not convs:
                break
            for c in convs:
                phones |= _phones_of(c)
            pn += 1
        seg_start = seg_end + 1
    return phones


def _ts(s):
    """'2026-10-09T03:05:11.691' (UTC, có/không Z) -> unix."""
    s = str(s or "").replace("Z", "")[:19]
    try:
        return int(datetime.strptime(s, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc).timestamp())
    except ValueError:
        return 0


def _collect_active_phones(tok, since, until):
    """Tập SĐT của hội thoại CÓ HOẠT ĐỘNG (cập nhật) trong [since, until] — gồm khách cũ nhắn lại.
    v2 trả hội thoại mới cập nhật trước -> lùi trang tới khi cũ hơn `since`."""
    phones, last = set(), None
    for _ in range(300):  # chặn vô hạn (60 hội thoại/trang)
        js = _api(tok, 2, {"last_conversation_id": last} if last else {})
        convs = (js or {}).get("conversations") or []
        if not convs:
            if js is None or js.get("success") is False:
                raise RuntimeError(f"Pancake v2: {(js or {}).get('message', 'không đọc được hội thoại')}")
            break
        for c in convs:
            if since <= _ts(c.get("updated_at")) <= until:
                phones |= _phones_of(c)
        last = convs[-1].get("id")
        if _ts(convs[-1].get("updated_at")) < since or len(convs) < 20:
            break
    return phones


def phones_in(tok, since, until):
    """Mọi SĐT 1 kênh trong kỳ = hội thoại tạo trong kỳ ∪ hội thoại có hoạt động trong kỳ."""
    return _collect_phones(tok, since, until) | _collect_active_phones(tok, since, until)


def split_period(tok, since, until, seen_hashes=None):
    """{'new','old','all'} của 1 kênh trong [since, until]. seen_hashes = HASH SĐT đã thấy TRƯỚC kỳ;
    None -> tự tải từ BACKFILL_SINCE tới trước kỳ (dùng cho báo cáo tuần/tháng/đối chiếu)."""
    if not tok:
        return {"new": 0, "old": 0, "all": 0}
    if seen_hashes is None:
        start = int(datetime.strptime(BACKFILL_SINCE, "%Y-%m-%d").replace(tzinfo=VN_TZ).timestamp())
        seen_hashes = {_hash(n) for n in _collect_phones(tok, start, since - 1)} if since > start else set()
    ph = phones_in(tok, since, until)
    old = sum(1 for n in ph if _hash(n) in seen_hashes)
    return {"new": len(ph) - old, "old": old, "all": len(ph)}


def period(start_day, end_day):
    """4 dòng SĐT cho kỳ [start_day, end_day] (ngày VN) — đọc thẳng Pancake, không dùng state."""
    s, u = _day_bounds(start_day)[0], _day_bounds(end_day)[1]
    fb, zl = split_period(FB_TOKEN, s, u), split_period(ZALO_TOKEN, s, u)
    return _four(fb, zl)


def _four(fb, zl):
    return {"fb": fb["new"], "fb_old": fb["old"], "fb_all": fb["all"],
            "zalo": zl["new"], "zalo_all": zl["all"], "total": fb["all"] + zl["all"]}


def lines(pk):
    """4 dòng hiển thị (HTML Telegram) + chú thích định nghĩa. pk = kết quả run()/period()."""
    return [
        f"• (1) SĐT mới lần đầu (FB): <b>{pk.get('fb', 0)}</b>",
        f"• (2) SĐT khách cũ nhắn lại (FB): <b>{pk.get('fb_old', 0)}</b>",
        f"• (3) SĐT Zalo: <b>{pk.get('zalo_all', 0)}</b> (trong đó mới {pk.get('zalo', 0)})",
        f"• (4) Tổng SĐT FB + Zalo: <b>{pk.get('total', 0)}</b> ← khớp Pancake",
        "<i>(1) số lần đầu nhắn page FB · (2) số đã từng nhắn trước kỳ, nay nhắn lại · "
        "(3) mọi số kênh Zalo · (4) = 1+2+3, mỗi kênh 1 số tính 1 lần</i>",
    ]


def _extend_baseline(state, key, tok, upto_day):
    """Đảm bảo baseline (SĐT đã thấy) phủ HẾT tới ngày `upto_day` (bao gồm).
    Baseline chỉ tiến, KHÔNG chứa SĐT của ngày báo cáo -> chạy lại cùng ngày cho kết quả y hệt."""
    day_key = key + "_day"
    bl_day = state.get(day_key)  # ISO 'YYYY-MM-DD' đã phủ tới
    upto_iso = upto_day.strftime("%Y-%m-%d")
    if bl_day and bl_day >= upto_iso:
        return  # đã phủ đủ -> không tải lại (idempotent)
    if bl_day:
        prev = datetime.strptime(bl_day, "%Y-%m-%d").replace(tzinfo=VN_TZ)
        since = _day_bounds(prev + timedelta(days=1))[0]
    else:
        since = int(datetime.strptime(BACKFILL_SINCE, "%Y-%m-%d")
                    .replace(tzinfo=VN_TZ).timestamp())
    until = _day_bounds(upto_day)[1]
    seen = set(state.get(key, []))
    before = len(seen)
    # gồm cả hội thoại cũ có hoạt động -> SĐT đã đếm "mới" hôm qua không bị đếm mới lần nữa
    # (chỉ lùi tối đa 7 ngày cho phần hoạt động — baseline thường chỉ thiếu 1 ngày)
    for num in _collect_phones(tok, since, until) | _collect_active_phones(
            tok, max(since, until - 7 * 86400), until):
        seen.add(_hash(num))
    state[key] = sorted(seen)
    state[day_key] = upto_iso
    print(f"[pancake] baseline {key}: +{len(seen) - before} (tổng {len(seen)}) tới {upto_iso}")


def _count_day(state, key, tok, day):
    """{'new','old','all'} trong NGÀY day so với baseline (SĐT đã thấy trước ngày). KHÔNG sửa baseline."""
    s, u = _day_bounds(day)
    return split_period(tok, s, u, set(state.get(key, [])))


def _gclient():
    info = os.environ.get("GOOGLE_SA_JSON")
    if info:
        creds = Credentials.from_service_account_info(json.loads(info), scopes=SCOPES)
    else:
        creds = Credentials.from_service_account_file(
            os.environ.get("GSA_FILE", "gsa.json"), scopes=SCOPES)
    return gspread.authorize(creds)


def _norm_ddmm(s):
    p = (s or "").strip().split("/")
    if len(p) >= 2 and p[0].strip().isdigit() and p[1].strip().isdigit():
        return f"{int(p[0])}/{int(p[1])}"
    return (s or "").strip()


ZALO_COL = 9          # cột J (nằm giữa khối SP và khối tháng, an toàn không đụng công thức)
ZALO_HEADER = "SĐT Zalo"


def _write_row(day, fb_new, zalo_new):
    """Ghi SĐT mới FB -> cột 'SDT' (khối SP) và SĐT Zalo -> cột 'SĐT Zalo' (J), đúng dòng ngày."""
    gc = _gclient()
    ws = gc.open_by_key(ADS_SHEET_ID).worksheet(SHEET_ADS)
    vals = ws.get_all_values()
    hdr = 0
    for i, r in enumerate(vals[:8]):
        if any((x or "").strip() == "Chi tiêu ngày" for x in r):
            hdr = i
            break
    h = vals[hdr]
    col_ngay = next((i for i, x in enumerate(h) if (x or "").strip() in ("Ngày", "Ngày ")), 2)
    col_sdt = next((i for i, x in enumerate(h) if (x or "").strip() == "SDT"), -1)
    col_zalo = next((i for i, x in enumerate(h) if (x or "").strip() == ZALO_HEADER), -1)
    updates = []
    if col_zalo < 0:  # chưa có cột Zalo -> tạo header ở cột J
        col_zalo = ZALO_COL
        updates.append({"range": gspread.utils.rowcol_to_a1(hdr + 1, col_zalo + 1),
                        "values": [[ZALO_HEADER]]})
    ddmm = f"{day.day}/{day.month}"
    row = None
    for i, r in enumerate(vals):
        if col_ngay < len(r) and "/" in r[col_ngay] and _norm_ddmm(r[col_ngay]) == ddmm:
            row = i
            break
    if row is None:
        print(f"[pancake] chưa có dòng ngày {ddmm} -> bỏ ghi")
        return
    if col_sdt >= 0:
        updates.append({"range": gspread.utils.rowcol_to_a1(row + 1, col_sdt + 1),
                        "values": [[fb_new]]})
    updates.append({"range": gspread.utils.rowcol_to_a1(row + 1, col_zalo + 1),
                    "values": [[zalo_new]]})
    ws.batch_update(updates, value_input_option="USER_ENTERED")
    print(f"[pancake] ghi dòng {row + 1} ({ddmm}): SDT(FB)={fb_new} | Zalo={zalo_new}")


def run(target, state, write=True):
    """Trả về {'fb':n, 'zalo':n} hoặc None nếu chưa cấu hình token."""
    if not FB_TOKEN and not ZALO_TOKEN:
        return None
    day_before = target - timedelta(days=1)
    if FB_TOKEN:
        _extend_baseline(state, "pk_seen_fb", FB_TOKEN, day_before)
    if ZALO_TOKEN:
        _extend_baseline(state, "pk_seen_zalo", ZALO_TOKEN, day_before)
    pk = _four(_count_day(state, "pk_seen_fb", FB_TOKEN, target),
               _count_day(state, "pk_seen_zalo", ZALO_TOKEN, target))
    print(f"[pancake {target.strftime('%d/%m')}] SĐT FB mới={pk['fb']} cũ={pk['fb_old']} | "
          f"Zalo={pk['zalo_all']} (mới {pk['zalo']}) | tổng={pk['total']}")
    if write:  # sheet giữ như cũ: cột SDT = SĐT mới FB, cột 'SĐT Zalo' = SĐT mới Zalo
        try:
            _write_row(target, pk["fb"], pk["zalo"])
        except Exception as e:
            print("[pancake] lỗi ghi sheet:", e)
    key = f"{target.year}-{target.month:02d}-{target.day:02d}"
    state.setdefault("pk_daily", {})[key] = pk
    return pk


if __name__ == "__main__":
    import argparse
    import ssl
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="dd/mm/yyyy")
    ap.add_argument("--to", help="dd/mm/yyyy — có thì in 4 dòng SĐT cho kỳ --date..--to (đối chiếu Pancake, không ghi gì)")
    ap.add_argument("--no-write", action="store_true")
    ap.add_argument("--local-ssl", action="store_true", help="bỏ verify SSL khi test máy")
    a = ap.parse_args()
    if a.local_ssl:
        ssl._create_default_https_context = ssl._create_unverified_context
    tgt = (datetime.strptime(a.date, "%d/%m/%Y").replace(tzinfo=VN_TZ)
           if a.date else datetime.now(VN_TZ) - timedelta(days=1))
    if a.to:
        pk = period(tgt, datetime.strptime(a.to, "%d/%m/%Y").replace(tzinfo=VN_TZ))
        print(pk)
        print("\n".join(lines(pk)))
    else:
        st = {}
        print(run(tgt, st, write=not a.no_write))
