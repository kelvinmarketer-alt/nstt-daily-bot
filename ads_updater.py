#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Tự kéo CHI TIÊU + SỐ TIN NHẮN ngày hôm trước từ Meta (2 tài khoản SP/TD)
rồi ghi vào tab "Báo Cáo Ads" (upsert theo ngày). Cột được dò theo TÊN HEADER
(row 2) nên không vỡ khi sheet bị chèn/dịch cột.

Chỉ auto-điền: SP -> Chi tiêu ngày ; TD -> Chi Tiêu + Lead(số tin nhắn).
Các cột kết quả kinh doanh (SDT, SLKH, Doanh thu, CV...) để NHẬP TAY.
"""
import os
import sys
import json
import argparse
from datetime import datetime, timedelta, timezone

import requests
import gspread
from google.oauth2.service_account import Credentials

ADS_SHEET_ID = os.environ.get("ADS_SHEET_ID", "1zkiqyJCV88gszPncZgNFhNQRDP6fvhAWaZ5Sgb479_I")
SHEET_ADS = "Báo Cáo Ads"
SP_ACCOUNT = os.environ.get("ADS_SP_ACCOUNT", "act_1998481310677042")   # TKQC 2 (Sản phẩm)
TD_ACCOUNT = os.environ.get("ADS_TD_ACCOUNT", "act_1945007079637982")   # TKQC 3 (Tuyển dụng)
META_TOKEN = os.environ.get("META_TOKEN", "")
GRAPH = "https://graph.facebook.com/v21.0"
MSG_METRIC = "onsite_conversion.messaging_conversation_started_7d"      # số tin nhắn
VN_TZ = timezone(timedelta(hours=7))
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


def gclient():
    info = os.environ.get("GOOGLE_SA_JSON")
    if info:
        creds = Credentials.from_service_account_info(json.loads(info), scopes=SCOPES)
    else:  # chạy máy: dùng file gsa.json cạnh đây
        creds = Credentials.from_service_account_file(
            os.environ.get("GSA_FILE", "gsa.json"), scopes=SCOPES)
    return gspread.authorize(creds)


def meta_day(account, since_iso):
    """(spend_int, messages_int) cho 1 ngày."""
    r = requests.get(f"{GRAPH}/{account}/insights", params={
        "fields": "spend,actions",
        "time_range": json.dumps({"since": since_iso, "until": since_iso}),
        "access_token": META_TOKEN,
    }, timeout=60)
    data = r.json().get("data", [])
    if not data:
        return 0, 0
    d = data[0]
    spend = round(float(d.get("spend", 0) or 0))
    msg = 0
    for a in d.get("actions", []):
        if a.get("action_type") == MSG_METRIC:
            msg = int(float(a.get("value", 0)))
    return spend, msg


def _norm(s):
    """'28/8' -> '28/8' ; '08/08/2026' -> '8/8' (bỏ số 0 đầu, bỏ năm)."""
    p = (s or "").strip().split("/")
    if len(p) >= 2 and p[0].strip().isdigit() and p[1].strip().isdigit():
        return f"{int(p[0])}/{int(p[1])}"
    return (s or "").strip()


def resolve_cols(header):
    """Dò cột theo tên trong dòng header (row 2)."""
    def find(name):
        for i, h in enumerate(header):
            if h.strip() == name:
                return i
        return -1
    ngay = [i for i, h in enumerate(header) if h.strip() in ("Ngày", "Ngày ")]
    thang = [i for i, h in enumerate(header) if h.strip() == "Tháng"]
    if not ngay:
        raise RuntimeError("Không tìm thấy cột 'Ngày' trong Báo Cáo Ads")
    return {
        "sp_thang": min(thang) if thang else 0,
        "sp_ngay": min(ngay),
        "sp_chi": find("Chi tiêu ngày"),
        "td_thang": max(thang) if thang else 23,
        "td_ngay": max(ngay),
        "td_chi": find("Chi Tiêu"),
        "td_lead": find("Lead"),
    }


def _dated_rows(vals, col):
    return [i for i, r in enumerate(vals)
            if len(r) > col and r[col].strip() and "/" in r[col] and "gày" not in r[col]]


def run(target=None, dry=False):
    now = datetime.now(VN_TZ)
    day = (now - timedelta(days=1)) if target is None else target
    since = day.strftime("%Y-%m-%d")
    ddmm = f"{day.day}/{day.month}"
    month = day.month

    sp_spend, sp_msg = meta_day(SP_ACCOUNT, since)
    td_spend, td_msg = meta_day(TD_ACCOUNT, since)
    print(f"[Meta {since}] SP chi={sp_spend} msg={sp_msg} | TD chi={td_spend} msg={td_msg}")

    gc = gclient()
    ws = gc.open_by_key(ADS_SHEET_ID).worksheet(SHEET_ADS)
    vals = ws.get_all_values()
    c = resolve_cols(vals[1])
    print("[cols]", c)

    # tìm dòng đã có ngày này (ở block SP hoặc TD); nếu chưa có -> dòng mới sau dòng có ngày cuối
    def find_date_row(col):
        for i in _dated_rows(vals, col):
            if _norm(vals[i][col]) == ddmm:
                return i
        return None
    row = find_date_row(c["sp_ngay"])
    if row is None:
        row = find_date_row(c["td_ngay"])
    if row is None:
        last = max((_dated_rows(vals, c["sp_ngay"]) or [1])[-1],
                   (_dated_rows(vals, c["td_ngay"]) or [1])[-1])
        row = last + 1
    rownum = row + 1  # 1-based

    # ô cần ghi (col_index_0based, value) — chỉ chi tiêu + tin nhắn + tháng + ngày
    cells = [
        (c["sp_thang"], month), (c["sp_ngay"], ddmm), (c["sp_chi"], sp_spend),
        (c["td_thang"], month), (c["td_ngay"], ddmm), (c["td_chi"], td_spend),
        (c["td_lead"], td_msg),
    ]
    plan = [(gspread.utils.rowcol_to_a1(rownum, ci + 1), v) for ci, v in cells if ci >= 0]
    print(f"[ghi] dòng {rownum} ({ddmm}):", plan)
    if dry:
        print("(dry-run, không ghi)")
        return
    ws.batch_update([{"range": a1, "values": [[v]]} for a1, v in plan],
                    value_input_option="USER_ENTERED")
    print("Đã ghi xong.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--date", help="dd/mm/yyyy để test 1 ngày cụ thể")
    a = ap.parse_args()
    tgt = datetime.strptime(a.date, "%d/%m/%Y").replace(tzinfo=VN_TZ) if a.date else None
    run(target=tgt, dry=a.dry)
