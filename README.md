# Bot báo cáo hàng ngày — Nông sản Tuấn Tú

Bot gửi **2 báo cáo Telegram** theo 2 khung giờ (vì chi tiêu Ads chỉ chốt cuối ngày):

### 🧑‍💻 Báo cáo CÔNG VIỆC — gửi tối, có nhắc & cập nhật khi NV nhập muộn
1. **Công việc nhân viên trong ngày** — từng đầu việc, SL thực đạt/mục tiêu, % tiến độ, % KPI; tổng số việc hoàn thành / đang làm / trễ hạn / chưa bắt đầu.

Chạy lại **20h, 21h, 22h** (`report_bot.py work`) và **7h30 sáng hôm sau**
(`report_bot.py work_catchup`). Mỗi lần chạy bot **quét lùi 4 ngày** (`WORK_LOOKBACK`),
nhớ trạng thái trong `state.json`:
- Có việc & nội dung đổi so với lần gửi trước → gửi (đánh dấu **🔄 CẬP NHẬT** nếu đã gửi rồi).
- Ngày cũ từng bị bỏ sót (đã nhắc) nay NV mới nhập → gửi **⏰ GỬI BÙ** đúng ngày đó.
- Hôm nay trống → gửi **⚠️ nhắc nhập** đúng 1 lần (không nhắc ngày cũ).
- Không đổi → bỏ qua, không spam.

→ Ví dụ NV quên nhập ngày 24: tối 24 bot nhắc; NV nhập bù → sáng 25 bot **GỬI BÙ báo cáo ngày 24**,
tối 25 vẫn có báo cáo ngày 25 (bạn nhận đủ cả 2 ngày). Ngày cũ bot chưa từng theo dõi sẽ bỏ qua
để không đổ lịch sử khi mới deploy. `state.json` được workflow tự commit ngược repo (`contents: write`).

### 📊 Báo cáo ADS — `python report_bot.py ads` — gửi **9h00 sáng hôm sau** (dữ liệu NGÀY HÔM TRƯỚC)
2. **Ads Sản phẩm trong ngày** — chi tiêu, doanh thu, chi tiêu/doanh thu, SĐT, chi tiêu/SĐT.
3. **Ads Tuyển dụng trong ngày** — chi tiêu, lead, chi phí/lead, CV, chi phí/CV.
4. **Ads Sản phẩm tháng hiện tại** — chi tiêu, doanh thu, chi phí/doanh thu, khách chốt, giá trị/khách, tỷ lệ chốt.

## Nguồn dữ liệu
- `Daily Report` → mục 1 (công việc)
- `Báo Cáo Ads` → mục 2, 3, 4. Sheet này gồm nhiều block:
  - cột **A–H**: SP theo ngày
  - cột **K–S**: SP theo tháng (bot mục 4 đọc dòng tháng hiện tại)
  - cột **K–N** (block dưới): TD theo tháng
  - cột **U–AB**: TD theo ngày

Nếu sau này chèn/xoá cột trong `Báo Cáo Ads`, phải sửa lại các hằng số
vị trí cột (SP_*, M_*, TD_*) ở đầu `report_bot.py`.

Đọc qua gviz CSV nên **không cần service account**, chỉ cần sheet để chế độ
"Bất kỳ ai có đường liên kết → Người xem".

## Cài đặt (1 lần)

1. **Tạo bot Telegram:** nhắn `@BotFather` → `/newbot` → lấy **BOT_TOKEN**.
2. **Lấy CHAT_ID:** thêm bot vào nhóm, gửi 1 tin, mở
   `https://api.telegram.org/bot<BOT_TOKEN>/getUpdates` → copy `chat.id`
   (nhóm thường là số âm, vd `-100xxxx`).
3. **Đẩy repo lên GitHub** → Settings → Secrets and variables → Actions →
   thêm 2 secret: `BOT_TOKEN`, `CHAT_ID`.
4. Vào tab **Actions** → chọn workflow → **Run workflow** để test ngay.

## Chạy thử ở máy
```bash
export BOT_TOKEN=xxx CHAT_ID=yyy
python3 report_bot.py work   # báo cáo công việc
python3 report_bot.py ads    # báo cáo ads
```
Không đặt token → bot in báo cáo ra màn hình (không gửi).

## Đổi giờ gửi (giờ UTC = giờ VN − 7)
- Công việc: `cron` trong `.github/workflows/work.yml` — hiện `0 13 * * *` = 20h VN.
- Ads: `cron` trong `.github/workflows/ads.yml` — hiện `0 2 * * *` = 9h VN.

## Lưu ý dữ liệu
- Mục 2–3 chỉ có số khi NV đã nhập dòng của NGÀY hôm đó vào `Báo Cáo Ads`
  (block SP cột A–H, block TD cột U–AB). Thiếu dòng → bot báo "chưa có dữ liệu".
- Mục 4 cần dòng của tháng hiện tại trong bảng SP-theo-tháng (cột K–S) có số.
- Ngày trong sheet dạng `d/m` hoặc `dd/mm/yyyy` — bot tự nhận cả hai.
