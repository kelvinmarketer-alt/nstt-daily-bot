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
import json
import hashlib
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta, date

SHEET_ID = os.environ.get(
    "SHEET_ID", "1zkiqyJCV88gszPncZgNFhNQRDP6fvhAWaZ5Sgb479_I"
)
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
CHAT_ID = os.environ.get("CHAT_ID", "")

SHEET_TASKS = "Daily Report"
SHEET_ADS = "Báo Cáo Ads"
STATE_FILE = os.environ.get("STATE_FILE", "state.json")

# Vị trí cột (0-based) trong sheet "Báo Cáo Ads"
# SP theo ngày
SP_NGAY, SP_CHI, SP_SDT, SP_DTHU = 2, 3, 4, 7
# SP theo tháng (block K:S)
M_THANG, M_CHI, M_DTHU, M_SDT, M_SLKH, M_TYLE, M_CPDTHU = 10, 11, 12, 13, 15, 17, 18
# TD theo ngày (block U:AB)
TD_NGAY, TD_CHI, TD_LEAD, TD_CV = 22, 23, 24, 26
# TD theo tháng (bảng thứ 2, dùng chung cột K:N): Tổng chi, CV, $/CV
TDM_CHI, TDM_CV, TDM_CPCV = 11, 12, 13

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


def parse_date(s, default_year):
    """'dd/mm/yyyy' hoặc 'dd/mm' -> datetime.date (thiếu năm dùng default_year)."""
    parts = (s or "").strip().split("/")
    if len(parts) < 2 or not (parts[0].isdigit() and parts[1].isdigit()):
        return None
    y = int(parts[2]) if len(parts) >= 3 and parts[2].isdigit() else default_year
    try:
        return date(y, int(parts[1]), int(parts[0]))
    except ValueError:
        return None


def _is_done(st):
    s = (st or "").lower()
    return "hoàn thành" in s or "tạm dừng" in s


def _task_cols(grid):
    """Map tên cột -> chỉ số, dùng chung cho mọi hàm xử lý công việc."""
    header = grid[0] if grid else []

    def idx(name):
        for i, h in enumerate(header):
            if h.strip().lower() == name.lower():
                return i
        return -1

    return {k: idx(v) for k, v in {
        "ngay": "NGÀY", "dv": "ĐẦU VIỆC", "mt": "MỤC TIÊU SL", "td": "THỰC ĐẠT SL",
        "unit": "ĐƠN VỊ", "tiendo": "% TIẾN ĐỘ", "kpi": "% ĐẠT KPI",
        "tt": "TRẠNG THÁI", "deadline": "DEADLINE",
    }.items()}


def _row_complete(r, c):
    """Việc coi như XONG nếu: TRẠNG THÁI Hoàn thành/Tạm dừng, hoặc thực đạt >= mục tiêu,
    hoặc % tiến độ >= 100 (lọc việc lặp ngày bị bỏ trống ô trạng thái)."""
    if _is_done(col(r, c["tt"])):
        return True
    mt, td = to_int(col(r, c["mt"])), to_int(col(r, c["td"]))
    if mt and td >= mt:
        return True
    return to_int(col(r, c["tiendo"])) >= 100


def _task_key(r, c):
    return f"{norm_date(col(r, c['ngay']))}|{col(r, c['dv']).strip()}"


def _fmt_task(r, c, show_deadline=False):
    st = col(r, c["tt"])
    icon = {"hoàn thành": "✅", "đang làm": "🟡", "trễ": "🔴",
            "chưa bắt đầu": "⚪", "tạm dừng": "⏸"}
    if _row_complete(r, c):
        ic = "✅"
    else:
        ic = next((v for k, v in icon.items() if k in st.lower()), "▫️")
    sl = ""
    if col(r, c["mt"]) or col(r, c["td"]):
        sl = f" — {col(r, c['td']) or 0}/{col(r, c['mt']) or 0} {col(r, c['unit'])}".rstrip()
    hạn = ""
    if show_deadline and col(r, c["deadline"]):
        hạn = f" · hạn {norm_date(col(r, c['deadline']))}"
    return (f"{ic} <b>{col(r, c['dv'])}</b>{sl}  "
            f"<i>(TĐ {col(r, c['tiendo']) or '—'} · KPI {col(r, c['kpi']) or '—'}{hạn})</i>")


