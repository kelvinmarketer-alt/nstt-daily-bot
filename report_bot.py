#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Bot báo cáo hàng ngày — Nông sản Tuấn Tú.
Đọc Google Sheet (public, qua gviz CSV) -> dựng báo cáo -> gửi Telegram.

Nguồn dữ liệu:
  - Công việc:  sheet "Daily Report"
  - Ads (SP + TD): sheet "Báo Cáo Ads"  (1 sheet, nhiều block)

4 mục báo cáo:
  1. Công việc nhân viên trong ngày            (Daily Report)
  2. Hiệu suất Ads SẢN PHẨM trong ngày         (Báo Cáo Ads, block A:H)
  3. Hiệu suất Ads TUYỂN DỤNG trong ngày       (Báo Cáo Ads, block U:AB)
  4. Hiệu suất Ads SẢN PHẨM tháng hiện tại     (Báo Cáo Ads, block K:S)
"""

import os
import csv
import io
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta

SHEET_ID = os.environ.get(
    "SHEET_ID", "1zkiqyJCV88gszPncZgNFhNQRDP6fvhAWaZ5Sgb479_I"
)
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
CHAT_ID = os.environ.get("CHAT_ID", "")

SHEET_TASKS = "Daily Report"
SHEET_ADS = "Báo Cáo Ads"

# Vị trí cột (0-based) trong sheet "Báo Cáo Ads"
# SP theo ngày
SP_NGAY, SP_CHI, SP_SDT, SP_DTHU = 2, 3, 4, 7
# SP theo tháng (block K:S)
M_THANG, M_CHI, M_DTHU, M_SDT, M_SLKH, M_TYLE, M_CPDTHU = 10, 11, 12, 13, 15, 17, 18
# TD theo ngày (block U:AB)
TD_NGAY, TD_CHI, TD_LEAD, TD_CV = 22, 23, 24, 26

VN_TZ = timezone(timedelta(hours=7))


# ---------------------------------------------------------------- helpers
def now_vn():
    return datetime.now(VN_TZ)


def fetch_grid(sheet_name):
    """Đọc toàn bộ 1 tab thành list[list[str]] (đã pad đều cột)."""
    url = (
        f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/gviz/tq"
        f"?tqx=out:csv&sheet={urllib.parse.quote(sheet_name)}"
    )
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        data = r.read().decode("utf-8")
    rows = [[c.strip() for c in row] for row in csv.reader(io.StringIO(data))]
    width = max((len(r) for r in rows), default=0)
    return [r + [""] * (width - len(r)) for r in rows]


def col(row, idx):
    return row[idx] if idx < len(row) else ""


def to_int(s):
    """'699.671 đ' -> 699671 ; '12,00' -> 12 ; '' -> 0"""
    digits = "".join(ch for ch in str(s or "") if ch.isdigit())
    return int(digits) if digits else 0


def vnd(n):
    return f"{n:,.0f} đ".replace(",", ".")


def pct(part, whole):
    return f"{(part / whole * 100):.1f}%" if whole else "—"


def per(total, count):
    return vnd(total // count) if count else "—"


def norm_date(s):
    """Chuẩn hoá ngày về 'dd/mm'. Chấp nhận '12/6', '01/06/2026'."""
    parts = (s or "").strip().split("/")
    if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
        return f"{int(parts[0]):02d}/{int(parts[1]):02d}"
    return (s or "").strip()


# ---------------------------------------------------------------- 1. công việc
def section_tasks(today_ddmm):
    grid = fetch_grid(SHEET_TASKS)
    if not grid:
        return "<b>1️⃣ CÔNG VIỆC NHÂN VIÊN TRONG NGÀY</b>\n• Không đọc được sheet."
    header = grid[0]

    def idx(name):
        for i, h in enumerate(header):
            if h.strip().lower() == name.lower():
                return i
        return -1

    c_ngay = idx("NGÀY")
    c_dv = idx("ĐẦU VIỆC")
    c_mt = idx("MỤC TIÊU SL")
    c_td = idx("THỰC ĐẠT SL")
    c_dv_unit = idx("ĐƠN VỊ")
    c_tiendo = idx("% TIẾN ĐỘ")
    c_kpi = idx("% ĐẠT KPI")
    c_tt = idx("TRẠNG THÁI")

    rows = [r for r in grid[1:] if norm_date(col(r, c_ngay)) == today_ddmm]
    buckets = {"Hoàn thành": 0, "Trễ hạn": 0, "Đang làm": 0, "Chưa bắt đầu": 0}
    lines = []
    for r in rows:
        st = col(r, c_tt)
        key = next((k for k in buckets if k.lower() in st.lower()), None)
        if key:
            buckets[key] += 1
        icon = {"Hoàn thành": "✅", "Đang làm": "🟡",
                "Trễ hạn": "🔴", "Chưa bắt đầu": "⚪"}.get(key, "▫️")
        sl = ""
        if col(r, c_mt) or col(r, c_td):
            sl = f" — {col(r, c_td) or 0}/{col(r, c_mt) or 0} {col(r, c_dv_unit)}".rstrip()
        lines.append(
            f"{icon} <b>{col(r, c_dv)}</b>{sl}  "
            f"<i>(TĐ {col(r, c_tiendo) or '—'} · KPI {col(r, c_kpi) or '—'})</i>"
        )

    out = ["<b>1️⃣ CÔNG VIỆC NHÂN VIÊN TRONG NGÀY</b>"]
    if not rows:
        out.append("• Chưa có dữ liệu nhập cho hôm nay.")
        return "\n".join(out)
    out += lines
    out.append(
        f"\n📊 Tổng <b>{len(rows)}</b> việc — "
        f"✅ {buckets['Hoàn thành']} hoàn thành · 🟡 {buckets['Đang làm']} đang làm · "
        f"🔴 {buckets['Trễ hạn']} trễ hạn · ⚪ {buckets['Chưa bắt đầu']} chưa bắt đầu"
    )
    return "\n".join(out)


# ---------------------------------------------------------------- 2 & 3 & 4 (ads)
def find_daily(grid, col_ngay, today_ddmm):
    """Trả về dòng đầu tiên có Ngày == hôm nay trong block ads."""
    for r in grid[2:]:
        if norm_date(col(r, col_ngay)) == today_ddmm:
            return r
    return None


def section_ads_sp_day(grid, today_ddmm):
    r = find_daily(grid, SP_NGAY, today_ddmm)
    out = ["<b>2️⃣ ADS SẢN PHẨM — TRONG NGÀY</b>"]
    if r is None:
        out.append("• Chưa có dữ liệu nhập cho hôm nay.")
        return "\n".join(out)
    chi, sdt, dthu = to_int(col(r, SP_CHI)), to_int(col(r, SP_SDT)), to_int(col(r, SP_DTHU))
    out.append(f"• Chi tiêu: <b>{vnd(chi)}</b>")
    out.append(f"• Doanh thu: <b>{vnd(dthu)}</b>")
    out.append(f"• Chi tiêu/Doanh thu: <b>{pct(chi, dthu)}</b>")
    out.append(f"• SĐT: <b>{sdt}</b>")
    out.append(f"• Chi tiêu/SĐT: <b>{per(chi, sdt)}</b>")
    return "\n".join(out)


def section_ads_td_day(grid, today_ddmm):
    r = find_daily(grid, TD_NGAY, today_ddmm)
    out = ["<b>3️⃣ ADS TUYỂN DỤNG — TRONG NGÀY</b>"]
    if r is None:
        out.append("• Chưa có dữ liệu nhập cho hôm nay.")
        return "\n".join(out)
    chi, lead, cv = to_int(col(r, TD_CHI)), to_int(col(r, TD_LEAD)), to_int(col(r, TD_CV))
    out.append(f"• Chi tiêu: <b>{vnd(chi)}</b>")
    out.append(f"• Lead: <b>{lead}</b>")
    out.append(f"• Chi phí/Lead: <b>{per(chi, lead)}</b>")
    out.append(f"• CV: <b>{cv}</b>")
    out.append(f"• Chi phí/CV: <b>{per(chi, cv)}</b>")
    return "\n".join(out)


def section_ads_sp_month(grid, month):
    """Block SP theo tháng (K:S). Lấy dòng có Tháng == tháng hiện tại,
    chỉ trong bảng SP đầu tiên (dừng trước header bảng TD)."""
    out = [f"<b>4️⃣ ADS SẢN PHẨM — THÁNG {month}</b>"]
    target = None
    started = False
    for r in grid:
        cell = col(r, M_THANG).strip()
        if cell == "Tháng":          # gặp header bảng tháng
            if started:
                break                # header thứ 2 = bảng TD -> dừng
            started = True
            continue
        if started and cell == str(month):
            target = r
            break
    if target is None:
        out.append("• Chưa có dữ liệu tháng này.")
        return "\n".join(out)

    chi = to_int(col(target, M_CHI))
    dthu = to_int(col(target, M_DTHU))
    slkh = to_int(col(target, M_SLKH))
    tyle = col(target, M_TYLE) or pct(slkh, to_int(col(target, M_SDT)))
    cpdthu = col(target, M_CPDTHU) or pct(chi, dthu)
    out.append(f"• Chi tiêu: <b>{vnd(chi)}</b>")
    out.append(f"• Doanh thu: <b>{vnd(dthu)}</b>")
    out.append(f"• Chi phí/Doanh thu: <b>{cpdthu}</b>")
    out.append(f"• Số khách chốt: <b>{slkh}</b>")
    out.append(f"• Giá trị/khách chốt: <b>{per(dthu, slkh)}</b>")
    out.append(f"• Tỷ lệ chốt: <b>{tyle}</b>")
    return "\n".join(out)


# ---------------------------------------------------------------- send
def send_telegram(text):
    if not BOT_TOKEN or not CHAT_ID:
        print("[!] Thiếu BOT_TOKEN / CHAT_ID — in ra màn hình thay vì gửi:\n")
        print(text)
        return
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = urllib.parse.urlencode({
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": "true",
    }).encode()
    with urllib.request.urlopen(urllib.request.Request(url, data=payload), timeout=60) as r:
        print("Telegram:", r.status)


def main():
    now = now_vn()
    today_ddmm = f"{now.day:02d}/{now.month:02d}"
    ads = fetch_grid(SHEET_ADS)
    header = (
        f"📋 <b>BÁO CÁO HÀNG NGÀY — NÔNG SẢN TUẤN TÚ</b>\n"
        f"🗓 {now.strftime('%d/%m/%Y')} (cuối ngày)\n{'─' * 22}"
    )
    msg = "\n\n".join([
        header,
        section_tasks(today_ddmm),
        section_ads_sp_day(ads, today_ddmm),
        section_ads_td_day(ads, today_ddmm),
        section_ads_sp_month(ads, now.month),
    ])
    send_telegram(msg)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("LỖI:", e, file=sys.stderr)
        sys.exit(1)
