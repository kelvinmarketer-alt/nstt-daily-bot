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


def _collect_phones(tok, since, until):
    """Tập SĐT (đã chuẩn hoá) xuất hiện trong [since, until]. Chia cửa sổ ≤25 ngày."""
    pid = _page_id(tok)
    phones = set()
    seg_start = since
    while seg_start <= until:
        seg_end = min(seg_start + _CHUNK, until)
        pn = 1
        while pn <= 400:  # chặn vô hạn
            js = None
            for attempt in range(3):  # retry chống timeout lẻ
                try:
                    r = requests.get(f"{BASE}/{pid}/conversations", params={
                        "page_access_token": tok, "since": seg_start, "until": seg_end,
                        "page_number": pn,
                    }, timeout=90)
                    js = r.json()
                    break
                except Exception as e:
                    print(f"[pancake] trang {pn} lỗi (lần {attempt + 1}): {e}")
            if js is None:
                break
            convs = js.get("conversations", [])
            if not convs:
                break
            for c in convs:
                if c.get("has_phone") or c.get("recent_phone_numbers"):
                    for ph in (c.get("recent_phone_numbers") or []):
                        num = _norm_phone(ph.get("phone_number") or ph.get("captured"))
                        if len(num) >= 9:
                            phones.add(num)
            pn += 1
        seg_start = seg_end + 1
    return phones


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
    for num in _collect_phones(tok, since, until):
        seen.add(_hash(num))
    state[key] = sorted(seen)
    state[day_key] = upto_iso
    print(f"[pancake] baseline {key}: +{len(seen) - before} (tổng {len(seen)}) tới {upto_iso}")


def _count_new(state, key, tok, day):
    """Số SĐT mới trong NGÀY day = SĐT không có trong baseline. KHÔNG sửa baseline."""
    if not tok:
        return 0
    seen = set(state.get(key, []))
    s, u = _day_bounds(day)
    return sum(1 for num in _collect_phones(tok, s, u) if _hash(num) not in seen)


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
    fb_new = _count_new(state, "pk_seen_fb", FB_TOKEN, target)
    zalo_new = _count_new(state, "pk_seen_zalo", ZALO_TOKEN, target)
    print(f"[pancake {target.strftime('%d/%m')}] SĐT mới: FB={fb_new} | Zalo={zalo_new}")
    if write:
        try:
            _write_row(target, fb_new, zalo_new)
        except Exception as e:
            print("[pancake] lỗi ghi sheet:", e)
    key = f"{target.year}-{target.month:02d}-{target.day:02d}"
    state.setdefault("pk_daily", {})[key] = {"fb": fb_new, "zalo": zalo_new}
    return {"fb": fb_new, "zalo": zalo_new}


if __name__ == "__main__":
    import argparse
    import ssl
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="dd/mm/yyyy")
    ap.add_argument("--no-write", action="store_true")
    ap.add_argument("--local-ssl", action="store_true", help="bỏ verify SSL khi test máy")
    a = ap.parse_args()
    if a.local_ssl:
        ssl._create_default_https_context = ssl._create_unverified_context
    tgt = (datetime.strptime(a.date, "%d/%m/%Y").replace(tzinfo=VN_TZ)
           if a.date else datetime.now(VN_TZ) - timedelta(days=1))
    st = {}
    print(run(tgt, st, write=not a.no_write))