# ---------------------------------------------------------------- 1. công việc
def section_tasks(day, grid=None):
    """Trả về (body_text, số_việc) cho ngày `day`. Tách 4 nhóm:
    ✅ Hoàn thành hôm nay (việc nhập hôm nay đã xong), 🟡 Đang làm hôm nay,
    🔄 Đang làm tiếp (việc cũ chưa xong còn hạn), 🔴 Quá hạn chưa xong."""
    if grid is None:
        grid = fetch_grid(SHEET_TASKS)
    if not grid:
        return ("• Không đọc được sheet.", 0)
    c = _task_cols(grid)
    target_ddmm = f"{day.day:02d}/{day.month:02d}"
    target_date = day if isinstance(day, date) and not isinstance(day, datetime) else day.date()

    today_done, today_ongoing, ongoing, overdue = [], [], [], []
    for r in grid[1:]:
        ng = norm_date(col(r, c["ngay"]))
        if not ng:
            continue
        done = _row_complete(r, c)
        if ng == target_ddmm:                  # việc nhập đúng hôm nay
            (today_done if done else today_ongoing).append(r)
            continue
        if done:                               # ngày khác & đã xong -> bỏ
            continue
        dl = parse_date(col(r, c["deadline"]), target_date.year)
        (ongoing if (dl is None or dl >= target_date) else overdue).append(r)

    done_lines = [_fmt_task(r, c) for r in today_done]
    total = len(done_lines) + len(today_ongoing) + len(ongoing) + len(overdue)
    if total == 0:
        return ("• Chưa có dữ liệu nhập.", 0)

    parts = []
    if done_lines:
        parts.append("✅ <b>Hoàn thành hôm nay</b>\n" + "\n".join(done_lines))
    if today_ongoing:
        parts.append("🟡 <b>Đang làm hôm nay</b>\n" +
                     "\n".join(_fmt_task(r, c) for r in today_ongoing))
    if ongoing:
        parts.append("🔄 <b>Đang làm tiếp (từ trước)</b>\n" +
                     "\n".join(_fmt_task(r, c, show_deadline=True) for r in ongoing))
    if overdue:
        parts.append("🔴 <b>Quá hạn chưa xong</b>\n" +
                     "\n".join(_fmt_task(r, c, show_deadline=True) for r in overdue))
    summary = (
        f"📊 Tổng <b>{total}</b> việc — ✅ {len(done_lines)} hoàn thành · "
        f"🟡 {len(today_ongoing) + len(ongoing)} đang làm · 🔴 {len(overdue)} quá hạn"
    )
    return ("\n\n".join(parts) + "\n\n" + summary, total)


def _detect_newly_done(grid, state, now):
    """Phát hiện việc KÉO DÀI vừa chuyển sang hoàn thành (so với lần chạy trước) để
    báo 1 lần dưới nhóm ✅. Cập nhật state['ongoing_seen'] và state['done_reported']."""
    if not grid:
        return []
    c = _task_cols(grid)
    today_ddmm = f"{now.day:02d}/{now.month:02d}"
    seen = state.get("ongoing_seen", {})          # việc bot đã thấy 'đang làm'
    done_rep = state.setdefault("done_reported", {})
    cur_ongoing, announce = {}, []
    for r in grid[1:]:
        ng = norm_date(col(r, c["ngay"]))
        if not ng:
            continue
        key = _task_key(r, c)
        if _row_complete(r, c):
            if key not in done_rep and (key in seen or ng == today_ddmm):
                done_rep[key] = today_ddmm        # đánh dấu đã xử lý
                if key in seen and ng != today_ddmm:
                    announce.append(r)            # việc cũ đang làm -> vừa xong
        else:
            cur_ongoing[key] = 1
    state["ongoing_seen"] = cur_ongoing
    return announce


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


def _month_rows(grid, month):
    """Các dòng có cột Tháng == month & có Tổng chi. Thứ tự: [0]=bảng SP, [1]=bảng TD."""
    return [r for r in grid
            if col(r, M_THANG).strip() == str(month) and col(r, M_CHI).strip()]


def section_ads_sp_month(grid, month):
    """Bảng SP theo tháng (bảng đầu, cột K:S)."""
    out = [f"<b>4️⃣ ADS SẢN PHẨM — THÁNG {month}</b>"]
    rows = _month_rows(grid, month)
    if not rows:
        out.append("• Chưa có dữ liệu tháng này.")
        return "\n".join(out)
    target = rows[0]
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


def section_ads_td_month(grid, month):
    """Bảng TD theo tháng (bảng thứ 2, cột K:N)."""
    out = [f"<b>5️⃣ ADS TUYỂN DỤNG — THÁNG {month}</b>"]
    rows = _month_rows(grid, month)
    if len(rows) < 2:
        out.append("• Chưa có dữ liệu tháng này.")
        return "\n".join(out)
    target = rows[1]
    chi = to_int(col(target, TDM_CHI))
    cv = to_int(col(target, TDM_CV))
    cpcv = col(target, TDM_CPCV) or per(chi, cv)
    out.append(f"• Chi tiêu: <b>{vnd(chi)}</b>")
    out.append(f"• CV nhận: <b>{cv}</b>")
    out.append(f"• Chi phí/CV: <b>{cpcv}</b>")
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


# ---------------------------------------------------------------- state
def load_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


WORK_LOOKBACK = 4   # số ngày quét lùi để gửi bù các ngày bị bỏ sót


