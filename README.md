# Bot báo cáo hàng ngày — Nông sản Tuấn Tú

Bot gửi **1 báo cáo buổi sáng** (`python report_bot.py daily`), **tất cả là số liệu NGÀY HÔM TRƯỚC**
— vì NV và chi tiêu Ads chỉ chốt cuối ngày, nên sáng hôm sau mới đủ dữ liệu. Chạy **9h** và lặp lại
**11h, 14h** để bắt cập nhật muộn (chỉ gửi lại khi nội dung đổi).

### 🧑‍💻 Phần CÔNG VIỆC (của ngày hôm trước)
1. **Công việc nhân viên** — tách 4 nhóm: ✅ Hoàn thành / 🟡 Đang làm / 🔄 Đang làm tiếp (việc cũ còn hạn) /
   🔴 Quá hạn; kèm dòng tổng. Việc kéo dài vừa xong → tin riêng **🎉 VIỆC VỪA HOÀN THÀNH**.

Bot **quét lùi 4 ngày** (`WORK_LOOKBACK`), nhớ trạng thái trong `state.json`:
- Nội dung đổi so với lần gửi trước → gửi (đánh dấu **🔄 CẬP NHẬT**).
- Ngày cũ từng bị bỏ sót (đã nhắc) nay NV mới nhập → **⏰ GỬI BÙ** đúng ngày đó.
- Ngày hôm trước trống → **⚠️ nhắc nhập** 1 lần.
- Không đổi → bỏ qua, không spam.

`state.json` được workflow tự commit ngược repo (`contents: write`).

### 📊 Phần ADS (của ngày hôm trước)
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
python3 report_bot.py daily   # công việc + ads (của ngày hôm trước)
python3 report_bot.py work    # chỉ phần công việc
python3 report_bot.py ads     # chỉ phần ads
```
Không đặt token → bot in báo cáo ra màn hình (không gửi).

## Đổi giờ gửi (giờ UTC = giờ VN − 7)
- `cron` trong `.github/workflows/daily.yml` — hiện `0 2/4/7 * * *` = 9h/11h/14h VN.

## Lưu ý dữ liệu
- Cột A của `Daily Report` (ngày) phải để nguyên — nếu đổi/xoá tiêu đề "NGÀY" bot vẫn
  chạy nhờ fallback về cột A, nhưng nên giữ tên "NGÀY" cho rõ.
- Phần Ads chỉ có số khi NV đã nhập dòng của NGÀY hôm đó vào `Báo Cáo Ads`
  (block SP cột A–H, block TD cột U–AB). Thiếu dòng → bot báo "chưa có dữ liệu".
- Tổng tháng cần dòng của tháng hiện tại trong các bảng K:S (SP) / K:N (TD) có số.
- Ngày trong sheet dạng `d/m` hoặc `dd/mm/yyyy` — bot tự nhận cả hai.
