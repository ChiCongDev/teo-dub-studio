# SPEC — Nguồn kênh tự động (Douyin → nhiều Page, duyệt caption trước khi đăng)

Trạng thái: **Giai đoạn 1–3 đã xong và kiểm thử (Giai đoạn 0 chưa), CHƯA nạp vào Tèo đang chạy** · Soạn: 16/10/2026 · Loại: Tier XL
Chủ dự án bảo "tiếp tục" (16/10, 14:09). Câu hỏi §8 dùng giá trị mặc định đề xuất (10/lượt, 20/ngày, 24 giờ, mỗi video 1 Page); đổi được về sau.
Bằng chứng: `work/20261016-channel-autopilot/` (470/470) và `work/20261016-plan-review/` (804/804).

## 1. Vấn đề

Chủ dự án muốn: **dán link một kênh Douyin, chọn các Page → Tèo tự lấy video, lồng tiếng, AI viết caption và
xếp giờ; chủ dự án xem lại/sửa caption rồi bấm duyệt; Tèo tự đăng đúng giờ.** Luồng phải dùng cho nhiều Page.

Quyết định của chủ dự án (16/10/2026):
- **Duyệt caption trước rồi mới bật lịch.** Không có chế độ tự đăng khi chưa duyệt.
- **Dùng cho nhiều Page.**
- Phong cách caption: ngắn, lôi cuốn, giải trí; khoảng 8–20 từ (xem `work/20261009-page-content-link/caption-ngan-60-mau-20261011.txt`).

## 2. Hiện trạng (đã đọc mã, 16/10/2026)

| Khâu | Mã hiện có | Còn thiếu |
|---|---|---|
| Quét kênh | `channel_discovery.scan_channel()` trả `items`, `complete`, `reason` (`login_required`…) | Không quét lại định kỳ; không nhớ video đã lấy |
| Tạo + chạy chiến dịch | `campaign.create_campaign()`, `CampaignBatchController.start()` | Phải bấm tay 2 nút; controller cố ý "never auto-start" |
| AI viết caption + giờ | `page_ai.PageAIController` theo từng Page; `auto_publish=False` → kế hoạch chờ duyệt | Danh sách chiến dịch nguồn cố định; **bắt buộc liên kết chat Codex** mới lưu được cấu hình |
| Duyệt | Tab Kế hoạch từ Codex: duyệt & bật mục đã chọn | **Không sửa được caption**; duyệt từng Page một, không có màn hình chung |
| Đăng | `page_schedule` + `facebook_web_post` | **Chưa xác nhận được bài đã lên** (16 lần bấm Đăng, 0 xác nhận trên Minh châu sneaker) |

## 3. Thiết kế

### Khái niệm mới (Ubiquitous Language)
| Thuật ngữ (mã) | Giao diện | Nghĩa |
|---|---|---|
| `ChannelSource` | Nguồn kênh | 1 link kênh Douyin + danh sách Page đích + cấu hình lồng tiếng đã chốt + giới hạn + trạng thái |
| `SeenVideo` | Video đã lấy | Link video kênh đã từng đưa vào chiến dịch; không bao giờ lấy lại |
| Review inbox | Hộp duyệt caption | Mọi bài AI đề xuất chưa duyệt của mọi Page, sửa caption và duyệt tại một chỗ |

### Luồng
```
Nguồn kênh (link + Page A, B…)
  → quét kênh (lần đầu ngay, sau đó theo chu kỳ)        [channel_discovery]
  → video mới chưa có trong SeenVideo, tối đa N/lượt
  → tạo 1 chiến dịch, xếp hàng chạy khi Tèo rảnh         [campaign / campaign_batch]
  → video hoàn tất được thêm vào nguồn AI của Page đích  [page_ai policy.campaigns]
  → đến giờ, AI viết caption + chọn giờ (chế độ duyệt)   [page_ai, auto_publish luôn = false]
  → Hộp duyệt caption: xem, sửa, duyệt mục đã chọn       [page_content_plan]
  → lịch đăng hiện có đăng đúng giờ                       [page_schedule]
```

### Vị trí trong mã (đề xuất)
- Mới: `teodub/channel_autopilot.py` (store SQLite riêng + controller có `tick()` giống `PageAIController`),
  `teodub/channel_autopilot_ui.py`, `teodub/plan_review_ui.py`.
- Sửa: `page_ai.py` (thêm/bớt chiến dịch nguồn qua hàm public; chat chiến lược thành **tùy chọn**; hướng dẫn phong cách
  caption theo Page), `page_content_plan.py` (sửa caption của mục chưa duyệt, giữ bản AI gốc), `campaign_batch_controller.py`
  (hàm "chạy khi rảnh"), `app.py` (gắn `tick`), tests.
- Store riêng `channel-autopilot.sqlite3`; chỉ gọi module khác qua hàm public của module đó (không mở thẳng DB của module khác).