def _send_work_day(day, grid, state, is_today, remind_today):
    """Xử lý 1 ngày trong vùng quét."""
    ddmm = f"{day.day:02d}/{day.month:02d}"
    key = f"{day.year}-{ddmm}"
    entry = state.get(key, {})
    seen = key in state               # bot đã từng theo dõi ngày này chưa
    body, count = section_tasks(day, grid)

    if count == 0:
        # Chỉ nhắc cho đúng HÔM NAY buổi tối; không nhắc ngày cũ.
        if is_today and remind_today and not entry.get("reminded"):
            send_telegram(
                f"⚠️ <b>NHẮC NHẬP BÁO CÁO CÔNG VIỆC</b>\n"
                f"🗓 Ngày {day.strftime('%d/%m/%Y')} chưa có đầu việc nào trong "
                f"sheet <i>Daily Report</i>.\nNhân viên vui lòng cập nhật."
            )
            entry["reminded"] = True
            state[key] = entry
        return

    h = hashlib.md5(body.encode("utf-8")).hexdigest()
    if entry.get("hash") == h:
        return                        # đã gửi đúng nội dung này rồi
    # Ngày CŨ mà bot chưa từng theo dõi -> bỏ qua, tránh dump lịch sử khi mới deploy.
    if not is_today and not seen:
        return
    # Ngày CŨ đã từng gửi báo cáo -> không re-gửi bản cập nhật (thay đổi đã có ở 🎉 / báo cáo hôm nay).
    if not is_today and "hash" in entry:
        return

    if "hash" in entry:
        tag = " (🔄 CẬP NHẬT)"         # đã gửi trước đó, giờ NV sửa
    elif not is_today:
        tag = " (⏰ GỬI BÙ)"           # ngày cũ từng bị bỏ sót, giờ mới có dữ liệu
    else:
        tag = ""                      # báo cáo bình thường trong ngày
    header = (
        f"🧑‍💻 <b>BÁO CÁO CÔNG VIỆC{tag} — NÔNG SẢN TUẤN TÚ</b>\n"
        f"🗓 {day.strftime('%d/%m/%Y')}\n{'─' * 22}"
    )
    send_telegram(header + "\n\n" + body)
    entry["hash"] = h
    state[key] = entry


def process_work(now, remind_today):
    """Quét lùi WORK_LOOKBACK ngày. Ngày bị bỏ sót (đã nhắc) rồi NV nhập sau
    -> GỬI BÙ đúng ngày đó. Ngày cũ chưa từng theo dõi -> bỏ qua.
    remind_today=True (buổi tối) -> hôm nay trống thì nhắc 1 lần."""
    grid = fetch_grid(SHEET_TASKS)
    state = load_state()
    newly_done = _detect_newly_done(grid, state, now)  # việc kéo dài vừa hoàn thành
    if newly_done:                                     # báo riêng 1 lần, không đụng dedup ngày
        c = _task_cols(grid)
        lines = "\n".join(_fmt_task(r, c, show_deadline=True) for r in newly_done)
        send_telegram(
            f"🎉 <b>VIỆC VỪA HOÀN THÀNH</b> (việc kéo dài nhiều ngày)\n{'─' * 22}\n{lines}"
        )
    for offset in range(WORK_LOOKBACK, -1, -1):       # cũ -> mới
        day = now - timedelta(days=offset)
        is_today = (offset == 0)
        # nếu hôm nay vừa có việc kéo dài hoàn thành thì không nhắc 'trống' nữa
        rt = remind_today and not (is_today and newly_done)
        _send_work_day(day, grid, state, is_today, rt)
    save_state(state)


def process_ads(target):
    """Báo cáo ADS cho ngày `target` (datetime), chống gửi trùng + đánh dấu cập nhật.
    Gửi 9h sáng và chạy lại vài mốc; chỉ gửi lại khi số liệu đổi (chốt trễ / sửa)."""
    ddmm = f"{target.day:02d}/{target.month:02d}"
    ads = fetch_grid(SHEET_ADS)
    body = "\n\n".join([
        section_ads_sp_day(ads, ddmm),
        section_ads_td_day(ads, ddmm),
        section_ads_sp_month(ads, target.month),
        section_ads_td_month(ads, target.month),
    ])
    state = load_state()
    key = f"ads-{target.year}-{ddmm}"
    entry = state.get(key, {})

    h = hashlib.md5(body.encode("utf-8")).hexdigest()
    if entry.get("hash") == h:
        print(f"[{key}] Nội dung không đổi -> bỏ qua")
        return

    tag = " (🔄 CẬP NHẬT)" if "hash" in entry else ""
    header = (
        f"📊 <b>BÁO CÁO ADS{tag} — NÔNG SẢN TUẤN TÚ</b>\n"
        f"🗓 Số liệu ngày {target.strftime('%d/%m/%Y')}\n{'─' * 22}"
    )
    send_telegram(header + "\n\n" + body)
    entry["hash"] = h
    state[key] = entry
    save_state(state)


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "work"
    if mode == "ads":                  # sáng: số liệu NGÀY HÔM TRƯỚC
        process_ads(now_vn() - timedelta(days=1))
    elif mode == "work":               # buổi tối: hôm nay (có nhắc) + bù ngày cũ
        process_work(now_vn(), remind_today=True)
    elif mode == "work_catchup":       # sáng hôm sau: chỉ gửi bù ngày cũ, KHÔNG nhắc
        process_work(now_vn(), remind_today=False)
    else:
        sys.exit(f"Mode không hợp lệ: {mode} (work / work_catchup / ads)")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("LỖI:", e, file=sys.stderr)
        sys.exit(1)