## 4. Chốt an toàn (bất biến — không được bỏ)
1. Nguồn kênh **không bao giờ** đặt `auto_publish=true`. Chỉ bài đã duyệt mới có lịch bật.
2. Giữ nguyên mọi bất biến đăng bài trong CLAUDE.md (bấm Đăng một lần, không đăng lại lượt chưa rõ, không báo đăng khi chưa có bằng chứng).
3. **Mặc định mỗi video chỉ đăng trên 1 Page** (tránh nội dung trùng giữa các Page, dấu hiệu spam với Meta). Bật đăng nhiều Page phải chọn rõ.
4. Giới hạn: tối đa **N video mới/lượt quét** (mặc định 10) và **M video xử lý/ngày cho cả máy** (mặc định 20).
5. Gặp Douyin đòi đăng nhập, hết hạn mức Codex, lỗi bản quyền/chặn từ Facebook hoặc kết quả đăng chưa rõ → **dừng nguồn đó, báo "Cần kiểm tra"**, không tự thử lại.
6. Chỉ chạy khi Tèo rảnh; không chen vào Video đơn/chiến dịch/học giọng đang chạy. Tắt Tèo rồi mở lại: không quét/chạy bù dồn.

## 5. Các giai đoạn và tiêu chí nghiệm thu

**Giai đoạn 0 — Đăng bài tự xác nhận được (điều kiện để cả luồng có ý nghĩa)**
- [ ] Một lượt đăng thật trên Page thử được Tèo tự ghi **Đã xác nhận đăng** kèm link bài.
- Ghi chú: Codex đang làm phần này; giai đoạn 1–2 làm song song được vì chưa đăng gì nếu chưa duyệt.

**Giai đoạn 1 — Nguồn kênh: dán link → video lồng tiếng sẵn sàng cho AI**
- [x] Màn hình Nguồn kênh: dán link kênh Douyin, chọn ≥1 Page (mọi tài khoản), thư mục lưu → Lưu. Cấu hình giọng/Sub chốt từ tab Video đơn lúc lưu. Không tạo trùng cùng link + Page.
- [x] Sau khi lưu, Tèo tự quét và tạo chiến dịch chỉ gồm video chưa từng lấy (tối đa N). Quét lại không tạo video/chiến dịch trùng.
- [x] Chiến dịch tự chạy khi Tèo rảnh; Tạm dừng/Tiếp tục từng Nguồn kênh có hiệu lực.
- [x] Chiến dịch được thêm vào nguồn AI của từng Page đích. Page chưa có cấu hình AI → tạo cấu hình mặc định **chế độ duyệt** (2 video/ngày; mỗi sáng 06:30 AI soạn bài cho trong ngày), không cần chat Codex.
- [x] Lỗi ở bất kỳ bước nào hiện lý do trên Nguồn kênh; không lặp vô hạn.

**Giai đoạn 2 — Hộp duyệt caption cho nhiều Page**
- [x] Một màn hình liệt kê mọi bài AI đề xuất chưa duyệt của mọi Page; lọc theo Page; mở xem video; thấy caption và giờ.
- [x] Sửa caption (và giờ) từng bài; bản sửa là nội dung đăng thật; bản AI gốc được giữ trong lịch sử.
- [x] Duyệt các bài đã chọn → bật lịch. Bỏ bài → video trở lại kho cho lượt AI sau.
- [x] Bài chưa duyệt không bao giờ được đăng (có test).
- [x] Yêu cầu gửi AI mặc định theo phong cách caption ngắn; mỗi Page chỉnh được (ô Mục tiêu ở Đăng theo kế hoạch AI).

**Giai đoạn 3 — Theo dõi kênh** (đã làm cùng giai đoạn 1, có test)
- [x] Mỗi Nguồn kênh quét lại theo chu kỳ (mặc định 24 giờ), chỉ lấy video mới.
- [x] Áp giới hạn N/lượt và M/ngày; Douyin đòi đăng nhập → dừng nguồn, báo cần đăng nhập.
- [x] Quá giờ quét khi Tèo tắt → mở lại chỉ quét 1 lần, không quét dồn.

## 6. Rủi ro
- **Đăng chưa xác nhận được** (giai đoạn 0): tự động hóa phía trước sẽ dồn bài "chưa xác minh" nếu chưa sửa.
- **Nội dung không nguyên bản**: video Douyin lồng tiếng lại bị Meta giảm phân phối; đã gặp chặn bản quyền (STUDIOCANAL). Tự động hóa làm rủi ro tăng theo số Page → giới hạn ở mục 4.
- **Hạn mức Codex**: mỗi video dịch và mỗi lượt AI lập kế hoạch đều tốn hạn mức.
- **Douyin**: không đăng nhập thường chỉ đọc được ~18–19 video đầu kênh (đủ cho việc lấy video mới).
- **Năng lực máy**: mỗi lúc chỉ chạy 1 luồng xử lý video; nhiều kênh sẽ xếp hàng.
- **Hai agent cùng sửa repo** (Claude + Codex): mỗi giai đoạn chỉ một agent làm; ghi HANDOFF trước/sau.

## 7. Ngoài phạm vi
Đăng không qua duyệt · kênh TikTok/YouTube · đăng qua API Meta · tự đổi chiến lược theo số liệu xem · tự quay/dựng video mới.

## 8. Câu hỏi chờ chủ dự án
1. Giới hạn mặc định: **10 video mới mỗi lượt quét, 20 video xử lý mỗi ngày, quét lại mỗi 24 giờ** — đồng ý?
2. **Mặc định mỗi video chỉ đăng 1 Page** — đồng ý?
3. Bắt đầu từ **giai đoạn 1**, song song Codex làm giai đoạn 0?
