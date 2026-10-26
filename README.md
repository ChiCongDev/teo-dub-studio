# Tèo Dub Studio

## Đăng Page được lại sau khi Facebook đổi nút menu tài khoản — 24/10/2026

- Từ sáng 24/10, Facebook đổi tên nút menu tài khoản (ảnh đại diện ở góc phải trên cùng) thành **"Account, your profile"**. Tèo không nhận ra nút này, nên bỏ qua bước chọn và xác minh đúng Page qua menu.
  - Page **du lịch toàn cầu** báo "không thấy nút Chuyển ngay".
  - Page **Phú thọ** báo "chưa xác minh được người đăng".
  - Tèo luôn dừng **trước khi bấm Đăng**, nên không có bài nào bị đăng sai tên hay đăng trùng.
- Nay Tèo nhận được nút mới. Các bước sau vẫn kiểm tra chặt như trước: menu "Your profile" → "See all profiles" → "Select profile" → chọn đúng tên Page → phải thấy "currently selected" ở đúng Page đó.
- **Lưu ý:** Tèo không chuyển sang Page khác khi trong Chrome của tài khoản còn **hộp viết bài đang mở dở**, để không làm mất bản nháp. Hãy đóng các hộp viết bài cũ trước.
- Các lịch "Cần kiểm tra" hôm nay: kiểm tra trên Facebook trước, rồi làm lại bằng "Tạo lượt mới (chưa bật)".

## Bấm Chạy hoặc Dừng link khi đang chạy xen kẽ: tab đổi ngay — 24/10/2026

- Trước đây, khi các link đang chạy xen kẽ mà bạn bấm **Chạy** ở một link, tab vẫn ghi "đã dừng" cho tới khi link khác làm xong video đang dở. Lý do là Tèo chỉ nhận yêu cầu giữa hai video, nên rất dễ tưởng là chưa bấm được.
- Nay tab đổi ngay thành **"chờ lượt"** và nút Chạy khóa lại, bạn không cần bấm thêm. Bấm **Dừng link này** cũng hiện ngay **"đã dừng"**.
- Cách chạy không đổi: link vẫn vào lượt sau khi video đang làm xong.

## Thử lại video lỗi khi các link đang chạy xen kẽ — 24/10/2026

- Nút **Thử lại video lỗi** ở tab của mỗi link nay bấm được cả khi các link khác đang chạy.
- Khi bấm nút:
  - Các video đang "Lỗi" của link đó chuyển thành **"Chờ thử lại"**. Tên tab cũng hiện "chờ thử lại".
  - Link đó vào chạy xen kẽ như khi bấm **Chạy**.
  - Trong các lượt của link, video chờ thử lại được làm trước, mỗi lượt một video, xen kẽ với các link khác.
- Mỗi lần bấm, mỗi video chỉ được thử lại **một lần**. Nếu lỗi lại, video hiện "Lỗi" như cũ và Tèo làm tiếp video khác. Muốn thử nữa thì bấm nút thêm lần nữa.
- Video thử lại dùng lại các bước đã xong (bản dịch, giọng…). Video lỗi ở bước dịch sẽ gọi Codex thêm lần nữa, như trước.
- Nút **Chạy** vẫn bỏ qua video lỗi, không tự thử lại.
- Nếu bạn dừng link giữa chừng, các video "Chờ thử lại" chưa làm vẫn được giữ. Lần sau bấm Chạy link đó, Tèo sẽ làm tiếp.
- Nếu link đang chạy một mình (bấm Chạy ở tab "＋ Thêm link"), các video chờ thử lại sẽ được làm ở lần chạy sau của link đó.
- **Không đổi:** nút Thử lại video lỗi ở tab "＋ Thêm link" (vẫn chạy một mình), các nút Dừng, một video một lúc, đăng Page.

## Câu cuối bị nhận dạng trôi ra sau khi hết hình — 24/10/2026

- Đôi khi Whisper ghi mốc cho vài câu cuối trôi ra sau khi video đã hết. Ví dụ video 21 của chiến dịch "demo" dài 2:19, nhưng câu cuối được ghi ở 2:20–2:22. Lời vẫn đúng, chỉ có mốc giờ bị trôi.
- Nay Tèo dồn các câu đó vào khoảng cuối video, giữ nguyên thứ tự và lời. Giọng đọc các câu đó nối tiếp nhau ở cuối. Nếu hình hết trước khi đọc xong, Tèo giữ khung hình cuối cho tới khi đọc xong, như với câu cuối dài.
- Tèo vẫn dừng và báo "SRT dài hơn video" để bạn kiểm tra khi:
  - có hơn 5 câu bắt đầu sau khi hết hình, hoặc
  - không câu nào bắt đầu trong video.
- Chỉ áp dụng khi Tèo tự nhận dạng lời. File SRT bạn tự chọn vẫn được kiểm tra như trước.

## Bớt video lỗi: câu đọc nhanh, câu cuối của video, nạp giọng — 24/10/2026

Ngày 23/10 có 8 video của hai chiến dịch "test" và "demo" dừng vì 3 lỗi của chính Tèo, không phải do giọng hay bản dịch. Nay đã sửa:

- **"Thời lượng câu đọc sau khi đọc nhanh không khớp" (6 video):**
  - Khi một câu phải đọc nhanh hơn một chút cho khớp hình, công cụ đọc nhanh làm câu ngắn đi hụt 0,003–0,03 giây ở cuối.
  - Với câu ngắn dưới 2 giây, bước tự kiểm tra của Tèo coi mức hụt đó là lỗi và dừng video.
  - Nay Tèo thêm một chút im lặng sau câu trước khi đọc nhanh, rồi cắt lại đúng độ dài, nên câu luôn đủ và không bị mất đoạn cuối. Mức đọc nhanh vẫn tối đa 1,25×, giọng không đổi.
- **"SRT dài hơn video đã chọn" (1 video):**
  - Khi Tèo tự nhận dạng lời, câu cuối đôi khi được ghi là kết thúc sau khi video đã hết (ví dụ 2:46,3 trong video dài 2:45,87).
  - Nay câu đó kết thúc đúng lúc hết hình.
  - Câu bắt đầu khi video đã hết: xem mục "Câu cuối bị nhận dạng trôi ra sau khi hết hình" ở trên. File SRT do bạn tự chọn vẫn được kiểm tra như trước.
- **Lỗi mạng khi nạp giọng ("WinError 10054", 1 video):** mô hình giọng VieNeu và mô hình nhận dạng Whisper đã có trên máy thì Tèo dùng luôn, không hỏi trang HuggingFace nữa. Máy chưa có mô hình thì vẫn tải như trước.
- **Video đã lỗi trước đó:** bấm **Thử lại video lỗi**. Video chạy tiếp từ bước bị dừng, dùng lại bản dịch và giọng đã có, không tốn lượt Codex.
- **Không đổi:** cách đặt câu theo hình, mức đọc nhanh tối đa, âm lượng, phụ đề, đăng Page.
- Lỗi "Chưa tải được video từ các trang tải" không sửa được bằng code vì phụ thuộc Douyin và các trang tải. Tèo vẫn tự thử lại như trước.

## Mỗi link một tab trong "Chiến dịch hàng loạt" — 23/10/2026

Để dễ nhìn và kiểm soát nhiều link cùng lúc, tab **Chiến dịch hàng loạt** nay có một hàng **tab con**:

- **Tab đầu, "＋ Thêm link":** là màn hình như trước: dán link, Lấy danh sách, đặt tên, Tạo chiến dịch mới.
- **Mỗi chiến dịch tạo xong có một tab riêng, đúng tên chiến dịch** (mỗi link một tên). Tên tab hiện luôn tình trạng, ví dụ "video-mon-an · 15/100 · chờ lượt", "video-làm-món-ăn · 1/100 · đang làm".
- **Trong mỗi tab:** link kênh, tiến độ, video đang làm và đang ở bước nào, bảng video của riêng link đó, cùng các nút **Chạy**, **Dừng link này**, **Thử lại video lỗi**, **Mở video gốc**, **Mở thành phẩm**, **Đóng tab**.
- **Chạy nhiều tab:** bấm Chạy ở bao nhiêu tab cũng được, theo thứ tự nào cũng được. Máy làm một video một lúc, nên các link đang chạy làm **xen kẽ, mỗi link một video**. Link đang làm dở video nào thì làm xong video đó rồi mới chuyển lượt.
- **Dừng link này** chỉ dừng link đó: video đang làm dừng lại và giữ các bước đã xong, các link khác vẫn chạy. Bấm **Chạy** ở tab đó để chạy lại.
- **Đóng tab** chỉ ẩn tab, không dừng và không xóa chiến dịch. Mở lại bằng **Danh sách chiến dịch → Mở chiến dịch**. Các tab đang mở vẫn còn khi mở lại Tèo.
- **Thử lại video lỗi** dùng được khi không có link nào đang chạy. Từ 24/10, nút này bấm được cả khi link khác đang chạy (xem mục "Thử lại video lỗi khi các link đang chạy xen kẽ" ở trên).
- Nút **Dừng** ở tab "＋ Thêm link" và nút **Dừng hàng đợi** ở Danh sách chiến dịch vẫn dừng tất cả như trước.
- **Giữ nguyên:** một video một lúc, các bước làm từng video, đăng Page, AI viết caption, giờ đăng. Quản lý Page vẫn chọn nguồn theo tên chiến dịch.

## Chạy xen kẽ nhiều chiến dịch: mỗi chiến dịch một video, lần lượt — 23/10/2026

Trước đây, khi một chiến dịch đang chạy, các nút **Lấy danh sách** và **Tạo chiến dịch mới** bị khóa. Nhiều chiến dịch thì phải chạy xong chiến dịch này mới sang chiến dịch kia. Nay:

- **Tạo chiến dịch khi đang chạy:** trong lúc một chiến dịch đang chạy, bạn vẫn dán link mới, bấm **Lấy danh sách** và **Tạo chiến dịch mới** được. Tèo hỏi lại cho chắc, rồi cho chiến dịch mới **chạy xen kẽ** với chiến dịch đang chạy:
  - Video đang làm được làm xong, không bị cắt ngang.
  - Sau đó mỗi chiến dịch một video, lần lượt: A → B → A → B… Thêm chiến dịch thứ ba thì A → B → C → A…
  - Nhờ vậy các Page lấy video từ các chiến dịch khác nhau đều có video mới đều nhau.
- **Khi có chuyện:**
  - Chiến dịch nào hết video thì tự rời vòng.
  - Video lỗi được ghi lỗi như trước rồi chuyển lượt.
  - Hết nguồn tải, hết lượt Codex hoặc cần đăng nhập thì cả vòng tạm dừng. Riêng trường hợp chờ nguồn tải, Tèo tự chạy lại khi tới giờ, như trước.
- **Dừng và chạy tiếp:**
  - Nút **Dừng** dừng cả vòng.
  - Bấm **Chạy / Tiếp tục** trên bất kỳ chiến dịch nào trong vòng thì Tèo chạy tiếp đúng lượt đang dở.
  - Đóng app, hoặc bấm "Khởi động lại, giữ công việc", thì vòng vẫn chạy. Ngoại lệ duy nhất: nếu bạn đóng app đúng lúc chiến dịch cũ còn đang làm nốt video trước khi chuyển sang xen kẽ, vòng xen kẽ sẽ bắt đầu khi bạn mở lại Tèo.
  - Đang lấy danh sách mà bấm **Dừng** thì chỉ dừng việc lấy danh sách; chiến dịch đang chạy vẫn tiếp tục.
- **Hiển thị:** chiến dịch đang đợi lượt hiện "Chạy xen kẽ · chờ tới lượt". Ở tab **Danh sách chiến dịch** có thêm lựa chọn **Xen kẽ từng video**, bên cạnh **Lần lượt từng chiến dịch** như trước.
- **Giữ nguyên:**
  - Vẫn chỉ làm một video một lúc, mỗi video vẫn đi đủ các bước như trước.
  - Đánh số video, tên file và thư mục không đổi.
  - Đăng Page, AI viết caption và giờ đăng không đổi.
  - Khi một danh sách "lần lượt" (kể cả Nguồn kênh tự động) đang chạy, chiến dịch mới chỉ được lưu, chưa tự chạy.

## Giọng Việt khớp hình, tiếng gốc tự hạ khi đọc, dịch gọn theo thời lượng — 23/10/2026

Trước đây, ở khoảng 6/10 số câu, giọng Việt dài hơn lời gốc. Tèo đọc các câu nối đuôi nhau nên giọng **trễ dần so với hình**: video 15 trễ trung bình 12 giây, có lúc 56 giây. Có video thì Tèo phải **làm chậm cả hình** (video 14 còn 0,92 lần). Tiếng Trung gốc để 56% nên to gần bằng giọng Việt. Nay:

- **Khớp hình:** mỗi câu bắt đầu đúng lúc người trong video nói câu đó. Câu nào dài hơn chỗ trống trước câu kế tiếp thì Tèo **đọc nhanh hơn, tối đa 1,25 lần, không đổi giọng**. Khi đang bị trễ, khoảng nghỉ giữa hai câu rút còn 0,08 giây để đuổi kịp. **Không làm chậm hình nữa.** Nếu câu cuối còn đọc khi hết hình, Tèo giữ khung hình cuối thêm chút ít.
- **Tiếng gốc tự hạ:** lúc không có giọng Việt, tiếng gốc giữ đúng mức bạn chọn (ví dụ 56%) để còn tiếng động, không khí. Khi giọng Việt đọc, tiếng gốc tự hạ xuống **12%**, chuyển mượt: bắt đầu hạ 0,15 giây trước câu, lên lại trong 0,3 giây sau câu. Hai câu cách nhau dưới 0,6 giây thì giữ thấp luôn, không bị "phập phồng". Nếu mức tiếng gốc đang chọn từ 12% trở xuống, hoặc đã tắt giọng Việt, thì Tèo không hạ.
- **Dịch gọn:** với video dịch mới, Tèo gửi Codex kèm **số giây của từng câu** và dặn dịch khoảng **4 chữ mỗi giây**: bỏ từ thừa, lặp, nhưng giữ đủ ý, con số, tên, phủ định. Video đang dịch dở vẫn dịch theo cách cũ cho tới hết.

Nghe thử 3 phút đầu video 15 (cùng bản dịch, cùng giọng, không tốn lượt Codex):

- **Độ trễ:** giọng trễ trung bình 17,3 giây → **0,3 giây**, chậm nhất 2,5 giây.
- **Độ rõ:** giọng Việt to hơn tiếng gốc bên dưới **13 dB** (trước −0,8 dB, tức to ngang tiếng Trung).
- **Tốc độ đọc:** 3,6 → **4,4 chữ/giây**, câu nhanh nhất 5,4 chữ/giây, trong khoảng nói chuyện tự nhiên.

Tổng âm lượng video nhỏ hơn khoảng 3 dB vì tiếng Trung được hạ; giọng Việt vẫn to như cũ. Video đã dựng hoặc đã đăng không tự đổi.

## Ẩn dòng "Chat chiến lược" ở tab Thiết lập — 23/10/2026

Liên kết chat chiến lược **không bắt buộc** (AI vẫn viết caption theo **Mục tiêu phát triển** và dữ liệu Page), và hiện không Page nào dùng. Theo yêu cầu, dòng "Chưa liên kết chat chiến lược…" cùng nút **Chat chiến lược…** dưới ô Mục tiêu đã được ẩn cho gọn. Ghi cách viết hoặc caption mẫu ưng ý thẳng vào ô **Mục tiêu phát triển** của từng Page. Phần liên kết vẫn còn trong tab **Kế hoạch** nếu sau này cần; lưu ý nếu liên kết hoặc đổi chat **sau khi đã lưu thiết lập**, cần bấm **Lưu thiết lập & hẹn giờ** lại, nếu không lượt AI kế tiếp sẽ dừng và chế độ Tự động tự tắt.

## Caption video tự nhiên như người thật — 23/10/2026

Trước đây AI viết caption dài 55–80 chữ, giọng thuyết minh ("Video ghi lại…", kèm lời khuyến cáo). Nay lời dặn AI (chế độ Tự động từ chiến dịch và kế hoạch AI) theo kiểu bạn chọn hôm 11/10:

- **8–20 chữ**, 1–2 câu ngắn, giọng nói chuyện như người trong cuộc kể lại; **câu đầu là móc câu**: chuyện bất ngờ/thú vị nhất có thật trong video (tình huống, câu thoại, con số, giá, món ăn, nơi chốn). Đổi kiểu móc câu giữa các bài; không kể hết kết quả.
- Không mở đầu bằng "Video ghi lại/Video này/Hành trình", không giọng báo cáo; không gọi tên người khi chưa rõ; **2–4 hashtag**, tối đa 1–2 emoji; **không kêu gọi like/share/comment/theo dõi** (Facebook hạ phân phối kiểu này), không giật tít, không bịa chi tiết.
- **Mục tiêu phát triển** của Page được ưu tiên: ghi cách viết hoặc dán caption mẫu ưng ý vào đó thì AI theo.
- AI đọc lời thoại **rải đầu – giữa – cuối video** (tới 1.200 ký tự mỗi video khi ít video) thay vì chỉ 700 ký tự đầu, để chọn đúng khoảnh khắc hay nhất.

Thử trước với 5 video du lịch (1 lượt Codex): caption mới 13–16 chữ, ví dụ *"Chờ sáu tiếng đến nửa đêm để ngắm núi lửa phun ở Yogyakarta. #Yogyakarta #Indonesia #NuiLua"*. Bài đã bật lịch giữ caption cũ. Caption chỉ là một phần nhỏ của lượt xem — không ai bảo đảm video lên xu hướng.

## Đăng thước phim không còn dừng vì "2 ô mô tả" — 23/10/2026

Video 13 và 15 (Page "du lịch toàn cầu") từng dừng trước khi bấm Đăng với lỗi "Không xác định duy nhất ô mô tả thước phim cuối cùng (2 mục)": Facebook đôi khi giữ một ô mô tả cũ trong **lớp bị ẩn** của hộp tạo thước phim. Nay Tèo chọn đúng ô mô tả đang hiện (cũng là ô được kiểm tra lời đăng trước khi bấm Đăng); vẫn kiểm tra toàn bộ lời đăng và chỉ bấm Đăng một lần. Nếu thật sự có 2 ô hiện rõ, Tèo vẫn dừng trước khi đăng nhưng ghi kèm thông tin từng ô để chẩn đoán.

## Ghi chú hỏng của Codex không còn làm dừng cả video — 22/10/2026 (chiều)

Video 15 dừng vì Codex trả kèm bản dịch một **ghi chú nghe duyệt** gắn sai số câu ("Ghi chú bản dịch Codex không khớp câu nguồn."). Ghi chú chỉ để bạn nghe lại, không phải lời dịch. Nay Tèo **bỏ những ghi chú hỏng** (gắn câu ngoài nhóm, trùng câu, rỗng; ghi chú quá dài thì cắt ngắn), ghi vào nhật ký "Bỏ N ghi chú…", và vẫn dùng bản dịch. Không tốn thêm lượt Codex. Lời dịch vẫn được kiểm tra chặt như trước; bản dịch đã lưu vẫn kiểm tra chặt.

## Khung che Sub gốc luôn nằm giữa; không còn nhầm đồ vật trên bàn là phụ đề — 22/10/2026 (chiều)

- **Luôn căn giữa:** chữ Việt và khung luôn nằm chính giữa khung hình. Khi phụ đề gốc rộng hơn chữ Việt, khung nới đều cả hai bên để che hết.
- **Bộ dò phụ đề gốc chính xác hơn:** chỉ nhận **nét chữ có viền tối ở hai bên** (đặc trưng của phụ đề chữ trắng viền đen). Trong cảnh máy quay đứng yên, cạnh ô bàn caro, gói snack, cốc, mép đĩa nằm cùng hàng với phụ đề không còn bị gộp vào vùng che (đây là lý do video 10 — núi lửa Bromo — có khung rộng và lệch trái).
- Đo trên 7 video thật: hàng phụ đề Tèo học được không đổi; ở video 10, 90% số câu chỉ lệch khỏi giữa tối đa 13 điểm ảnh (trước 88), số câu có khung rộng hơn 1,5 lần chữ giảm từ 30% xuống 18%. Đã soát 48 khung hình: không lộ chữ Trung.
- Giới hạn còn lại: vật bóng loáng có vệt sáng mảnh (vành nồi inox) vẫn có thể bị nhầm là nét chữ; khi đó khung nằm giữa nhưng rộng hơn. Khi phụ đề gốc là một câu dài mà chữ Việt ngắn, khung vẫn phải rộng bằng phụ đề gốc.

## Khung che Sub gốc: một khung gọn, chữ Việt to theo chữ gốc — 22/10/2026

Trước đây khi bật "Khung che Sub gốc", mỗi câu có **hai khung trắng chồng lên nhau** (khung ôm chữ Việt + khung che chữ Trung). Chỗ hai khung lệch nhau thành hình bậc thang "lồi lõm"; câu 2 dòng thì mỗi dòng một khung. Nay:

- Mỗi câu chỉ có **một khung chữ nhật góc vuông**, **chữ Việt nằm chính giữa**, khung **vừa đủ**: to bằng chữ Việt, hoặc bằng phụ đề gốc nếu phụ đề gốc to hơn. Câu bình thường nằm cùng một độ cao suốt video; chỉ câu có phụ đề gốc to hoặc lệch thì khung bám theo phụ đề đó.
- **Chữ Việt to theo chữ gốc, theo từng video:** Tèo đo cỡ chữ gốc của video và đặt chữ Việt cao bằng chữ gốc, **cùng một cỡ cho mọi câu**, không nhỏ hơn cỡ bạn chọn, tối đa gấp 1,5. Thử trên 6 video: kênh ăn uống lên cỡ 20, phim tóm tắt cỡ 17, các video du lịch giữ cỡ 16. Cỡ chữ trong cài đặt không đổi.
- **Lề trong khung** giảm từ 1/4 xuống **1/6** chiều cao dòng, khung sát chữ hơn.
- Bộ dò phụ đề gốc chỉ tính phần nằm trên **hàng phụ đề** của video: vật sáng dính sát phụ đề (vành nồi inox, mép bánh, tay, túi nilon…) không còn làm khung phình ra. Trên 6 video thật (3.129 câu): số câu khung bị kéo dài quá dòng phụ đề giảm từ 22–48% xuống 0–9%; đã soát bằng mắt 84 khung hình, không câu nào lộ chữ Trung do thay đổi này.

Giới hạn còn lại: câu mà Tèo không nhận ra phụ đề gốc (rất hiếm, ví dụ phụ đề 2 dòng nằm thấp hơn thường lệ trên nền trắng) vẫn có thể lộ một phần như trước. Không phóng to chữ Việt riêng từng câu, vì vùng "chữ to" trong một câu nhiều khi là đĩa trắng, túi nilon bị nhận nhầm. Video đã dựng hoặc đã đăng không tự đổi.

## Máy ngủ dậy vẫn dựng tiếp; câu Codex không dịch được thì hỏi lại riêng câu đó — 22/10/2026

**Máy tính bị cho Ngủ giữa lúc làm (video 10, 21/10):** trước đây Tèo tính cả thời gian máy ngủ vào giới hạn thời gian, nên máy vừa tỉnh là Tèo tưởng bị treo và dừng (video 10 chỉ còn 12 giây là xong bước trộn âm thanh), rồi báo lỗi khó hiểu "WinError 32". Nay giới hạn thời gian của **bước dựng video, bước xóa Sub (GPU) và bước chỉnh nhịp giọng** chỉ tính lúc máy thức: máy tỉnh dậy là chạy tiếp. Khi dọn thư mục tạm mà Windows còn giữ tệp, Tèo chờ và thử xóa lại vài giây; không được thì để lại thư mục nhưng **báo đúng lỗi thật**. Khi dựng lại một video lỗi, Tèo tự xóa thư mục tạm còn sót của chính video đó, và nếu thư mục thành phẩm cũ chỉ còn rác tạm thì video dựng lại **giữ đúng tên cũ** (không thành "_lan-2").

Lưu ý: muốn Tèo chạy qua đêm thì **đừng bấm Ngủ (Sleep)** — chỉ tắt màn hình hoặc khóa máy (Windows + L). Lúc máy ngủ, Tèo không làm gì và cũng không đăng bài được.

**Codex trả câu rỗng hoặc còn chữ Trung (video 8 câu 556, video 11 câu 457):** thường do nhận dạng giọng nói nghe nhầm thành chữ vô nghĩa. Trước đây cả video dừng trước bước tạo giọng. Nay nếu trong một nhóm dịch **chỉ có tối đa 3 câu** bị hỏng, Tèo **giữ các câu tốt và tự hỏi lại Codex đúng 1 lần, chỉ cho mấy câu đó** (kèm ghi chú "câu có thể bị nghe nhầm, dịch theo ngữ cảnh, không để chữ Trung"); câu hỏi lại được ghi vào **Ghi-chu-ban-dich.txt** để nghe duyệt. Hỏi lại vẫn hỏng, hoặc hỏng hơn 3 câu, thì dừng như trước, chờ bạn bấm Thử lại. Mỗi lần hỏi lại tốn thêm một lượt Codex (ít hơn dịch lại cả nhóm 300 câu).

## Tự động đăng: video xong muộn vẫn đăng bù trong ngày — 21/10/2026

Trước đây "Video mỗi ngày 10 · Giờ bài đầu 09:30 · Cách nhau 5 phút" nghĩa là đúng 10 giờ cố định (09:30 … 10:15); video xong sau 10:15 phải sang 09:30 hôm sau dù hôm đó mới đăng 2–3 bài. Nay nhịp **09:30 + 5 phút** chạy **tới hết ngày**: nếu đã qua các giờ đầu mà **hôm nay chưa đủ 10 bài**, video xong được xếp vào mốc kế tiếp của nhịp đó ngay trong ngày (ví dụ xong 12:33 → đăng 12:40). Đủ 10 bài thì sang 09:30 hôm sau; không bao giờ đăng trước giờ bài đầu; quá 23:55 thì sang hôm sau. Lịch đã hẹn trước đó không tự đổi (muốn đổi: Tạm dừng → đổi giờ → Bật).

## Khung che thông minh: ôm sát Sub Việt, tự nới vừa đủ che phụ đề gốc — 21/10/2026

Thay cho dải trắng rộng 92%: khi bật "Khung che Sub gốc", mỗi câu Việt nằm trên **khung ôm sát chữ** (rộng bằng chữ + lề nhỏ, cao bằng số dòng). Trước khi dựng, Tèo **tự tìm phụ đề gốc (chữ Trung) trong hình** ở đúng những lúc câu Việt hiện và **chỉ phủ thêm vừa đủ** chỗ chữ Trung đó (chữ Trung dài hơn thì khung nới ra, 2 dòng thì cao 2 dòng).

- Cách tìm: chữ phụ đề sáng, có viền tối và **đứng yên** trong khi nền chuyển động; Tèo học **vùng phụ đề riêng của từng video** từ các câu chắc chắn để loại nhầm (áo, vành nồi, cạnh cửa…). Câu nào không thấy rõ thì dùng **khung điển hình** của video đó để không bao giờ lộ chữ; video không có phụ đề gốc thì chỉ có khung ôm chữ Việt.
- Đã thử trên 5 video thật của 4 kênh (du lịch chữ nhỏ, tóm tắt phim chữ trắng/vàng trên nền đen, ăn uống chữ to viền dày, chợ chữ viền mỏng): mọi câu mẫu đều được che; quét thêm khoảng **0,07–0,1 giây mỗi câu** (video 41 phút ≈ 1,5–2 phút), không cần cài thêm gì, không dùng GPU.
- Có thể **thừa một chút trắng** ở chỗ Tèo không chắc; khi phụ đề gốc lệch giữa hay cao hơn chữ Việt, phần trắng có thể thành hình chữ L (ít trắng nhất). Nếu vì lý do gì không đọc được hình, Tèo quay về **dải rộng** để vẫn che chắc.
- Không che **tiêu đề** đặt ở chỗ khác trong hình (không phải phụ đề).

Cùng đợt: **chiến dịch chờ nguồn tải nay luôn tự chạy lại** (lúc nguồn tạm dừng sớm nhất có lại, hoặc sau 30 phút nếu các nguồn chỉ hỏng riêng với video đó), và nếu đang ở lượt "Thử lại video lỗi" thì chạy tiếp đúng lượt đó.

## Tải quá chậm thì chuyển nguồn sớm — 21/10/2026

Trước đây nếu đường tải chậm (ví dụ video 9: khoảng 0,45 MB/giây, cả video mất gần 40 phút), Tèo cứ chờ cho tới khi đường tải đứt. Nay trong lúc tải, Tèo đo tốc độ trung bình của **phút gần nhất** (bỏ qua phút đầu vì máy chủ hay khởi động chậm). Nếu với tốc độ đó **phần còn lại cần hơn 15 phút** (hoặc, khi không biết dung lượng, tốc độ dưới 300 KB/giây), Tèo bỏ đường này và **chuyển sang nguồn kế tiếp** ("Đường tải quá chậm (khoảng … MB/giây; phần còn lại cần khoảng … phút); chuyển sang nguồn khác.").

- **Nguồn cuối cùng không bao giờ bị bỏ vì chậm**: khi mạng chậm chung, video vẫn tải xong thay vì thất bại.
- Chậm chỉ là chuyện của đường tải đó, nên Tèo **không đánh dấu trang là hỏng**; video sau vẫn thử lại trang đó.
- Áp dụng cho Douyin trực tiếp, SaveTik, Social Video Download, AnySave. Viện Phần Mềm tải qua trình duyệt nên chưa đo được tốc độ.

## Khung che Sub gốc: dải nền đặc dưới phụ đề Việt — 21/10/2026

Khi ghi Sub Việt vào hình, phụ đề Việt hay đè lên phụ đề Trung có sẵn trong video, rất rối mắt. Nay có tùy chọn **"Khung che Sub gốc"** (tab Video đơn, ngay dưới hàng "Ghi Sub Việt trực tiếp vào hình"):

- **Không khung** (mặc định, như cũ) · **Khung trắng · chữ đen** · **Khung đen · chữ trắng**.
- Khi bật: mỗi câu Việt nằm trên một **dải nền đặc rộng khoảng 92% chiều ngang**, cao vừa đúng số dòng của câu (câu dài 2–3 dòng thì dải cao lên), đặt đúng chỗ phụ đề hiện nay (sát đáy), nên **che kín** dòng chữ Trung ở đó. Màu chữ do khung quyết định (ô "Màu chữ" tự khóa); độ đậm và cỡ chữ giữ như bạn chọn.
- Dải **chỉ hiện khi có câu Việt**; các câu cách nhau dưới 1 giây dùng chung một dải liền (không nhấp nháy).
- Chạy ngay khi dựng video, **không cần GPU, không làm chậm**. Video đã xuất/đã đăng giữ nguyên.
- Với chiến dịch: chọn khung ở tab Video đơn rồi bấm **"Lấy cấu hình từ tab Video đơn"** để các video chưa làm xong dùng kiểu mới; chiến dịch tạo mới sẽ lưu sẵn lựa chọn này.
- Giới hạn: khung nằm sát đáy. Video nào đặt phụ đề gốc cao hơn (hay gặp ở video dọc) thì khung không che tới; khi cần sẽ thêm ô chỉnh độ cao.

## Nguồn tải mới đứng đầu: "Douyin trực tiếp" (không cần đăng nhập) — 21/10/2026

Tèo giờ lấy video **trực tiếp từ chính trang video Douyin** trước, rồi mới tới 4 trang tải cũ. Thứ tự mới: **Douyin trực tiếp → SaveTik → Viện Phần Mềm → Social Video Download → AnySave** (tiến trình hiện "Nguồn 1/5…").

- **Cách làm:** Tèo mở trang video bằng trình duyệt riêng tạm thời (ẩn), **không đăng nhập, không đọc cookie, không dùng hồ sơ Chrome nào**, đọc thông tin video mà chính trang Douyin nhận về, chọn bản hình tốt nhất (cao nhất, bitrate lớn nhất, dưới giới hạn 2 GB) rồi tải từ máy chủ video công khai của Douyin bằng đúng bước tải và kiểm tra hình/tiếng như các nguồn khác. Mỗi video chỉ mở trang một lần, không tự thử lại.
- **Chất lượng:** đã chạy thật: 1920×1080, 30 khung hình/giây, H.264, có tiếng; thường **tốt hơn** bản các trang tải trả về (có video SaveTik trả 866×576 hoặc HEVC nén thấp). Bản tốt hơn nên file **nặng hơn** (ví dụ video 9 phút khoảng 250 MB, video 41 phút khoảng 1 GB).
- **Khi Douyin từ chối một video** (một số video Douyin không cho xem khi chưa đăng nhập, khoảng 8/13 lượt thử thành công): Tèo chuyển ngay sang SaveTik… trong khoảng 10 giây; video sau vẫn thử Douyin trước. Nếu Douyin hiện trang xác minh (captcha), Tèo **không tự giải**, tạm bỏ nguồn này 1 giờ và dùng các trang khác.
- **Khi Douyin từ chối và các trang khác đều đang hỏng:** video chờ ("Chờ nguồn tải…") và tự chạy lại khi có trang dùng lại được, như bản 20/10.

Lồng tiếng, caption, xếp giờ, đăng bài không đổi. Cần **Khởi động lại, giữ công việc** để dùng; video lỗi cũ bấm **Thử lại video lỗi**. Lưu ý: đừng bật VPN khi chạy (Douyin hạn chế truy cập từ nước ngoài; Facebook có thể coi đăng nhập từ nước ngoài là lạ).

## Tải video khi các trang tải miễn phí hỏng: nhớ trang hỏng, chờ nguồn, tự chạy lại — 20/10/2026

Trước đây mỗi video đều phải thử lần lượt 4 trang tải bên ngoài; khi SaveTik và Viện Phần Mềm không trả kết quả, Social Video Download bảo trì Douyin và AnySave hết lượt trong ngày thì **mỗi video mất khoảng 3 phút rồi báo lỗi đỏ**, và cả danh sách bị đánh lỗi hàng loạt. Nay:

1. **Tèo nhớ trang nào đang hỏng** (file `download-provider-health.json` trong thư mục dữ liệu, dùng chung cho mọi tiến trình): hết lượt trong ngày (theo giờ trang báo, tối đa 24 giờ), bảo trì hoặc xác minh/giới hạn (1 giờ), không phản hồi (30 phút), lỗi kết nối (20 phút). Video kế tiếp **bỏ qua** trang đó (không gửi thêm yêu cầu nào tới trang) và dùng ngay trang còn tốt. Trang tải thành công thì được xóa khỏi danh sách hỏng. Lỗi riêng của một video (không thấy video, MP4 hỏng) không làm trang bị đánh dấu.
2. **Không còn lỗi đỏ hàng loạt:** khi cả 4 trang đều chưa dùng được, video nhận trạng thái **"Đã dừng" với thông báo "Chờ nguồn tải: … Tèo thử lại sau HH:MM"**, chiến dịch **tạm dừng** (các video sau giữ nguyên "Chờ xử lý") thay vì đốt hết danh sách.
3. **Tự chạy lại:** tab Chiến dịch hàng loạt đang mở chiến dịch đó sẽ **tự chạy tiếp khi đến giờ** trang sớm nhất dùng lại được (kiểm tra mỗi phút, chỉ với chiến dịch tự tạm dừng vì chờ nguồn tải). Các lý do tạm dừng khác (hết hạn mức Codex, bấm Dừng…) không bao giờ tự chạy lại.

Không đổi gì ở khâu lồng tiếng, caption, xếp giờ, đăng bài; file video tải về vẫn là bản gốc từ trang tải nên chất lượng không đổi. Chưa thêm trang tải thứ năm: chưa tìm được cách tải trực tiếp từ Douyin (yt-dlp bị 403 dù có cookie mới). Video đang lỗi từ trước vẫn cần bấm **Thử lại video lỗi** một lần sau khi khởi động lại Tèo.

## Sửa lỗi "Không xác định duy nhất tên Page (0 mục)" khi tên Page khác chữ hoa/thường — 20/10/2026

Bài 14:25 của Page "du lịch toàn cầu" dừng ở bước chuẩn bị (chưa bấm Đăng) vì Tèo lưu tên chữ thường còn Facebook hiển thị "Du lịch toàn cầu"; Tèo tìm tên Page bằng so khớp phân biệt hoa/thường nên báo 0 mục. Nay hai chỗ tìm tên Page (nút đầu trang và tiêu đề Page) **không phân biệt hoa/thường** nhưng vẫn phải khớp **toàn bộ** tên, vẫn đúng URL Page và đúng một mục nhìn thấy được. Tên khác (ví dụ thêm chữ) vẫn bị từ chối. Cửa sổ Page cũng tự cập nhật số video nguồn mỗi khoảng 10 giây.

Sau khi cập nhật: Khởi động lại Tèo, bấm **Xếp lại lịch lỗi…** cho bài đang "Cần kiểm tra" hoặc để Tèo xếp bài kế tiếp. Chưa thử trên Facebook thật.

## Tự động từ chiến dịch: lồng tiếng xong → AI viết caption → tự duyệt → tự đăng — 19/10/2026

Trong **Quản lý Page → chọn Page → Thiết lập → Nhịp nội dung & giờ gọi AI** có thêm **một ô tick**: **Tự động từ chiến dịch: video lồng tiếng xong → AI viết caption → tự duyệt → tự đăng**. Mặc định **tắt**, bật riêng cho từng Page.

**Cách dùng**
1. Chọn **Nguồn video** (chiến dịch) cho Page.
2. Đặt **Video mỗi ngày** (số bài tối đa mỗi ngày do bạn tự đặt, không có giới hạn nào ngoài ngưỡng an toàn 1.000), **Giờ bài đầu mỗi ngày (HH:MM)** và **Cách nhau (phút)**, **tùy ý bạn từ 1 đến 1440 phút**, Tèo không áp mức tối thiểu nào ở chế độ này. Ví dụ 11:20, cách nhau 45, 3 bài/ngày: 11:20, 12:05, 12:50; cách nhau 5 phút thì 10:00, 10:05, 10:10… Số bài lớn có thể chạy qua nửa đêm.
3. Tick ô **Tự động từ chiến dịch…** rồi bấm **Lưu thiết lập & hẹn giờ**.

**Tèo làm gì sau đó:** mỗi 5 phút Tèo xem Page có video mới lồng tiếng xong (có phụ đề) chưa dùng không. Có thì **gom tất cả video mới thành một lượt AI** (một lần gọi Codex cho cả đợt), AI viết caption riêng cho từng video, Tèo **tự duyệt và bật lịch**: mỗi video được xếp vào **giờ trống kế tiếp** trên lưới giờ của bạn (bỏ qua giờ còn cách lúc AI chạy dưới **3 phút**, giờ trùng đúng phút với một lịch có sẵn và ngày đã đủ số bài). Đến giờ Tèo đăng. Không giới hạn số lượt AI mỗi ngày ở chế độ này, nhưng mỗi lượt vẫn tốn hạn mức Codex. Nếu một giờ trôi qua trong lúc AI đang viết (còn chưa tới 90 giây khi AI trả lời), Tèo **bỏ riêng bài đó, vẫn bật lịch các bài còn lại**, và video bị bỏ ở lại hàng chờ cho lượt sau. **Bài đã xóa hoặc đã hủy không tính vào số bài mỗi ngày** và không chặn giờ ở chế độ này, nên giờ bạn đặt luôn được tính từ **hôm nay** (chỉ khi hết giờ trong lưới của hôm nay mới sang ngày kế tiếp); bài đang bật, tạm dừng, đã gửi hoặc đã đăng vẫn chiếm một suất trong ngày.

**Xóa bài khỏi danh sách trả video về hàng chờ:** bài chưa từng chuẩn bị/gửi mà bạn bỏ khỏi **Lịch đăng** (nút **Xóa video**) thì video được giải phóng và lượt AI tự động kế tiếp sẽ xếp lại (viết caption mới); bài có thể đã gửi thì vẫn giữ chỗ để không đăng trùng. Lưu ý: Tèo đăng **từng bài một cho mỗi tài khoản** (tải video lên mất vài phút), nên các bài cách nhau quá ngắn sẽ xếp hàng và có thể đăng trễ hơn giờ đặt; bài đăng dồn dập cũng dễ bị Meta coi là spam.

**Xem lại:** tab **Duyệt caption** có mục **Đã tự duyệt** liệt kê bài, giờ, caption và trạng thái; chọn bài rồi bấm **Tạm dừng bài đã chọn** để chặn. Tab **Lịch đăng** vẫn hiện từng lịch. Muốn tắt hẳn, bỏ tick ô trên và Lưu (bài đã bật lịch không bị gỡ, tạm dừng riêng ở Lịch đăng).

**Tự dừng khi có chuyện:** AI lỗi hoặc hết hạn mức, AI trả giờ/caption sai quy tắc, Tèo bị tắt giữa lượt: Tèo **tắt chế độ tự động của Page đó** (các thiết lập khác giữ nguyên), ghi lý do ở **Lịch sử AI** và không tự thử lại. Muốn chạy tiếp thì tick lại ô và Lưu. Video chưa có phụ đề hoặc đã lỗi bị bỏ qua. Mỗi video chỉ đăng một lần trên một Page.

**Không đọc caption trước khi đăng:** caption sai vẫn lên Page. Tèo vẫn **chưa tự xác nhận được bài đã lên Facebook**, nên hãy mở Page xem bài đầu tiên. Nên bắt đầu với 1 Page và 1–2 bài mỗi ngày. Video chỉ lồng tiếng lại có thể bị Meta giảm hiển thị hoặc chặn bản quyền. Giữ Tèo mở và máy không ngủ. **Nguồn kênh tự động** vẫn đưa video vào Page này như thường (Page ở chế độ này không bị từ chối).

Bấm **Khởi động lại, giữ công việc** để nạp. Đã kiểm thử bằng dữ liệu tạm và AI giả lập; **chưa chạy với AI hoặc Facebook thật**.

## Tạm ẩn các ô lịch AI trong màn hình Thiết lập của Page — 19/10/2026

Theo yêu cầu, trong **Quản lý Page → Thiết lập → Nhịp nội dung & giờ gọi AI** đã **tạm ẩn**: **Bài chữ mỗi tuần**, dòng ghi chú kế hoạch, ô **Tôi tự đặt giờ đăng, AI chỉ viết caption** và **Tự bật lịch đăng sau khi Tèo kiểm tra kế hoạch**. Sáng 19/10 hai ô **Bật lịch gọi AI** và **Gọi AI tiếp theo (UTC+7) / Lặp sau** từng được hiện lại để thử, rồi theo yêu cầu **ẩn lại**; lượt AI của Page **du lịch toàn cầu** (hẹn 10:40) đã được **tắt** (giữ nguyên các thiết lập khác). Vì các ô này ẩn nên Page mới **không có cách bật AI từ giao diện**, và tab Duyệt caption chỉ có bài khi AI đã chạy; muốn dùng lại AI, đổi `HIDE_AI_SCHEDULE_CONTROLS` về `False` trong `teodub/page_ai_ui.py` (và ô **Bật lịch gọi AI** sẽ hiện), hoặc tạo bài thủ công ở **Lịch đăng → Thêm bài đăng…**. Vẫn hiện: **Video mỗi ngày**, **Giờ bài đầu mỗi ngày**, **Cách nhau (phút)**, nguồn video, mục tiêu, Tùy chọn AI, **Lưu thiết lập & hẹn giờ**, **Tạm dừng AI** và mọi tab khác.

Chỉ ẩn giao diện, không xóa mã hay dữ liệu: các giá trị đã lưu của từng Page (bật/tắt AI, giờ gọi AI kế tiếp, lặp, bài chữ, tự bật lịch, giờ cố định) **giữ nguyên** khi bấm Lưu, kể cả giờ gọi AI không bị đổi. Page mới lưu thì **AI tự chạy tắt**, bài chữ bằng 0 và không tự bật lịch đăng; muốn dùng AI viết caption thì dùng nút/tab hiện có, hoặc nhờ bật lại các ô (đổi `HIDE_AI_SCHEDULE_CONTROLS` về `False` trong `teodub/page_ai_ui.py`). Bấm **Khởi động lại, giữ công việc** để áp dụng. Các Page đã bật AI từ trước (ví dụ Minh châu sneaker) không bị đụng; dừng riêng bằng **Tạm dừng AI**.

## Sửa lỗi bài không đăng vì hộp thoại "Speak With People Directly" — 17/10/2026

Sau khi bấm Đăng Reel, Facebook có thể hiện hộp thoại **Speak With People Directly** (gợi ý thêm nút "Call Now") và **giữ bài lại cho đến khi bấm "Not now"**. Trước đây Tèo chỉ bấm "Not now" khi đang theo dõi kết quả, và chỉ nhận ra tên hộp thoại cũ (Talk directly to people); khi kiểm tra kết quả bị tắt, hộp thoại bị bỏ lại và bài không được đăng (16:15 và 16:20 ngày 17/10, trang Reels không có bài mới).

Nay ngay sau lần bấm Đăng duy nhất, Tèo bấm **chỉ nút "Not now"** của đúng hộp thoại này (khi kiểm tra kết quả tắt thì chờ tối đa 12 giây để hộp thoại hiện ra; khi bật thì bước theo dõi kết quả tự bấm) (nhận cả "Speak With People Directly", "Talk directly to people" và bản tiếng Việt; chữ "Call Now"/"Gọi ngay" không phân biệt hoa thường). Tèo **không bấm Add Button, không bấm Đăng lại**, và không mở tab kết quả khi kiểm tra đang tắt. Hộp thoại khác hoặc có nhiều hộp thoại cùng lúc thì không bị bấm. Nếu không có hộp thoại, bài vẫn chạy như cũ (khi kiểm tra tắt, thêm tối đa 12 giây giữ ô đăng). Đã kiểm thử **1.329/1.329** (toàn bộ nhóm đăng bài, Facebook, lịch, AI, chiến dịch).

Hai bài 16:15/16:20 đã ở trạng thái **Đã gửi — chưa xác minh**; Tèo không tự gửi lại. Kiểm tra Page → Reels; bài không lên thì tạo lịch mới (Bỏ lượt cũ và tạo lịch mới…) sau khi nạp bản sửa bằng **Khởi động lại, giữ công việc**. Đã kiểm thử bằng trang Facebook giả ngoại tuyến; **chưa chứng minh trên Facebook thật**.

## Tự đặt giờ đăng, AI chỉ viết caption — 17/10/2026

Trong **Quản lý Page → chọn Page → Thiết lập → Nhịp nội dung & giờ gọi AI**, tick **Tôi tự đặt giờ đăng, AI chỉ viết caption**, rồi nhập **Giờ bài đầu mỗi ngày (HH:MM)** và **Cách nhau (phút)**. Số bài mỗi ngày là ô **Video mỗi ngày**. Ví dụ giờ đầu 12:00, cách nhau 450 phút, 2 video/ngày: mỗi ngày đăng lúc 12:00 và 19:30. Dòng chữ bên dưới hiện luôn các giờ sẽ dùng.

Khi AI chạy, Tèo gửi cho AI danh sách giờ của bạn và yêu cầu **chỉ viết caption**. Nếu AI chọn giờ khác, thêm giờ, đặt hai video cùng một giờ hoặc viết bài chữ thì kế hoạch bị từ chối và không bật lịch. Giờ đã qua hoặc cách lịch có sẵn dưới 30 phút được bỏ qua; nếu không còn giờ trống, Tèo không gọi AI (không tốn hạn mức). Các bài của một ngày phải nằm trong cùng ngày (giờ đầu + khoảng cách × số bài < 24:00). Giờ cố định chỉ dùng cho video; bài chữ mỗi tuần phải bằng 0.

Để **không cần duyệt**, tick thêm **Tự bật lịch đăng sau khi Tèo kiểm tra kế hoạch**: Tèo bật lịch đúng các giờ bạn đã đặt với caption AI viết, không có bước đọc lại. Bỏ tick để duyệt ở tab **Duyệt caption** như trước. Ô giờ cố định để trống thì AI tự chọn giờ như cũ.

Chưa kiểm chứng đăng thật: Tèo vẫn chưa tự xác nhận được bài đã lên Facebook. Hãy mở Facebook xem bài đầu tiên trước khi để chạy tiếp. Bấm **Khởi động lại, giữ công việc** để nạp. Đã kiểm thử bằng dữ liệu tạm; không gọi AI hoặc đăng Facebook thật.

## Duyệt caption cho mọi Page — 16/10/2026

Tab mới **Duyệt caption** (tab cuối) gom mọi bài AI đề xuất **chưa duyệt** của tất cả Page về một chỗ, gồm bài từ Nguồn kênh, từ lượt AI theo giờ và từ chat Codex. Lọc theo Page ở ô **Page** phía trên.

1. Chọn một bài: khung bên phải hiện Page, video (**Mở video** để xem), caption và giờ đăng.
2. Sửa caption hoặc giờ (dạng `YYYY-MM-DD HH:MM`, giờ Việt Nam) rồi bấm **Lưu sửa**. Bản AI gốc vẫn được giữ; **Khôi phục bản AI** đưa về như cũ. Chuyển sang bài khác thì Tèo tự lưu phần đang sửa.
3. Chọn một hoặc nhiều bài (Ctrl/Shift), bấm **Duyệt & bật lịch bài đã chọn**, đọc lại danh sách rồi xác nhận. Tèo kiểm tra video, lưu lịch với **đúng caption bạn đã duyệt** và bật lịch; đến giờ mới đăng.
4. **Bỏ bài đã chọn…**: bài không được đăng, video trở lại kho để AI chọn ở lượt sau.

**Bài chưa duyệt không bao giờ được đăng.** Duyệt từng phần cũng được: bài còn lại vẫn chờ. Bài **Quá giờ** phải sửa giờ hoặc bỏ. Giờ đăng phải cách các lịch khác của Page ít nhất 30 phút. Nếu video đã đổi hoặc lưu lịch lỗi, bài chuyển **Cần kiểm tra** kèm lý do; không tự thử lại. Mở **Quản lý Page → Lịch đăng** để xem lịch đã lưu, hoặc bấm Bỏ để gỡ bài khỏi danh sách (không bật lịch nào).

Kế hoạch đã sửa hoặc duyệt từng bài ở đây sẽ không duyệt cả kế hoạch được nữa ở tab Kế hoạch cũ, để tránh đăng trùng; tab cũ hiện **Đã xử lý ở Duyệt caption**. Sửa thêm một lỗi: Page **không liên kết chat Codex** nay nhận được kế hoạch AI có bài thật (trước đó lượt AI dừng ở **Cần kiểm tra**).

Bấm **Khởi động lại, giữ công việc** để nạp. Đã kiểm thử **804/804** (dữ liệu tạm, lịch đăng thật trong thư mục thử, không đăng Facebook).

## Nguồn kênh tự động: kênh Douyin → nhiều Page, duyệt caption trước khi đăng — 16/10/2026

Tab mới **Nguồn kênh tự động** (tab cuối). Dán link kênh Douyin, chọn một hoặc nhiều Page (Ctrl để chọn nhiều), bấm **Lưu nguồn và quét ngay**. Giọng, Sub và model chốt theo tab **Video đơn** lúc bấm Lưu; video lưu vào hai thư mục của tab **Chiến dịch hàng loạt**.

Sau đó Tèo tự làm:
1. Quét kênh ngay và quét lại mỗi 24 giờ, chỉ lấy video chưa từng lấy: tối đa 10 video/lượt (chỉnh 1–20) và 20 video/ngày cho cả máy.
2. Chia video mới lần lượt cho các Page đã chọn; **mỗi video chỉ về một Page**. Tick **Cho phép đăng cùng một video lên mọi Page** nếu thật sự muốn đăng trùng (Meta dễ coi là nội dung lặp).
3. Tạo chiến dịch và lồng tiếng **khi Tèo rảnh** (không chen vào Video đơn, chiến dịch hoặc học giọng đang chạy). Tiến độ xem ở **Danh sách chiến dịch**.
4. Thêm chiến dịch vào **Đăng theo kế hoạch AI** của Page. Page chưa có cấu hình thì Tèo tạo sẵn: **mỗi sáng 06:30 AI soạn caption** cho 2 video trong ngày, theo kiểu ngắn gọn, giải trí, khoảng 8–20 từ. Không cần liên kết chat Codex nữa; liên kết vẫn dùng được nếu bạn muốn AI đọc chiến lược.

**Tèo không tự đăng bài từ Nguồn kênh.** Vào tab **Duyệt caption** để đọc, sửa caption, giờ, video rồi bấm duyệt và bật lịch. Page đang bật **Tự bật lịch đăng kế hoạch AI** sẽ không nhận video từ Nguồn kênh (báo **Cần kiểm tra**) để giữ đúng quy tắc duyệt trước.

Khi Douyin đòi đăng nhập mà không trả video nào, quét lỗi, tạo chiến dịch lỗi hoặc Page không nhận nguồn, nguồn chuyển **Cần kiểm tra** kèm lý do, rồi dừng quét đến khi bạn bấm **Tiếp tục**. Nếu bạn bấm Dừng hàng đợi, hoặc hàng đợi tạm dừng vì hết hạn mức hay lỗi đăng nhập, Tèo hiện thông báo đỏ và không tự chạy lại; bấm **Tiếp tục xử lý tự động** khi đã xử lý xong. **Tạm dừng** một nguồn sẽ dừng cả quét và xử lý video của nguồn đó; **Bỏ nguồn…** giữ nguyên chiến dịch, video và lịch sử.

Lưu ý: AI chỉ dùng video đã lồng tiếng xong. Nếu Tèo đang tắt lúc 06:30, lượt AI hôm đó bị bỏ và AI của Page tạm tắt (đổi giờ ở **Đăng theo kế hoạch AI**). Phần đăng bài vẫn cần sửa lỗi xác nhận kết quả (xem mục bên dưới).

Bấm **Khởi động lại, giữ công việc** để nạp. Đã kiểm thử **470/470** với dữ liệu tạm, trình quét/chiến dịch/AI giả lập; chưa quét kênh thật, gọi AI thật hay đăng bài.

## Tạo lượt mới cho video "Đã gửi — chưa xác minh" đã xóa — 16/10/2026

Khi tắt **Tự kiểm tra trạng thái sau khi gửi**, video đã bấm Đăng được ghi **Đã gửi — chưa xác minh**. Nếu bạn xóa video đó khỏi lịch, trước đây nút **Tạo lượt đăng mới…** trong **Thêm bài đăng…** bị khóa và báo nhầm là lượt đã xác nhận đăng hoặc đang xử lý. Bản sửa cho phép tạo lượt mới cho trường hợp này, vẫn theo đúng quy trình cũ: xem lại lượt cũ, tick hai xác nhận, lượt mới được lưu **chưa bật**.

**Trước khi tick xác nhận, mở Page trên Facebook để kiểm tra.** "Chưa xác minh" không có nghĩa là chưa đăng: Facebook có thể đã đăng video đó. Trên Page Minh châu sneaker, Tèo đã bấm Đăng 16 lần từ 14/10 đến 15/10 nhưng chưa xác nhận được lần nào, nên Page có thể đang có bài trùng. Lượt có bằng chứng đã đăng vẫn bị chặn như trước.

Bấm **Khởi động lại, giữ công việc** để nạp bản sửa. Nếu Tèo báo còn hộp soạn bài trong Chrome, kiểm tra và xử lý hộp đó trên Facebook trước. Kiểm thử dùng dữ liệu tạm: 389/389 kiểm thử liên quan đạt; chưa thao tác Facebook thật.

## Tạm tắt kiểm tra trạng thái bài đăng — 16/10/2026

Trong **Quản lý Page → Nâng cao**, bỏ chọn **Tự kiểm tra trạng thái sau khi gửi** để tạm dừng cả xác minh nền và nút kiểm tra lại. Tèo vẫn chuẩn bị, tải và bấm Đăng một lần theo lịch đã bật; sau đó ghi **Đã gửi — chưa xác minh** và tiếp tục bài kế tiếp. Trạng thái này chưa xác nhận bài đã xuất hiện trên Facebook.

Tùy chọn được lưu riêng cho các lần mở Tèo sau. Lịch, video, caption và bằng chứng lượt gửi được giữ; tắt kiểm tra không gửi lại bài cũ hoặc chạy bù lịch lỡ giờ. Có thể bật lại tùy chọn khi cần dùng chức năng xác minh.

## Đăng nhiều bài và xác minh độc lập — 15/10/2026

Tèo tách **gửi bài** khỏi **xác minh kết quả**. Khi một video đã gửi, lịch tiếp theo có thể tải/gửi trong lúc Tèo theo dõi video trước. Mỗi tài khoản vẫn thao tác một hộp tải/gửi tại một thời điểm; các bài chờ xác minh được kiểm tra xen kẽ, không giữ hàng đợi đăng. Giờ lịch là lúc bắt đầu chuẩn bị/tải, không phải cam kết Facebook xử lý xong đúng phút đó.

**Đã gửi — đang xác minh** khác **Đã xác nhận đăng**. Tèo chỉ xác nhận khi kết quả khớp Page ID, đầy đủ caption và video theo thời lượng/tỷ lệ. Hỗ trợ link Reel/video/bài thực tế và thẻ Post insights có đủ bằng chứng xuất bản. Link Insights giữ nguyên `content_id`; nếu chưa có permalink công khai, không đoán ra link Reel. Thiếu bằng chứng hoặc không phân biệt được hai lượt giống nhau thì giữ **Chưa xác minh đã đăng**, không báo thành công hay gửi lại.

Theo dõi video chạy nền tối đa 30 phút; bản ghi lượt gửi được giữ để nối lại kiểm tra sau khi mở Tèo. Trong **Lịch đăng**, bấm **Kiểm tra lại trạng thái…** cho lượt đã gửi cần kiểm tra để theo dõi lại, không tải video hoặc bấm Đăng lần nữa. Các bản nháp/lịch đã lỡ giờ chưa gửi không tự chạy bù khi khởi động lại.

## Kiểm tra lại trạng thái bài Facebook — 14/10/2026

Trong **Quản lý Page → Quản lý Page / Bài viết → Lịch đăng**, chọn lượt video đã gửi nhưng **Chưa xác minh đã đăng**, rồi bấm **Kiểm tra lại trạng thái…**. Cửa sổ hiển thị đúng lượt cũ, Page, caption, tệp và lịch sử gửi. Dán link trực tiếp bài/Reel thực tế, tick xác nhận bài thuộc đúng lượt đó, rồi bấm **Kiểm tra và cập nhật nếu đúng**. Chrome của tài khoản cần đang mở và không xử lý bài khác. Khi video chưa được chọn, bản nháp mới chưa gửi hoặc tài khoản còn đang bận, nút kiểm tra bị khóa.

Tèo đọc bài để đối chiếu Page ID, toàn bộ caption, video, thời lượng/tỷ lệ và lượt gửi. Khi đủ bằng chứng, kết quả được lưu cho đúng lịch/bài cũ cùng link thực tế; lịch đã xóa vẫn ở lịch sử. Thiếu bằng chứng thì giữ trạng thái cũ. Chức năng này không upload, bấm Đăng, bật lịch hoặc tạo lượt mới. Hai lượt cùng video/caption cần thời điểm đăng rõ trên Facebook để phân biệt; tick xác nhận không bỏ qua điều kiện này. Caption bị rút gọn hoặc video chưa đọc được metadata có thể chưa xác minh; mở đầy đủ bài rồi kiểm tra lại. Link Page/Insights không thay cho permalink bài.

Header **Đã xác nhận đăng** đếm các lịch hiện trong Tèo có kết quả đã xác minh, không phải tổng video trên Facebook. **Đã lưu, chưa bật** là bản nháp/lịch chưa chạy; không thể gán bài của lượt trước vào bản nháp này. Nếu không đọc được giờ đăng thực tế, Tèo giữ giờ đăng trống và lưu riêng giờ xác minh. Đóng cửa sổ đối soát chỉ dừng cập nhật cửa sổ; yêu cầu đã gửi tiếp tục đọc trong thời hạn 90 giây. Đóng Tèo/phiên Chrome hoặc hết hạn sẽ hủy yêu cầu.

**988/988 kiểm thử liên quan đạt** trong 347 giây, không lỗi hoặc skip, gồm Chrome cô lập với trang ngoại tuyến, giao diện Tk, giao dịch lưu dữ liệu, hủy/hết hạn, phản hồi sai, nhận nhầm bài gợi ý, đăng trùng và các luồng lịch/trình duyệt/AI/web cũ. Bản mới đã được nạp bằng **Khởi động lại, giữ công việc** lúc **23:05 ngày 14/10/2026**; giao diện thật đã mở đúng cửa sổ xem trước Video 2 (`test video 2`). Kiểm tra dữ liệu trước/sau giữ nguyên cài đặt, 13 lịch, 19 bài, 66 sự kiện và tệp chiến dịch. Chưa gửi đối soát bài Facebook thật hoặc đánh dấu các video của người dùng là đã đăng. Bằng chứng ở `work/20261014-browser-status-reconcile/verification.json`.

## Xác nhận bài đã đăng và chờ lượt trước — 14/10/2026

Phần đọc kết quả trình duyệt hỗ trợ thông báo **Your reel is published** và link **Content Insights**. Link Insights chỉ dùng để tìm bài thật; Tèo vẫn phải đối chiếu đúng Page ID, caption đầy đủ và video trước khi báo **Đã đăng**. Không suy ra URL Reel từ mã `content_id`. Nếu giao diện không cung cấp link/bằng chứng đủ rõ, kết quả vẫn cần kiểm tra và Tèo không bấm Đăng lần nữa.

Lịch đã đến giờ khi Tèo đang chạy và phải đợi một lượt trước cùng tài khoản được ghi nhận là đang chờ. Sau khi lượt trước được xác nhận đăng thành công, Tèo chạy tiếp theo thứ tự và giữ khoảng cách giữa các lịch. Nếu lượt trước chưa rõ kết quả, những lịch đã chờ lượt đó dừng để kiểm tra. Lịch lỡ giờ do ứng dụng dừng vẫn cần đổi giờ; việc khởi động lại không tự chạy bù hoặc gửi lại.

Bản sửa giữ nguyên giờ lịch đã lưu, video, caption, lịch sử và quy tắc chống đăng trùng. Nó không tự sửa kết quả các lượt cũ. Với Video 1 đã có trên Facebook, kiểm tra bài thật trước khi thao tác lại. Video 2 chưa từng gửi có thể đổi giờ rồi bật lịch. Thông báo **STUDIOCANAL blocked your reel: test333** cần được xử lý trên Facebook; bản sửa không gỡ chặn bản quyền.

**899/899 kiểm thử liên quan đạt** trong 284 giây, không lỗi hoặc skip. Đối chiếu trước/sau giữ nguyên cài đặt, 10 lịch, 16 bài, 58 sự kiện và tệp chiến dịch. Bản mã **chưa được nạp vào Tèo đang mở**: điều khiển cửa sổ báo `failed to activate captured window` ở hai lần thử. Bấm **Khởi động lại, giữ công việc** trong Tèo để nạp; không cần xóa lịch hoặc tạo lại video chỉ để cập nhật mã.

Bằng chứng trong `work/20261014-publication-receipt-queue/`. Bộ lọc và luồng kết quả được kiểm bằng Chrome cô lập, trang mẫu ngoại tuyến và dữ liệu tạm; việc đó không thay cho xác minh giao diện Insights thật, hiện còn chờ xác nhận mở thông báo. Lần kiểm tra chỉ đọc thấy Facebook còn một thông báo thành công mới sau thông báo chặn `test333`, nên chặn bản quyền và việc bài đã được xuất bản cần được đối chiếu riêng, không suy luận rằng chắc chắn chưa có bài.

## Đăng lại video sau khi xóa khỏi lịch

Trong **Lịch đăng → Thêm bài đăng… → Video từ chiến dịch**, video đã xóa và chưa từng thao tác có thể chọn lại bình thường. Nếu video có **Lượt cũ đã xóa — cần xác nhận đăng lại**, chọn video rồi bấm **Tạo lượt đăng mới…**.

Màn hình mới giữ video/caption cũ, hiển thị kết quả và lịch sử lượt cũ. Kiểm tra Facebook rằng video chưa đăng hoặc hẹn giờ; lưu hoặc bỏ phần soạn cũ rồi đóng phiên Chrome của tài khoản. Chọn giờ mới và tick hai xác nhận, bấm **Tạo lượt mới (chưa bật)**. Đóng cửa sổ, chọn dòng mới trong Lịch đăng rồi bấm **Bật phần đã chọn**. Caption đang nhập ở cửa sổ Thêm bài đăng không thay caption cũ trong luồng này.

Tèo giữ nguyên lượt cũ và bằng chứng đã có, tạo một mã lịch/bản nháp mới chưa bật. Video có bằng chứng đã đăng thành công không được chạy lại qua luồng này. Lịch còn đang dùng vẫn báo **Đã có lịch trên Page này**; muốn thay bằng lượt mới phải xử lý/xóa lịch đó trước.

Đã nạp bản sửa lúc **16:34 ngày 14/10**; **864/864 kiểm thử liên quan đạt**. Giao diện thật đã cho chọn Video 2 đã xóa, bật nút tạo lượt mới và mở xem trước đúng Page/video/caption. Giờ **17:14** được giữ lại, các xác nhận chưa tick và nút lưu chưa bật. Kiểm tra dữ liệu trước/sau giữ nguyên cài đặt, lịch/bài/sự kiện và tệp chiến dịch; chưa tạo hoặc đăng lượt thật. Bằng chứng trong `work/20261014-page-schedule-new-attempt/`.

## Xóa video khỏi danh sách lịch đăng — 14/10/2026

Trong **Quản lý Page / Bài viết → Lịch đăng**, chọn một hoặc nhiều video, bấm **Xóa video** rồi xác nhận. Video biến mất khỏi danh sách lịch đang dùng. Tick **Hiện video đã xóa** để xem lại hồ sơ cũ.

Nút này chỉ xóa khỏi danh sách Tèo: tệp video, caption, lịch sử và bài trên Facebook vẫn được giữ. Lịch đang thực sự xử lý phải đợi kết thúc mới xóa được. Lịch chưa từng thao tác có thể thêm lại bằng **Thêm bài đăng…**; lượt đã chuẩn bị/gửi hoặc chưa rõ kết quả vẫn giữ khóa chống trùng. Xóa khỏi danh sách không tự cho phép đăng lại một lượt đã gửi.

Đã nạp qua khởi động lại an toàn lúc **16:06 ngày 14/10**. **813/813 kiểm thử liên quan đạt**. Kiểm tra giao diện thật xác nhận nút hoạt động với video báo lỗi và mở đúng hộp xác nhận; đã chọn **No** nên chưa xóa dòng nào của người dùng. Cài đặt, bảng lịch/bài/sự kiện và tệp chiến dịch trước/sau giữ nguyên. Bằng chứng trong `work/20261014-page-schedule-delete/`.

## Bỏ lượt chuẩn bị cũ và tạo lịch mới — 14/10/2026

Trong **Quản lý Page → Quản lý Page / Bài viết → Lịch đăng**, nút **Bỏ lượt cũ và tạo lịch mới…** giúp xếp lại các video đã dừng ở bước chuẩn bị, có lỗi hoặc chưa rõ kết quả. Lượt cũ và lịch sử vẫn được lưu; video, caption và Page ID giữ nguyên. Đây không phải nút xóa bài đã đăng trên Facebook.

1. Kiểm tra Facebook rằng video chưa được đăng hoặc hẹn giờ. Chọn một hoặc nhiều lịch đủ điều kiện rồi bấm **Bỏ lượt cũ và tạo lịch mới…**.
2. Đọc tên Page, Page ID, video, caption/hashtag và kết quả lượt cũ trong cửa sổ xem trước.
3. Chọn giờ đầu trong tương lai và khoảng cách giữa các bài.
4. Nếu phiên Chrome của tài khoản còn mở, bấm **Đóng phiên Chrome cũ** trong cửa sổ này. Tèo hỏi xác nhận trước khi đóng đúng phiên của tài khoản đang quản lý; nội dung nháp chưa lưu trên Facebook có thể mất. Đợi báo phiên đã đóng. Nếu tài khoản còn công việc khác đang xử lý, Tèo chặn việc đóng.
5. Tick hai xác nhận: đã kiểm tra video chưa đăng/hẹn giờ, và đã lưu hoặc đồng ý bỏ phần soạn cũ trên Facebook. Tick không tự đóng trình duyệt; vẫn phải đợi phiên Chrome cũ đóng thực sự.
6. Bấm **Tạo lịch mới (chưa bật)**. Lịch được lưu **chưa bật**; đóng cửa sổ, chọn những lịch mới rồi bấm riêng **Bật phần đã chọn** khi sẵn sàng đăng.

Tèo tạo bản nháp mới và giữ lịch sử lượt cũ, vẫn dùng cùng mã lịch/mã nguồn để tránh tạo hai lịch cho một video trên Page. Mở cửa sổ, đóng Chrome hoặc xem trạng thái không tự tạo lịch hay đăng bài. Không chuyển sang Page khác, không thay video/caption, không chạy lại lượt đã sang duyệt/gửi/đã đăng. Nút **Khôi phục lịch đã chọn…** cũ vẫn có cách hoạt động như trước.

Chức năng đã được nạp qua khởi động lại an toàn lúc **15:10 ngày 14/10**. Bộ kiểm thử liên quan đạt **752/752**; kiểm tra dữ liệu trước/sau xác nhận cài đặt, bảng lịch/bài/sự kiện và tệp chiến dịch giữ nguyên. Giao diện thật đã mở xem trước với đúng Page/video/caption; hai xác nhận vẫn chưa tick và nút lưu bị khóa. Việc kiểm tra này chưa đóng phiên Chrome thật, tạo lại lịch, bật lịch hoặc đăng Facebook. Bằng chứng ở `work/20261014-page-schedule-recreate/`.

## Nhận diện nút gỡ video Facebook — 14/10/2026

Facebook có thể đặt tên nút gỡ video là **Remove post attachment**. Tèo nhận tên này và các tên cũ khi nút nằm trong đúng hộp soạn bài, kể cả khi Facebook dùng nhãn liên kết thay vì chữ hiện trên nút. Vẫn cần đúng một video xem trước có nút gỡ, bằng chứng tải hoàn tất, đúng Page/caption và một nút **Next/Đăng** đang bật. Chữ trong caption, nhãn từ hộp khác, nút gỡ bị ẩn hoặc tên nút không rõ không được dùng để nhận video. Nhãn tham chiếu bị ẩn hợp lệ vẫn được đọc theo tên truy cập của Chrome.

Vì vậy, giao diện có **100%** và **Your reel is safe to publish!** sẽ không còn bị kẹt chỉ vì Facebook đổi tên nút gỡ. Bản nhận diện tải xong trước đó đã hỗ trợ hai dấu hiệu này nhưng chưa nhận tên **Remove post attachment**. Khi chờ, Tèo phân biệt thiếu bằng chứng tải xong, thiếu nút gỡ/xem trước, tệp hoặc video thay đổi; không dùng một thông báo chung cho mọi nguyên nhân.

Bản sửa chỉ ở phần nhận diện và thông báo chờ trong `teodub/facebook_web_post.py`. Kiểm tra tài khoản/Page, toàn bộ caption, mã SHA256/tệp gốc, một lần tải và một lần bấm Đăng, lịch đăng, khôi phục lịch và tải/dịch/lồng tiếng giữ nguyên. Kiểm thử dùng Chrome ngoại tuyến và dữ liệu tạm; chưa chứng minh một lượt đăng Facebook thật sau bản sửa. Lượt Video 1 bắt đầu lúc 11:02 đã hết thời gian chờ lúc 11:17 và có kết quả chưa rõ; không tự đặt lại hoặc đăng lại lượt đó.

Trạng thái cập nhật lúc **14:28 ngày 14/10**: người dùng đã **Khởi động lại, giữ công việc** thành công và bản sửa **đã được nạp**. Tèo mở lúc 14:28:13, báo sẵn sàng, không bận; mã nguồn vẫn là bản đã đạt **725/725 kiểm thử**, không lỗi hoặc bỏ qua. Lần khởi động lại lúc 14:21 trước đó bị chặn vì còn hộp soạn bài chưa đóng; đây là trạng thái cũ đã được xử lý. Xác nhận nạp mã chưa phải là xác nhận bài đã đăng; chưa có kiểm chứng đăng Facebook thật sau lần nạp này.

Lượt **test333 · Video 3**, hẹn 14:07, dừng lúc 14:12 với lỗi **Không xác định duy nhất hộp Tạo bài viết (0 mục)… chưa bấm Đăng**, trước khi nạp bản sửa. Tại lần kiểm tra 14:17, Facebook hiện **Create post / Loading**, chưa có ô nhập nội dung. Chưa có bản duyệt hoặc bằng chứng đăng thành công; thông báo kết nối mạng đã khôi phục không đủ để kết luận nguyên nhân, và chưa có bằng chứng video bị chặn bản quyền. Đối chiếu sau lần khởi động lại thành công lúc 14:28 với bản trước cập nhật lúc 14:20 xác nhận cài đặt, dữ liệu lịch/bài/sự kiện và tệp chiến dịch giữ nguyên. Bản sửa không tự đặt lại hoặc đăng lại lượt có kết quả chưa rõ. Báo cáo xác nhận nạp và giữ dữ liệu ở `work/20261014-facebook-latest-diagnosis/confirmed-deployment.json`.

## Khôi phục lịch video đã lỗi hoặc tạm dừng — 14/10/2026

Trong **Quản lý Page → Quản lý Page / Bài viết → Lịch đăng**, có nút **Khôi phục lịch đã chọn…** để xếp lại một hoặc nhiều video đã dừng ở bước chuẩn bị trong Chrome. Có thể dùng khi tên Page đã được sửa đúng hoặc lượt tải video cũ chưa rõ kết quả. Chỉ áp dụng cho **cùng Page ID bằng số**, không chuyển lịch sang Page khác.

1. Kiểm tra trên Facebook rằng các video cần khôi phục **chưa được đăng hoặc hẹn giờ**. Đóng hộp soạn bài cũ, rồi bấm **Đóng / Lưu phiên tài khoản này** trong Tèo và đợi Chrome đóng.
2. Mở **Lịch đăng**, chọn những dòng cần khôi phục bằng Ctrl/Shift; nếu cả ba dòng đều đủ điều kiện, bấm **Chọn tất cả → Khôi phục lịch đã chọn…**.
3. Đọc tên Page cũ và hiện tại, Page ID, tệp video, mã kiểm tra và toàn bộ caption/hashtag sẽ giữ nguyên.
4. Chọn giờ tương lai cho **Bài đầu (UTC+7)** và **Cách nhau (phút)**; mặc định cách nhau 30 phút. Ví dụ 11:00 thì ba bài lần lượt 11:00, 11:30, 12:00.
5. Tick cả hai xác nhận đã kiểm tra Facebook và đã đóng hộp soạn/phiên Chrome cũ, rồi bấm **Khôi phục lịch (chưa bật)**.
6. Sau khi khôi phục thành công, đóng hộp khôi phục, chọn các lịch vừa khôi phục và bấm riêng **Bật phần đã chọn**. Giữ Tèo chạy, máy không ngủ và Facebook còn đăng nhập đến giờ.

Tèo giữ nguyên video thành phẩm, caption/hashtag, mã nguồn, mã lịch và lịch sử lượt cũ; tạo bản nháp mới theo tên/link Page hiện tại. Lịch được lưu **chưa bật**, không tự đăng khi khôi phục hoặc mở lại ứng dụng. Mỗi video vẫn chỉ có một lịch trên Page. Nếu một dòng lỗi, kết quả ghi rõ từng dòng; bấm lưu lại trong cùng hộp chỉ thử các dòng chưa khôi phục, không lặp lại những dòng đã thành công.

Lịch đã sang bước duyệt/gửi/đang đăng, đã có bằng chứng hoặc link đăng, khác Page ID, đổi caption/video hoặc còn thao tác Chrome sẽ bị chặn. Không tự bỏ trạng thái chưa rõ kết quả; hai xác nhận là bước bạn kiểm tra thực tế trước khi cho phép tạo lượt chuẩn bị mới. Không cần tải hoặc lồng tiếng lại video.

Bản khôi phục lịch và nhận diện tải xong đã được nạp qua **Khởi động lại, giữ công việc**. Ba lịch gốc vẫn tạm dừng, chưa được khôi phục hoặc đăng lại. Đối chiếu trước/sau cập nhật giữ nguyên thiết lập, lịch, bài viết, sự kiện và 14 tệp chiến dịch. Bộ kiểm thử liên quan đạt **683/683**, kiểm thử khôi phục riêng đạt **27/27 backend** và **10/10 giao diện**; các nhóm có phần trùng nhau nên không cộng số lượng. Đây là kiểm thử dữ liệu tạm/giao diện ngoại tuyến, chưa chứng minh một lượt tự đăng Facebook thực tế sau bản sửa.

## Nhận diện video Facebook tải xong — 14/10/2026

Tèo nhận thêm thanh tiến trình đã hoàn tất và giao diện Reel có huy hiệu **100%**, thông báo **Your reel is safe to publish!**, đúng một video xem trước có nút gỡ và một nút **Next/Đăng** đang bật. Chữ trong caption, phần tử ẩn, tiến trình chưa rõ hoặc chưa xong, cờ đang xử lý và lỗi tải tệp không được dùng để xác nhận hoàn tất. Tệp gốc được đối chiếu bằng dữ liệu thực khi chọn và giữ nguyên video đó khi Facebook nhả ô chọn tệp. Dòng tiến độ nói rõ điều kiện còn thiếu khi chờ.

Các bước xác minh tài khoản/Page, caption, mã kiểm tra tệp, duyệt trước khi đăng, chống tải lại/đăng trùng, lịch đăng và thời gian chờ giữ nguyên. Kiểm thử dùng Chrome ngoại tuyến với hồ sơ và dữ liệu tạm; chưa chứng minh một lượt tự đăng thực tế trên Page này sau bản sửa. Bản sửa đã được nạp qua khởi động lại an toàn ngày 14/10/2026 cùng chức năng khôi phục lịch ở trên. Khi cập nhật sau này, nếu Chrome còn hộp bài viết đang soạn, giải quyết hoặc lưu bản nháp đó trước khi khởi động lại. Không tự chạy lại lượt đã có kết quả chưa rõ.

## Sửa nhận dạng trên GTX 1050 / GPU ít bộ nhớ — 13/10/2026

Whisper kiểm tra chế độ tính toán được GPU hỗ trợ trước khi nạp, thay vì ép `int8_float16` cho mọi card NVIDIA. GTX 1050 2 GB dùng Whisper Small với `int8_float32` khi chọn **Tự động** cho phiên mới. Small/Medium/Large v3 được chọn theo ngưỡng bộ nhớ GPU bảo thủ; CPU dùng Medium khi Tự động không chọn được GPU phù hợp. Dòng trạng thái hiển thị model, GPU/CPU và chế độ thực tế.

Phiên cũ đã chốt model vẫn giữ model đó: ví dụ Large v3 trên card 2 GB được chọn chạy CPU trước khi nạp, có thông báo rõ. Không đổi phụ đề đã hoàn tất, không ghi lại request cũ và không tự thử CPU sau một lỗi GPU đang xử lý. Cùng cách chọn áp dụng cho Video đơn, Chiến dịch, Học giọng và Nội dung & kịch bản. Khi sửa mã, worker mới tự đọc bản sửa; có thể bấm **Thử lại video lỗi** sau khi kiểm tra. Phiên Large v3 chạy CPU có thể chậm; muốn Small cho video cũ cần dùng chức năng cập nhật cấu hình từ Video đơn để tạo phiên cấu hình riêng.

## Phân tích nội dung để làm video mới — 12/10/2026

Vào **Học giọng & phong cách → Video mẫu & nghe thử**, thêm link Facebook/YouTube (hoặc Douyin/TikTok, tệp trên máy). Mở **Nội dung & kịch bản**, nhập chủ đề, người xem và thông tin/tư liệu bạn có, chọn thời lượng video mới rồi bấm **Phân tích & viết kịch bản**. Không cần chạy bước học giọng trước. Nhận 1–3 video mỗi lượt; dùng mốc bắt đầu ở Video mẫu và giới hạn đọc riêng 3–1800 giây/video (mặc định 300). Đây là phân tích nội dung và ảnh lấy mẫu, không chỉ nhận dạng lời thoại.

Tèo trích tối đa 12 ảnh/video: nhiều ảnh đầu đoạn và các ảnh trải đều phần còn lại, lưu mốc thời gian thực. Whisper nhận dạng bằng GPU khi sẵn sàng; không tự chuyển sang CPU khi GPU lỗi. Video không có tiếng vẫn được phân tích bằng hình. Sau đó gửi tối đa 36 ảnh và lời thoại tới **Codex bằng tài khoản ChatGPT hiện có**, dùng hạn mức AI của tài khoản; không phải toàn bộ bước này chạy AI trên GPU máy. Mục học giọng cũ vẫn chạy trên máy. Mở tab/lịch sử không tự gọi AI; lỗi không tự gửi lại.

Kết quả gồm chủ đề, mở đầu, bố cục câu chuyện, cảnh quay, chữ trên hình, cách dựng và bài học áp dụng, có dẫn ảnh/mốc thời gian, phân biệt điều quan sát với suy luận. Kịch bản mới 15–300 giây có lời đọc Việt, danh sách cảnh cần quay, chữ màn hình, cách dựng và tư liệu cần chuẩn bị. Không xem liên tục mọi khung hình, không đo chính xác nhịp cắt từ ảnh thưa, không tự nghe nhạc/giọng qua Codex, không xác minh số view hoặc cam kết hiệu quả. Báo cáo ghi rõ khoảng đã phân tích, phần còn thiếu và lượng token nếu Codex trả về.

Lịch sử nằm ở **Đã lưu**; **Ảnh đã phân tích** cho xem lại ảnh thực. **Mở thư mục kết quả** có báo cáo `noi-dung-va-kich-ban.txt`, `content.json`, ảnh, bằng chứng và `loi-doc-moi.srt` với thời gian dự kiến. Các lượt riêng trong `voice-learning/content`; không ghi đè video nguồn, giọng đã lưu hay lịch Page. Đây chưa phải tính năng tự quay/tự dựng hình thành MP4.

Sau khi chuẩn bị video hình theo danh sách cảnh, bấm **Chọn video hình & dùng lời đọc…**: Tèo gắn bản lời mới với chính tệp bạn chọn, chuyển sang **Video đơn** và giữ cấu hình giọng hiện tại. Bấm **Xem / sửa lời thoại**, rồi **Lồng tiếng và xuất video**. Không tự lấy video tham khảo làm thành phẩm. Video hình cần đủ dài theo kịch bản; SRT là mốc dự kiến cần chỉnh theo bản dựng. Tèo không tự đăng bài hay huấn luyện lại trọng số từ lượt phân tích này.

Kết nối ảnh dùng tùy chọn đính kèm chính thức `codex exec --image`, phản hồi có schema và vô hiệu công cụ thực thi trong lượt phân tích; tham khảo [OpenAI Docs — CLI reference](https://learn.chatgpt.com/docs/developer-commands?surface=cli).

## Phân tích và học video bằng link — 11/10/2026

Vào **Học giọng & phong cách**, dán link video **Facebook/YouTube** (cũng nhận Douyin/TikTok) hoặc đoạn chia sẻ vào **Học video bằng link**, rồi bấm **Phân tích video mẫu**. Có thể bấm **Thêm link** trước để ghép nhiều nguồn, tối đa 10 link/tệp trong một lượt. Link trùng được gộp; có thể dùng chung link và tệp trên máy. Nhận YouTube watch/Shorts/youtu.be, Facebook Reel/Watch/video/link share r-v/fb.watch; link kênh/danh sách/Page và livestream đang phát chưa được nhận ở đây. Tèo tải vào thư mục riêng của lượt phân tích, rồi nhận dạng trên máy bằng Whisper, ưu tiên GPU nếu sẵn sàng. Không gửi video/lời thoại đến Codex hoặc dịch vụ AI trả phí.

Facebook/YouTube dùng bộ tải công khai riêng, ghép hình và tiếng vào MP4 khi nguồn tách luồng, chọn hình tối đa 720p và giữ âm thanh tốt nhất phù hợp. Giới hạn tổng tải 2 GB, có Dừng và báo nguyên nhân khi video yêu cầu đăng nhập/xác minh, không công khai hoặc bị nền tảng giới hạn. Bộ tải này chưa dùng phiên Facebook đã đăng nhập trong phần quản lý Page; với nguồn không tải được, nhập tệp đã tải trên máy. Tên video và link được lưu cùng báo cáo và hồ sơ giọng.

Môi trường dùng `yt-dlp[default]==2026.8.19` để có EJS tương thích. Node.js 22.23.3 được đặt riêng trong `tools/node-runtime` (nguồn chính thức Node.js, đã đối chiếu SHA-256 và giữ LICENSE), không thay Node.js hệ thống. Có thể dùng Node.js trên máy từ phiên bản 22 nếu không có bản riêng. Cấu hình dựa trên [hướng dẫn EJS chính thức](https://github.com/yt-dlp/yt-dlp/wiki/EJS); không tự tải script EJS từ xa mỗi lượt.

Chọn mốc bắt đầu và độ dài **3–300 giây cho mỗi nguồn** (mặc định 120 giây). **Báo cáo video** hiển thị thông số hình/thời lượng, lời thoại có mốc thời gian gốc, ngôn ngữ, nhịp đọc, khoảng nghỉ và gợi ý phong cách từ số đo âm thanh. Báo cáo và bản tải được lưu trong `voice-learning/analyses`; mở lại qua **Lượt phân tích đã lưu**, hoặc **Sao chép báo cáo**. Luồng học giọng này phân tích âm thanh và thông số hình; muốn phân tích cảnh quay, dùng mục **Nội dung & kịch bản** ở trên. Lời nhận dạng có thể sai; nhạc nền/người nói chưa được tự tách, và không huấn luyện lại trọng số mô hình.

Chọn đoạn giọng → **Nghe mẫu gốc** → **Tạo & nghe lời mới** → đặt tên → **Lưu giọng & phong cách**. Giọng tự được chọn ở **Video đơn** và dùng cho chiến dịch tạo mới; có thể tick áp dụng cho chiến dịch đang chọn từ video tiếp theo. Hồ sơ giữ bản âm thanh riêng và link nguồn, vẫn dùng được khi bản tải gốc bị di chuyển. **Dừng xử lý mẫu** hủy cả tải/nhận dạng; lỗi một nguồn được báo riêng và các nguồn đọc được vẫn có thể lưu mẫu. Khi nền tảng giới hạn tải, có thể dùng tệp đã tải trên máy.

## Đăng ngay trong quản lý Page — 11/10/2026

Vào **Quản lý Page → Quản lý Page / Bài viết → Lịch đăng**, chọn đúng một bài/video rồi bấm **Đăng ngay**. Tèo tự mở Chrome đúng tài khoản, chuyển sang danh tính Page, nhập caption/hashtag đã lưu, tải tệp, đi qua các bước thước phim, bấm Đăng và lấy kết quả. Không có bước duyệt thứ hai hoặc yêu cầu bạn bấm Tiếp/Đăng trong Chrome. Với lịch đã bật, đến giờ Tèo tự làm cùng quy trình này.

Bảng tự cập nhật mỗi giây: kiểm tra video → chuẩn bị/tải trong Chrome → đang đăng → đã xác nhận đăng hoặc cần kiểm tra với lý do. Khi Chrome có thông báo tiến độ, Tèo lưu và hiển thị bước hiện tại; không tạo phần trăm tải giả. Bấm **Mở bài / Page** để xem kết quả; chỉ báo đã xác nhận đăng khi có bằng chứng/link bài được lưu.

Tèo tự nhập lại caption cố định ở bước Cài đặt thước phim nếu Facebook không chuyển đầy đủ lời từ bước trước. Chấp nhận khác biệt xuống dòng do Facebook hiển thị nhưng vẫn đối chiếu toàn bộ lời và hashtag. Sau khi bấm Đăng video, chờ kết quả tối đa 5 phút; với link Reel mới, Tèo tự mở kết quả và đối chiếu Page ID, tên Page, toàn bộ caption và video trước khi lưu thành công. Việc đối chiếu do Tèo thực hiện, không cần bạn thao tác. Cần giữ máy thức, Tèo chạy và phiên Facebook còn đăng nhập; khi Facebook yêu cầu xác minh hoặc chưa rõ kết quả, tool báo nguyên nhân và giữ bài để tránh đăng trùng.

Đăng ngay dùng **cùng lịch và bản nháp**, chuyển giờ của bài đó sang hiện tại; không tạo bản sao, không đăng lại vào giờ cũ, giữ lịch khác. **Chi tiết / Tùy chọn** giữ lịch sử đăng ngay và giờ cũ. Chỉ áp dụng lịch chưa có lượt xử lý; bài đã đăng, đang tải/đăng hoặc chưa rõ kết quả bị chặn. Tài khoản đang xử lý bài khác cần đợi xong. Đăng ngay không tự thử lại lỗi và không gọi AI để viết lại caption.

Bấm **Khởi động lại, giữ công việc** để nạp nút mới và bản sửa thời gian chờ tải video. Nếu Tèo báo còn hộp soạn bài trong Chrome, kiểm tra kết quả trên Facebook rồi xử lý/đóng hộp đó trước khi cập nhật; không ép đóng Chrome hay chạy lại bài chưa rõ kết quả.

## Sửa thời gian chờ chuẩn bị video đăng Page — 11/10/2026

Bước chuẩn bị video qua Chrome có tối đa **20 phút**: chờ tải/xử lý tệp tối đa 15 phút, còn thời gian cho mở Chrome, xác minh Page và chuyển bước thước phim. Giới hạn trước đây 6 phút có thể cắt ngang video lớn dù bước tải vẫn còn thời gian. Các thao tác đăng bản đã duyệt/xem lại/bài chữ giữ giới hạn 6 phút. Khi hết hạn, thông báo kèm bước cuối; yêu cầu được hủy và không tự gửi lại.

Bấm **Khởi động lại, giữ công việc** để nạp thay đổi. Lượt đã bị ngắt khi nhập/tải video vẫn cần kiểm tra trong Chrome hoặc trên Page trước khi xử lý tiếp; sửa mã không tự đặt lại hoặc đăng lại những lượt chưa rõ kết quả.

## Học giọng & phong cách từ video — 11/10/2026

Tab riêng **Học giọng & phong cách** chạy trên máy, không gửi video/lời mẫu tới Codex hoặc dịch vụ giọng trả phí. Đây là thư viện giọng theo mẫu, **chưa huấn luyện lại trọng số mô hình**.

1. Bấm **Thêm video / âm thanh…**, chọn tối đa 10 tệp. Chọn mốc bắt đầu và thời lượng phân tích (3–300 giây/tệp, mặc định 120 giây).
2. Chọn Whisper Small / Medium / Large v3 rồi **Phân tích video mẫu**. Whisper tự nhận ngôn ngữ và dùng GPU khi có CUDA; lỗi GPU được báo, không tự chuyển mô hình. Lần đầu có thể cần tải mô hình miễn phí.
3. Chọn đoạn trong bảng, **Nghe mẫu gốc**. Tèo hiển thị lời nhận dạng, nhịp/cao độ/khoảng nghỉ ước tính và gợi ý tên phong cách dựa trên nhịp. Các số đo có thể bị nhạc nền ảnh hưởng; chưa tự tách nhạc hoặc nhận diện từng người nói. Chọn đoạn có một người nói rõ.
4. Nhập lời mới rồi **Tạo & nghe lời mới** để kiểm tra bằng VieNeu Turbo/Nano. **Nghe lại** dùng tệp đã tạo; đổi mẫu/lời/model cần tạo lại. Bộ giọng và đường tạo âm thanh giống Video đơn; nhận dạng mẫu dùng GPU, bộ giọng theo cấu hình hiện có của Tèo.
5. Đặt tên giọng và phong cách, **Lưu giọng & phong cách**. Tèo tự chọn hồ sơ vừa lưu và bộ giọng đã chọn tại **Video đơn**, lưu lựa chọn để mở lại, đồng thời dùng cho chiến dịch tạo mới. Tốc độ ban đầu 1,00×, giữ nhịp mẫu; có thể chỉnh tiếp. Có thể lưu nhiều phong cách cùng một tên giọng; mỗi hồ sơ dùng một mẫu 3–8 giây riêng. Phong cách được lấy từ âm thanh tham chiếu, không chỉ đổi tốc độ. Cuộn phần Video mẫu để tới các ô lưu hồ sơ trên màn hình thấp.
6. Để đổi riêng giọng của chiến dịch đã có, chọn chiến dịch tại **Chiến dịch hàng loạt**, rồi tick **Áp dụng cả cho chiến dịch đang chọn** trước khi lưu hồ sơ. Tèo áp dụng từ video chạy tiếp theo; giữ nguyên video đang chạy, thành phẩm và các cấu hình khác của chiến dịch. Khi đổi sang chiến dịch khác, mục tick tự tắt để bạn chọn lại đúng nơi. Trong **Giọng đã lưu**, chọn hồ sơ rồi **Dùng để tạo video** để dùng lại giọng cũ, cũng theo tùy chọn này. Nút **Lấy cấu hình từ tab Video đơn** vẫn dùng khi muốn sao chép toàn bộ cấu hình.

Lượt phân tích được lưu để mở lại. Hồ sơ trong `voice-learning/profiles` giữ bản âm thanh riêng, vẫn dùng được khi video nguồn/lượt phân tích không còn; mẫu đã dùng không bị ghi đè bởi hồ sơ mới. Mẫu hỏng hoặc thay đổi sẽ bị chặn thay vì tự chọn giọng khác. Nút **Dừng xử lý mẫu** dừng tiến trình riêng và giải phóng mô hình; khởi động lại có bàn giao sẽ đợi xử lý mẫu hoàn tất/dừng. Mở tab không tự chạy AI.

Lưu hồ sơ không tự bắt đầu tạo video. Khi chiến dịch đang xử lý, vẫn có thể chọn giọng đã lưu hoặc lưu đoạn đã phân tích cho lượt tiếp theo; phân tích/nghe thử AI mới sẽ đợi máy nghỉ. Nếu giọng đã lưu nhưng chưa áp dụng được, thông báo phân biệt hai bước; chọn hồ sơ trong thư viện để thử áp dụng lại, không cần tạo bản lưu trùng.

Đã kiểm tra nhận dạng GPU và tạo lời mới từ mẫu ADAM đã được cung cấp, cùng kiểm thử dữ liệu giả cho lựa chọn giọng/campaign/cache/Dừng/khởi động lại. Chất lượng giống giọng và biểu cảm vẫn cần nghe duyệt; không bảo đảm giống hoàn toàn người trong video. Nền tảng: [VieNeu — giọng theo mẫu](https://huggingface.co/pnnbao-ump/VieNeu-TTS-v3-Turbo), [faster-whisper](https://github.com/SYSTRAN/faster-whisper).

## Luồng đăng thước phim và lịch sáu ngày — 11/10/2026

Video có luồng **Tạo bài viết → Chỉnh sửa thước phim → Cài đặt thước phim → Đăng** đã được bổ sung. Tèo nhận đúng nút gỡ file đính kèm và dấu tải xong, giữ video xem trước qua hai bước Tiếp, đối chiếu lời và danh tính Page trước khi bấm Đăng một lần. Kiểm tra đầu trang dùng nút tên Page khi Facebook có nhiều tên tác giả trong bảng tin. Tải video lớn có tối đa 15 phút; nếu nội dung, video hoặc danh tính khác bản đã duyệt, Tèo dừng và giữ Chrome để kiểm tra.

Lượt thử thật đã đăng lên Kỹ Năng Câu Cá: [video đổi cá lấy rau](https://www.facebook.com/reel/877093668730266). Lượt này được tiếp tục từ hộp bài viết đã tải xong bằng giao diện Chrome, rồi mở link kết quả để đối chiếu Page ID, caption và hashtag. Tèo đã ghi nhận kết quả thật; không tải hoặc đăng lại lượt này. Mã luồng thước phim mới được kiểm thử trên giao diện thử cục bộ; không coi lượt hoàn tất bằng điều khiển Chrome là bằng chứng worker tự động đã chạy trọn luồng thật.

Lịch Page Kỹ Năng Câu Cá từ **11–16/10/2026** đã có và đang bật: 12 video lúc **11:45, 19:30** mỗi ngày, 3 bài chữ lúc **08:30 ngày 12, 14, 16/10**, giờ Việt Nam. Giữ Tèo chạy và máy không ngủ đến giờ. Nếu Facebook nhận bài nhưng chưa trả link/kết quả đủ rõ, Tèo báo cần kiểm tra và không gửi lại bài đó; các lịch tương lai khác giữ thời gian đã lưu.

## Sửa lỗi nhận diện Page có hai vùng nội dung — 10/10/2026

Lỗi **Không xác định duy nhất vùng Page (2 mục)** xuất hiện khi Facebook có nhiều vùng nội dung nhưng Tèo yêu cầu cả trang chỉ có một vùng. Tèo đã chuyển sang tìm đúng vùng mang tên Page và nút tạo bài của Page đó. Vẫn kiểm tra danh tính Page, nội dung, tệp đính kèm và kết quả trước/sau khi đăng; nếu có nhiều đối tượng thật sự trùng khớp thì dừng và báo nguyên nhân.

Để chạy lại lịch bị lỗi này:

1. Lưu nội dung đang sửa rồi bấm **Khởi động lại, giữ công việc** trên cửa sổ chính để nạp bản mới.
2. Mở Page → **Lịch đăng**, chọn một dòng **Cần kiểm tra** có lỗi nhận diện Page.
3. Bấm **Xếp lại lịch lỗi…**, chọn giờ mới trong tương lai và **Lưu giờ mới (chưa bật)**.
4. Bấm **Bật phần đã chọn**. Giữ Tèo chạy, máy không ngủ và Facebook còn đăng nhập đến giờ đăng.

Nút xếp lại chỉ hiện khi nhật ký xác nhận lượt trước dừng ở bước nhận diện Page, trước khi nhập nội dung hoặc tải tệp, chưa có lượt chuẩn bị/đăng hoặc kết quả chưa rõ. Page, caption và video được kiểm tra lại và giữ nguyên; lỗi và giờ cũ được lưu trong **Chi tiết / Tùy chọn**. Bài đã đăng hoặc chưa rõ kết quả vẫn cần kiểm tra Facebook, không được xếp lại theo cách này. Các lịch khác giữ trạng thái và giờ đã lưu.

Đã đạt 173 kiểm thử, gồm luồng gặp lỗi → xếp giờ mới → chuẩn bị → đăng đúng một lần trên giao diện thử. Chưa đăng thử lên Page thật hoặc tự sửa/bật lại các lịch của bạn.

## Giọng Video đơn và Chiến dịch — 10/10/2026

Khi bấm **Tạo chiến dịch mới**, Tèo lấy đầy đủ cấu hình hiện tại ở tab **Video đơn**. Với chiến dịch đã lưu, chọn chiến dịch rồi bấm **Lấy cấu hình từ tab Video đơn**: Tèo lưu cấu hình mới cho các video chưa hoàn tất, áp dụng từ video chạy tiếp theo. Đổi lựa chọn ở Video đơn chưa áp dụng cho chiến dịch cho đến khi bấm nút này. Cấu hình không hợp lệ được báo lỗi, không tự dùng giọng cũ thay thế.

Dòng **Video chạy tiếp theo** hiển thị cấu hình thật sẽ truyền vào xử lý: bộ giọng, mẫu giọng, model, kiểu đọc, tốc độ giọng/hình, âm lượng, Sub, từ ngữ, xóa chữ và Drive. Khi đổi cấu hình trong lúc chạy, dòng chính hiện cấu hình mới; dòng dưới ghi cấu hình mà video đang xử lý vẫn giữ. **Cấu hình tạo mới…** mở phần riêng dành cho tạo chiến dịch mới. **Chạy / Tiếp tục** bỏ qua video lỗi; dòng riêng cho **Thử lại video lỗi** chỉ rõ video/cấu hình sẽ bắt đầu khi thử lại.

Video đang xử lý chạy hết với cấu hình cũ; thành phẩm hoàn tất không bị làm lại. Khi đổi cấu hình, video chưa xong dùng phiên giọng/xuất riêng để không lẫn cache giọng cũ. Video nguồn và lời Việt đã hoàn tất, đúng mã kiểm tra được dùng lại nếu model nhận dạng/dịch và mức suy luận không đổi. Đổi model nhận dạng/dịch sẽ thực hiện lại bước tương ứng. Tạo lịch đăng, theo dõi và tiếp tục hàng đợi đọc đúng thành phẩm của phiên mới. Lưu nội dung đang nhập rồi dùng **Khởi động lại, giữ công việc** để nạp bản mới. Worker bản cũ còn chạy sẽ báo cần dừng chiến dịch và nạp bản mới trước khi áp dụng; Tèo không tự dừng công việc.

## Màn hình Facebook bên ngoài đã thu gọn — 10/10/2026

Tab **Quản lý Page** tập trung vào danh sách tài khoản, danh sách Page, đăng nhập/lấy Page và **Quản lý Page / Bài viết**. Chọn tài khoản ở bảng bên trái. **Thêm Page bằng link…** mới mở ô nhập link; bấm lại để ẩn, nội dung đang nhập được giữ.

**Nâng cao** hiện lại bộ chọn/đổi tên tài khoản, Business Suite, Meta Developers, đóng/lưu phiên, nhận lệnh từ web, thêm/sửa/bỏ Page thủ công, Page ID/link/thông tin API và hướng dẫn đầy đủ. Việc ẩn/hiện không tắt nhận lệnh web, không đóng Chrome, không xóa Page hay đổi công việc đang chạy. Các chức năng cũ vẫn được giữ trong mã. Dùng **Khởi động lại, giữ công việc** để nạp giao diện mới sau khi lưu biểu mẫu.

## Nguồn video chỉ hiện chiến dịch đã chọn — 10/10/2026

Trong **Thiết lập → Nguồn video**, bảng chính chỉ hiện các chiến dịch đã chọn cho Page. Bấm **Thêm nguồn…** để xem tất cả chiến dịch, chọn một hoặc nhiều dòng rồi **Thêm nguồn đã chọn**. Nguồn cũ được giữ, không thêm trùng. **Đóng** bỏ lựa chọn đang xem trong cửa sổ này. Muốn tạo chiến dịch từ link mới, chọn **Tạo nguồn Douyin mới…** trong cửa sổ Thêm nguồn.

Chọn dòng trong bảng chính rồi **Bỏ nguồn** để gỡ khỏi cấu hình đang chỉnh; video và chiến dịch vẫn được giữ. Bấm **Lưu thiết lập & hẹn giờ** để lưu danh sách mới. Bấm vào một dòng hoặc làm mới bảng không làm mất các nguồn khác đã chọn. Nguồn đã lưu được khôi phục khi mở Page, còn nguồn không tìm thấy được giữ thành dòng cảnh báo để bạn chủ động xử lý.

## Giao diện quản lý Page theo luồng công việc — 10/10/2026

Trong **Quản lý Page → Quản lý Page / Bài viết**, màn hình chính còn 5 tab:

1. **Thiết lập:** lưu chủ đề/mục tiêu, chọn chiến dịch nguồn, đặt nhịp nội dung và giờ gọi AI. **Thêm nguồn… → Tạo nguồn Douyin mới…** mở tab Chiến dịch hàng loạt sẵn có; tạo video ở đó rồi quay lại Page chọn nguồn. **Chat chiến lược…** mở phần liên kết chat trong Kế hoạch. Model, mức suy luận và số ngày mỗi lượt nằm trong **Tùy chọn AI**; cấu hình đã lưu vẫn được giữ.
2. **Kế hoạch:** xem nội dung từng bài và duyệt/bật lịch. Phần tìm/liên kết chat được thu gọn dưới **Nguồn chiến lược / Liên kết chat**.
3. **Lịch đăng:** theo dõi giờ, trạng thái, kết quả/cần xử lý; chọn nhiều dòng để bật hoặc tạm dừng, hoặc **Bật tất cả**. **Chi tiết / Tùy chọn** mở cửa sổ riêng chứa đường dẫn, mã kiểm tra, đổi giờ, bỏ lịch và báo cáo bật lịch.
4. **Kết quả:** xem các lượt Tèo đã ghi nhận của đúng Page, lọc bài đã đăng/cần kiểm tra và mở bài. Thống kê chưa thu được hiển thị **—**; màn hình này không tự gọi API hoặc AI.
5. **Lịch sử AI:** xem dữ liệu, yêu cầu, kế hoạch và thao tác giữa AI với Tèo.

Trạng thái lịch Page và nút mở Facebook/đúng Page luôn ở phía trên. **Nâng cao** chứa Soạn thủ công, Thông tin & ghi chú, Số liệu Meta và Kết nối API. Ẩn/hiện các mục không xóa nội dung chưa lưu, không đổi model hay bật/tạm dừng lịch. Đây là thay đổi giao diện desktop; web và bộ xử lý công việc giữ luồng hiện có.

Để nạp giao diện mới, lưu các biểu mẫu đang sửa rồi dùng **Khởi động lại, giữ công việc** trên cửa sổ chính. Nếu bản đang mở chưa có nút đó, đợi công việc nghỉ rồi mở lại Tèo. Lượt cập nhật giao diện này chưa khởi động lại ứng dụng thật hoặc thay đổi lịch Page của bạn.

Mỗi lượt AI hiện lập kế hoạch **1–7 ngày**, có thể lặp theo cấu hình; bản này chưa thêm chương trình tự vận hành 30 ngày. Khả năng đăng video/Reels nhiều màn hình và thu đủ thống kê Facebook vẫn có giới hạn như các mục dưới đây.

## Khởi động lại Tèo để nhận cập nhật, giữ công việc — 10/10/2026

Nút **Khởi động lại, giữ công việc** nằm trên đầu cửa sổ chính. Video đơn, chiến dịch và hàng đợi chiến dịch tiếp tục chạy trong tiến trình cũ; Tèo mới nối lại tiến độ, giữ cấu hình từng phiên và lịch Page đã bật. Không tải/dịch/tạo giọng lại chỉ vì khởi động lại. Phiên đăng nhập Facebook được lưu rồi mở lại bằng đúng hồ sơ của từng tài khoản. Chờ khởi động lại không gọi thêm AI.

Nếu đang có lượt AI phân tích, đăng bài Facebook, đăng nhập hoặc quét kênh, Tèo chờ lượt đó hoàn tất. Bài sắp đến giờ được xử lý trước khi khởi động lại. Lưu bản nháp/ghi chú Page và đóng cửa sổ biên tập/thiết lập còn mở; hộp soạn bài trong Chrome còn nội dung sẽ chặn đóng trình duyệt để giữ lời đang soạn. **Hủy khởi động lại** hủy yêu cầu, công việc đang chạy tiếp tục.

Bản mới dựng xong giao diện và phản hồi sẵn sàng trước khi bản cũ đóng. Nếu bước này lỗi, bản cũ vẫn hoạt động. Khi bàn giao, Tèo giữ đúng lịch/nội dung/Page đã duyệt. Lịch đã có thao tác gửi hoặc chưa rõ kết quả không được tự gửi lại; lịch bị đổi hoặc quá giờ cần kiểm tra. **Chi tiết** cho biết từng công việc đã nối lại hoặc lý do cần xử lý.

**Lần đầu:** ứng dụng cũ đang mở chưa có nút này; đợi công việc nghỉ rồi đóng/mở Tèo một lần để nạp bản cập nhật. Các lần cập nhật sau dùng nút **Khởi động lại, giữ công việc**. Đóng bằng dấu X vẫn là cách đóng thông thường, có thể dừng video đơn/tạm dừng lịch; không có bàn giao tự động như nút mới. Giữ máy có mạng và không ngủ; tính năng này không bảo đảm công việc đang gửi dở phục hồi sau mất điện.

## Bật lịch hàng loạt và tiếp tục sau đợt thử — 10/10/2026

Mở lại Tèo khi công việc đang nghỉ. Trong **Quản lý Page / Bài viết → Lịch đăng bài / video**, chọn nhiều dòng bằng **Ctrl / Shift** hoặc **Chọn tất cả**, rồi bấm **Bật phần đã chọn**. **Bật tất cả** bật các lịch đủ điều kiện của đúng Page đang mở, theo giờ đã lưu. Không cần chờ bài trước đăng xong để bật các bài sau. **Tạm dừng phần đã chọn** dừng nhiều lịch. Đổi giờ, mở bài và bỏ lịch vẫn yêu cầu chọn đúng một dòng.

Lịch quá giờ bị bỏ qua: chọn từng lịch rồi **Đổi giờ** sang tương lai trước khi bật. Lịch đã đăng/đã bỏ/đã có thao tác gửi không được gửi lại bằng nút bật lịch. Với video dừng ở bước chuẩn bị, dùng quy trình **Khôi phục lịch đã chọn…** ở đầu tài liệu nếu đủ điều kiện và đã kiểm tra Facebook. Lịch đang chạy được giữ nguyên khi bấm lại. Xem **Kết quả bật lịch** để đọc kết quả và nguyên nhân của từng dòng; giờ, nội dung, video và Page được giữ nguyên. Bật lịch không gọi AI; đến giờ bộ xử lý desktop mới thao tác đăng.

Để tiếp tục kế hoạch sau 6 ngày thử, vào **Đăng theo kế hoạch AI → Nguồn & lịch AI**. Chọn chiến dịch còn thành phẩm chưa dùng, đặt **Ngày kế hoạch = 6**, **Lặp sau = 6**, và **Gọi AI lần đầu** vào một giờ tương lai sau đợt hiện tại. Tick **Bật lịch gọi AI đã cấu hình**, rồi **Lưu cấu hình / hẹn giờ**. Tick thêm **Tự bật lịch đăng kế hoạch AI...** nếu muốn đợt kế hoạch mới tự chạy; không tick thì xem và duyệt ở **Kế hoạch từ Codex**. Chu kỳ tiếp theo lấy dữ liệu mới, loại video đã có trong lịch/kế hoạch trước và dùng model đã lưu. Chờ giờ không dùng AI; mỗi lượt phân tích có dùng hạn mức Codex.

Giữ Tèo mở, máy không ngủ, có mạng và Facebook đăng nhập đúng tài khoản/Page. Lỡ giờ gọi AI hơn 10 phút hoặc gặp lỗi thì cần kiểm tra và đặt giờ mới; không tự chạy dồn. Khi chưa nối Meta, AI dùng dữ liệu/thống kê đã lưu, chưa tự thu đủ view/reach. Trình đăng video/Reels nhiều màn hình vẫn có thể dừng để kiểm tra.

## Trạng thái lịch Page — 10/10/2026

Trong cửa sổ **Quản lý Page / Bài viết**, mục **Trạng thái lịch Page** nằm ở đầu cửa sổ, nhìn được khi chuyển giữa các tab. Màu xanh **Đang chạy** ghi rõ chờ giờ xử lý, AI đang phân tích, chuẩn bị bài hoặc đang đăng; kèm giờ AI/bài tiếp theo, số bài chờ/đã đăng/tạm dừng/cần kiểm tra. **Đã lưu lịch — chưa bật**, **Chờ duyệt kế hoạch AI**, **Đã tạm dừng**, **Dừng — cần kiểm tra lỗi**, **Đã hoàn tất** là các trạng thái riêng. Mở Chrome hoặc chỉ lưu cấu hình không được coi là đang chạy. Tạm dừng AI nhưng lịch bài còn bật thì Page vẫn báo đang chờ đăng. Lịch cũ không còn bộ xử lý sở hữu cần tiếp tục, không tự chạy lại để hiển thị xanh.

Desktop cập nhật trạng thái mỗi 2 giây, hoàn toàn đọc dữ liệu, không gọi AI/khởi chạy lịch/trình duyệt. Trên web Quản lý Page có cùng mục và giờ kế tiếp; mất kết nối máy Tèo thì hiển thị **Mất kết nối** cùng trạng thái gần nhất, không báo nhầm đang chạy. Mở lại desktop khi công việc nghỉ để nạp giao diện mới; web đã cập nhật.

## Đăng theo kế hoạch AI, chọn chiến dịch và xem lịch sử — 09/10/2026

Mở lại Tèo khi đang nghỉ, vào **Quản lý Page → Quản lý Page / Bài viết → Đăng theo kế hoạch AI**. Mỗi Page có cấu hình riêng. Liên kết chat chiến lược ở tab **Kế hoạch từ Codex** trước; chọn nhiều chiến dịch nguồn bằng Ctrl/Shift, đặt số video/ngày và bài chữ/tuần, model, giờ gọi AI đầu tiên, số ngày kế hoạch và khoảng lặp. Mặc định gợi ý 2 video/ngày, 3 bài chữ/tuần, GPT-5.6 Terra/thấp, kế hoạch 7 ngày; chưa bật cho bất kỳ Page thật nào. Khoảng lặp bằng 0 là một lần, khoảng lặp khác phải ít nhất số ngày kế hoạch.

Tick **Bật lịch gọi AI đã cấu hình** rồi **Lưu cấu hình / hẹn giờ**. Đến giờ, desktop đọc câu trả lời cuối đã hoàn tất trong chat chiến lược, lấy video thành phẩm của đúng chiến dịch, lời thoại SRT có hash đã kiểm chứng, thống kê bài/lịch và thông tin Page đã lưu. Tèo cập nhật số liệu qua Meta nếu đã có kết nối API; không yêu cầu mua API OpenAI. Thiếu view/reach/followers được ghi chưa biết, không giả thành 0. Giờ đăng do AI đề xuất trong khoảng kế hoạch, tối đa nhịp đã đặt, cách nhau và lịch có sẵn ít nhất 30 phút. Không tự dùng video chưa có lời thoại kiểm chứng, ngoài nguồn hoặc đã có lịch/kế hoạch.

Mỗi lượt AI chạy độc lập bằng Codex CLI đã đăng nhập ChatGPT, model thực được truyền xuống engine; chat đã liên kết là tài liệu chiến lược, không bị tự resume/ghi thêm. Cách này giới hạn dữ liệu và tránh gửi lại toàn bộ lịch sử chat dài. Câu trả lời, model yêu cầu/model quan sát được (nếu CLI cung cấp), token sử dụng thực nếu có đều được lưu. Chờ giờ và đọc dữ liệu không gọi model; tối đa một lượt AI/Page/ngày, không tự đổi model, fallback, gọi lại khi lỗi hoặc hết hạn mức. CLI không báo model/usage thì ghi chưa biết, không suy ra phần trăm hạn mức tuần. Tham khảo [Codex chạy theo lịch và trả JSON](https://learn.chatgpt.com/docs/non-interactive-mode).

Không tick **Tự bật lịch đăng**: nhận đề xuất ở **Kế hoạch từ Codex** để xem/duyệt từng bài. Tick **Tự bật lịch đăng kế hoạch AI theo đúng Page, nguồn và số bài ở trên** là cho phép những lượt tương lai tự viết nội dung và bật lịch sau khi kiểm tra chính sách đã lưu. Mẫu caption/hashtag lấy từ tab **Lịch đăng bài / video**. Đây là nội dung mới do AI viết; có thể chọn chế độ duyệt để kiểm tra trước. Trên web [Quản lý Page](https://teo.pipideal.com/facebook), phần **Đăng theo kế hoạch AI** gửi cùng cấu hình xuống desktop, không tự xử lý trình duyệt trên máy chủ web.

Tab **Lịch sử AI ↔ Tèo** ghi cấu hình bạn lưu, dữ liệu/yêu cầu gửi AI, phân tích/kế hoạch trả về, sử dụng token nếu có, lưu/bật lịch và trạng thái thực của từng bài. Chọn một dòng để đọc đầy đủ. Web xem các sự kiện gần nhất; bản đầy đủ nằm ở desktop. Tạm dừng AI chặn lượt mới/phản hồi cũ, **không hủy lịch bài đã bật**; dừng lịch bài ở tab Lịch đăng. Giữ máy/Tèo mở, có mạng và không ngủ, Chrome đúng tài khoản Facebook. Lỡ giờ gọi AI hơn 10 phút hoặc AI bị ngắt thì dừng để kiểm tra, không chạy dồn hay tự gửi lại. Sau khi mở lại ứng dụng, lịch Facebook cũ cần tiếp tục bằng nút có sẵn; không tự gửi lại lượt đăng bị mất kết quả.

Giới hạn: browser thống kê view/reach riêng tư chưa có đầy đủ; khi chưa nối Meta chỉ phân tích kết quả/thông tin đã lưu. Trình đăng video/Reels nhiều màn hình vẫn chưa hỗ trợ đầy đủ và chưa xác minh đăng thật trên Page; trường hợp đó báo cần kiểm tra. Không bảo đảm tăng view/follow. Đã kiểm thử lưu/tạm dừng, đến giờ mới gọi AI, một lượt duy nhất, model truyền đúng, thay đổi nguồn/chính sách, ghi lịch sử, GUI và lệnh web bằng dữ liệu thử; chưa gọi AI hoặc đăng nội dung thật trong lần cập nhật này.

## Vào Page bằng đúng danh tính để đăng bài — 09/10/2026

Trong **Quản lý Page**, chọn tài khoản và Page rồi **Mở Page đã chọn** hoặc **Quản lý Page / Bài viết → Mở đúng Page**. Tèo tự mở menu Facebook, vào **Xem tất cả trang cá nhân**, chọn đúng Page và kiểm dấu **đang chọn**; không chỉ mở đường dẫn fanpage bằng danh tính cá nhân. Lệnh **Mở Page** trên web cũng dùng luồng này ở desktop. Cần lưu đúng tên và Page ID; Chrome của tài khoản cần đăng nhập và có quyền quản lý Page.

Trong **Soạn & đăng bài**, lưu nội dung rồi chuẩn bị/xem trước và đăng bản đã duyệt. Bước chuẩn bị cũng tự chọn Page; trước khi Đăng, Tèo đọc lại danh tính hiện tại. Nếu bạn đã chuyển sang cá nhân/Page khác hoặc còn hộp soạn bài cần giữ, Tèo báo nguyên nhân và dừng. Hoàn tất/đóng hộp đang soạn trước khi đổi Page. Đăng thành công chỉ được ghi khi thấy bằng chứng/link bài thực; Tèo không tự bấm lại nếu kết quả chưa rõ.

Mở lại Tèo khi công việc đang nghỉ để nạp bản sửa. Kiểm thử chuyển/soạn/đăng đã đạt trong môi trường thử; chưa đăng bài thử lên Page thật của bạn. Trình đăng video/Reels nhiều bước vẫn chưa được hỗ trợ đầy đủ.

## Kế hoạch Page từ cuộc trò chuyện Codex — 09/10/2026

Tèo có thể liên kết riêng mỗi Page với một cuộc trò chuyện Codex trên máy xử lý. Đã lưu liên kết **Kỹ Năng Câu Cá** với **Phát triển page câu cá**, bật tự nhận kế hoạch; nhịp người dùng đã xác nhận là **2 video mỗi ngày và 3 bài chữ mỗi tuần**. Kế hoạch thử đầu tiên cho **10–16/10/2026** đã được nhập sẵn gồm **14 video và 3 bài chữ**. Sau khi mở lại desktop, chọn kế hoạch để xem và duyệt; chưa có bài đăng hoặc lịch được bật từ kế hoạch này.

Mở lại Tèo khi phiên đang nghỉ, vào **Quản lý Page → chọn Page → Quản lý Page / Bài viết → Kế hoạch từ Codex**:

1. Bấm **Tìm trong Codex**, chọn đúng cuộc trò chuyện rồi **Liên kết cuộc trò chuyện**. Liên kết được lưu riêng cho Page; các tài khoản Facebook vẫn dùng hồ sơ Chrome riêng như trước.
2. Trong cuộc trò chuyện đó, yêu cầu lên kế hoạch hoặc dùng **Sao chép yêu cầu lập kế hoạch** để gửi thông tin Page và một danh sách mẫu video thành phẩm. Caption, hashtag, video và thời gian được ghi trong khối `teo-page-plan` của câu trả lời cuối. Danh sách mẫu có tên chiến dịch, tối đa 30 video và tối đa 3 mỗi chiến dịch; không phải toàn bộ kho. Chọn đúng chủ đề Page và kiểm tra lời thoại/video trước khi chốt caption.
3. Bấm **Nhận kế hoạch mới**, hoặc bật lựa chọn tự nhận. Tèo chỉ đọc câu trả lời đã hoàn tất; nhận lại cùng kế hoạch giữ trạng thái cũ, không gọi AI, không tự tạo kế hoạch mới và không bật đăng. Việc yêu cầu Codex viết/sửa kế hoạch trong cuộc trò chuyện vẫn sử dụng hạn mức tài khoản.
4. Bấm **Duyệt & bật lịch đã chọn**, đọc toàn bộ Page, lời đăng, tệp video và giờ UTC+7 rồi xác nhận đúng kế hoạch. Bước kiểm tra có thể lưu lịch tạm nhưng chưa bật đăng; chỉ sau xác nhận mới bật các lịch đã duyệt. Không cần duyệt lại từng bài của cùng kế hoạch đã chốt. Muốn sửa nội dung/video/giờ trong kế hoạch, tạo bản kế hoạch có `plan_id` mới; không ghi đè bản cũ.

Quản trị viên cũng xem, nhận và duyệt kế hoạch tại [Quản lý Page trên web](https://teo.pipideal.com/facebook). Liên kết cuộc trò chuyện được thiết lập trên desktop; web gửi lệnh về máy Tèo. Giữ desktop mở, bật **Nhận lệnh quản lý Page từ web**, giữ máy không ngủ và đăng nhập Facebook đúng tài khoản. Tèo theo dõi từng bài/video cùng lịch hiện có; lỗi hoặc kết quả chưa rõ sẽ dừng để kiểm tra, không tự gửi lại. Tạm dừng trong **Lịch đăng bài / video**; bỏ liên kết chat hoặc tắt nhận lệnh web không hủy những lịch đã duyệt. Mở lại ứng dụng không tự chạy tiếp lịch cũ.

Chỉ video chiến dịch đã hoàn tất, còn đúng tệp/nội dung mới được dùng cho kế hoạch. Kế hoạch không thay mẫu caption/hashtag cố định đã lưu của Page. Caption tự nhiên và lịch đều đặn là phương án thử nghiệm; lượt xem và theo dõi không được bảo đảm, và bản này chưa tự đọc đầy đủ thống kê Facebook để tự điều chỉnh chiến lược.

**Giới hạn hiện tại:** luồng đăng video/Reels nhiều màn hình của Facebook chưa được hỗ trợ đầy đủ và chưa được kiểm chứng đăng video thành công trên Page thật. Liên kết chat, nhận đề xuất và lưu lịch không chứng minh tự đăng video đã ổn định. Cần thử đúng giao diện Facebook của tài khoản với nội dung đã duyệt; khi gặp luồng chưa hỗ trợ, kiểm tra Chrome và trạng thái lỗi trước khi tiếp tục.

## Quản lý Page bằng Chrome — desktop xử lý, web gửi lệnh

Mở lại Tèo desktop khi phiên đang nghỉ để nạp bản mới. Trong **Quản lý Page**, thêm/chọn tài khoản → **Đăng nhập / Mở Facebook** → đăng nhập trực tiếp trong Chrome → **Load Page của tài khoản**. Mỗi tài khoản có một hồ sơ Chrome riêng, lần mở sau dùng lại phiên; Facebook vẫn có thể yêu cầu đăng nhập/xác minh lại. Nếu chưa lấy được danh sách, **Lưu Page bằng link** hoặc **Thêm Page…**, lưu đúng tên và Page ID bằng số.

Chọn Page → **Quản lý Page / Bài viết**. Bài mới mặc định **Trình duyệt web / Tèo bấm Đăng sau khi duyệt**. Nhập lời/hashtag, chọn ảnh hoặc video → **Lưu bản nháp** → **Xem trước / Chuẩn bị**. Tèo mở đúng Page, kiểm danh tính Page, điền nội dung và tải tệp vào Chrome. Đọc bản thực tế rồi **Đăng bài đã duyệt**. Bài API cũ giữ cách đăng đã lưu; kết nối API là lựa chọn riêng, không cần cho cách đăng bằng Chrome.

Quản trị viên mở **Quản lý Page** trên [web Tèo](https://teo.pipideal.com/facebook). Giữ desktop mở và bật **Nhận lệnh quản lý Page từ web**. Web có danh sách tài khoản/Page, lưu link Page, soạn bài chữ hoặc chọn video thành phẩm đã hoàn tất của tài khoản web, gửi lệnh chuẩn bị và duyệt đăng. Chrome mở trên máy Tèo; mật khẩu và phiên Facebook không đi qua form web. Nhân viên không có quyền vào mục này. Lệnh có trạng thái đang chờ, đang xử lý, hoàn tất, lỗi hoặc cần kiểm tra; mất kết nối hiển thị dữ liệu đã nhận trước đó và chặn lệnh mới.

**Hẹn giờ bài chữ** có trong cửa sổ Page desktop và web: lưu bản nháp chữ chưa gửi, xem đúng Page/lời/giờ Việt Nam (UTC+7), xác nhận cho phép tự đăng rồi lưu lịch. Desktop phải mở, máy có mạng và Facebook còn đăng nhập lúc đến giờ. Video chiến dịch tiếp tục dùng chuột phải **Đăng video** để chọn Page, caption/hashtag và giờ đăng như trước. Lịch và kết quả được giữ khi dừng; mở lại không tự chạy lịch cũ. Không tự đăng bù nếu lỡ giờ quá 2 phút. Đóng/tắt nhận lệnh web không đồng nghĩa hủy các lịch đã được duyệt; tạm dừng lịch trong **Lịch đăng bài / video** trước nếu cần.

Tèo chỉ xác nhận đăng thành công khi đọc được bằng chứng/link bài trên Facebook. Kết quả chưa rõ thì giữ trạng thái cần kiểm tra, không tự gửi lại. Bản này xử lý form **Tạo bài viết** có một ô nội dung và ảnh/video; nếu Facebook chuyển sang trình đăng Reels/video nhiều bước chưa được hỗ trợ, Tèo báo rõ để bạn kiểm tra trực tiếp trong Chrome. Giao diện Facebook thật có thể đổi, nên cần thử trên Page của bạn với nội dung bạn duyệt; kiểm thử phát triển dùng dữ liệu tạm, chưa đăng bài thật. Hồ sơ Chrome được lưu qua [persistent context của Playwright](https://playwright.dev/docs/api/class-browsertype#browser-type-launch-persistent-context).

## Xóa Sub Trung ngay trên web

1. Dán link video, bấm **Tải video để khoanh vùng**. Bước này chỉ tải/kiểm tra video gốc, chưa gọi nhận dạng, dịch hay tạo giọng.
2. Trong hàng đợi, chờ tải xong rồi bấm **Khoanh vùng chữ gốc**. Tua video tới đoạn có chữ, dừng rồi bấm **Lấy khung hình**. Kéo trên ảnh để khoanh kín dòng chữ; kiểm tra nhiều đoạn của video vì cùng vùng được xóa trên mọi khung hình. Vùng tối đa 25% diện tích hình.
3. Bấm **Lưu vùng và bật xóa**. Form tự chọn đúng video và bật **Xóa chữ trong vùng đã lưu bằng GPU trước khi chèn Sub Việt**. Chọn giọng/âm lượng/Sub Việt rồi **Gửi lồng tiếng vào hàng đợi**. Tèo dùng lại video đã tải, dịch/tạo giọng, xóa vùng chữ bằng GPU rồi mới chèn Sub Việt.

Vùng được lưu riêng cùng hồ sơ video trên máy chủ và gắn với nội dung video. Khi mở web lại, bấm **Khoanh vùng chữ gốc** của video đó để nạp vùng đã lưu. Đổi link sẽ bỏ chọn vùng trên form; cần chọn lại đúng video. Tắt checkbox giữ vùng đã lưu nhưng phiên gửi mới không xóa chữ. Cấu hình công việc đã gửi được chốt; sửa vùng về sau không thay đổi phiên đang chạy hoặc tiếp tục. Tệp gốc/thành phẩm cũ được giữ nguyên; có thể chọn video gốc của công việc đã hoàn tất để tạo thành phẩm mới.

STTN dùng GPU NVIDIA của máy xử lý, không tiêu tốn token Codex ở bước xóa chữ. Nhận dạng/dịch/tạo giọng vẫn theo các model quản trị đã chọn. Xử lý chậm hơn; nền có thể nhòe hoặc có vệt, nhất là cảnh chuyển động. Tèo chưa tự phát hiện tất cả chữ Trung: chỉ xóa hình trong vùng bạn khoanh; tránh khoanh logo/mặt người/vùng muốn giữ. Quyền xem nguồn vẫn theo tài khoản; mỗi người lưu và dùng vùng của video thuộc tài khoản mình. Video chuẩn bị chỉ tải xong chưa được tính là đã lồng tiếng; thông báo trong thẻ nói rõ bước này.

## Model AI chung và cấu hình riêng từng nhân viên

Quản trị viên mở **Model AI** trên web để lưu chung nhận dạng, model dịch, mức suy luận và bộ giọng. Cấu hình hiện tại dùng **GPT‑6 Luna / Thấp**, nhận dạng tự động và VieNeu Turbo. Nhân viên xem model áp dụng; máy chủ kiểm tra cấu hình khi nhận công việc để chặn thay model bằng yêu cầu tự sửa. Các công việc mới lấy cấu hình quản trị; công việc đã gửi và tiếp tục phiên giữ snapshot ban đầu.

Giọng đọc, kiểu đọc, tốc độ giọng/hình, âm lượng, bật phụ đề, màu/đậm/cỡ chữ và chép Drive tự lưu theo từng tài khoản khi thay đổi. Đợi dòng **Đã nhớ lựa chọn riêng cho tài khoản này** để xác nhận đã lưu. Đăng nhập trên máy hoặc trình duyệt khác sẽ lấy lại lựa chọn đó. Tài khoản cũ bắt đầu từ công việc gửi gần nhất; link video và tên công việc được nhập mới mỗi lần. Khi đổi bộ giọng, lựa chọn giọng không có trong bộ mới chuyển sang Adam. Nhân viên đang mở web từ bản trước tải lại trang để nhận giao diện mới.

## Tèo Web trên pipideal.com — đã hoạt động 06/10/2026

Địa chỉ cố định: **https://teo.pipideal.com**. Đã kiểm tra đăng nhập quản trị, lịch sử công việc và tải MP4/SRT có sẵn qua tên miền. Máy này tiếp tục xử lý video; Google Drive cho máy tính tiếp tục đồng bộ thư mục thành phẩm. Vào **Nhân viên** để cấp tài khoản riêng rồi gửi link cho từng người. Giữ máy bật, có mạng và Tèo Web chạy; sau khi khởi động lại máy, mở **Mo Teo Web.cmd** để dùng lại cùng địa chỉ. Hướng dẫn sử dụng và cấu hình đã triển khai nằm trong **TEO_WEB_PIPIDEAL.txt**.

## Cấu hình địa chỉ web cố định

Mở **Cai dia chi Teo Web.cmd** trên Desktop hoặc nút **Địa chỉ cố định…** ở tab theo dõi. Có hai cách: tên miền riêng qua Cloudflare Tunnel cho dùng thường xuyên, hoặc địa chỉ ngrok được cấp cho tài khoản để thử nhỏ. Nhập địa chỉ HTTPS thật và mã kết nối ngay trong cửa sổ trên máy; mã được mã hóa Windows, không gửi qua chat hoặc ghi vào cấu hình công khai. Bấm **Lưu cấu hình**, rồi **Áp dụng khi web nghỉ**. Tèo kiểm tra hàng đợi/khóa xử lý trước khi mở lại riêng máy chủ web, giữ nguyên tài khoản và thành phẩm.

Những lần mở sau tự dùng địa chỉ đã lưu. Kết nối lỗi thì báo trạng thái và thử lại, không thay bằng link ngẫu nhiên. Trang quản trị phân biệt địa chỉ cố định với link thử và tự cập nhật trạng thái kết nối. Cần tên miền hoặc tài khoản dịch vụ thật để kích hoạt; phần mềm chưa tự mua/đăng ký tên miền. Hướng dẫn tại **TEO_WEB_DIA_CHI_CO_DINH.txt**. [Cloudflare cần domain trong tài khoản](https://developers.cloudflare.com/tunnel/get-started/); [ngrok miễn phí có dev domain cố định, hạn mức1GB/20.000requests mỗi tháng](https://ngrok.com/docs/pricing-limits/free-plan-limits). Máy xử lý vẫn cần bật và có mạng.

## Theo dõi công việc web ngay trên máy

Mở tab **Công việc web & thống kê** trong Tèo: xem hàng đợi, video đang chạy, tiến độ, lỗi, thành phẩm và trạng thái chép Drive. Tab tự cập nhật mỗi 3 giây, ưu tiên phiên đang chạy rồi các công việc chờ theo lượt gửi. Chọn một dòng để xem thông báo/cấu hình, **Mở thư mục thành phẩm** hoặc **Mở hồ sơ công việc** để xem nhật ký trên máy.

Lọc theo nhân viên và ngày gửi: tất cả, hôm nay, 7 hoặc 30 ngày gần đây. Sáu ô tổng và bảng nhân viên thống kê toàn bộ công việc trong phạm vi đó; bộ lọc trạng thái chỉ lọc danh sách. Có số đã dừng riêng. Danh sách hiển thị tối đa200 dòng, thống kê vẫn tính hết. **Mở web trên máy** vào portal hiện tại; **Sao chép link web** lấy link online đã lưu để gửi nhân viên.

Đây là thống kê công việc gửi qua web. Video đơn/chiến dịch trên máy vẫn theo dõi ở các tab hiện có. Khi web chưa phản hồi, tab báo rõ đang xem hồ sơ đã lưu; lỗi đọc giữ số liệu kèm thời điểm cập nhật trước. “Đã chép vào thư mục đồng bộ” chưa xác nhận upload lên Drive online. Mở lại Tèo khi công việc desktop đang nghỉ để nạp tab mới; web tiếp tục chạy độc lập.

## Tèo Web — bản thử cho nhân viên (06/10/2026)

Mở **Mo Teo Web.cmd** để chạy web và hàng đợi trên máy này. Mở **Xem tai khoan Teo Web.cmd** để xem tài khoản quản trị ban đầu; đổi mật khẩu trên web rồi vào **Nhân viên** để tạo tài khoản cho từng người. Link online hiện tại hiển thị trên trang quản trị và trong tệp `LINK_WEB.txt` cùng dữ liệu web. Không gửi tài khoản quản trị cho nhân viên. Windows trên máy này ánh xạ dữ liệu vào `AppData/Local/Packages/OpenAI.Codex_2p2nqsd0c76g0/LocalCache/Local/TeoDubWeb`; đường dẫn đã được ghim để giữ nguyên tài khoản/hàng đợi khi mở lại ngoài Codex.

Nhân viên dán **một link Douyin/TikTok**, chọn giọng preset, tốc độ, kiểu đọc, âm lượng và phụ đề; có thể tải trước để khoanh vùng xóa chữ Trung, rồi gửi lồng tiếng vào hàng đợi. Model AI do quản trị viên thiết lập. Xem tiến độ, dừng/tiếp tục và tải MP4/SRT khi hoàn tất. Mỗi người chỉ xem công việc của mình, quản trị viên xem toàn bộ. Một video chạy mỗi lần, dùng chung khóa xử lý với desktop để chờ khi máy đang chạy phiên khác. Cấu hình đã gửi được chốt; tiếp tục không đổi theo form hoặc cập nhật mới. Phiên lỗi/gián đoạn giữ các bước đã lưu.

Web dùng worker Tèo hiện có trong `.venv`, ASR/VieNeu/FFmpeg trên máy và tài khoản Codex hiện tại để dịch, nên hạn mức dịch vẫn áp dụng. Môi trường web riêng `.venv-web` không đổi môi trường mô hình của desktop. Nếu bật Drive, thành phẩm được chép vào `C:/Users/chungpc/Videos/Teo Drive`; Drive cho máy tính tiếp tục đồng bộ. Web không xác nhận tải lên cloud, tự chia sẻ Drive hay tạo link Drive. Nhân viên tải trực tiếp qua web có kiểm quyền sở hữu và SHA thành phẩm.

Máy cần bật, có mạng và máy chủ web chạy; không cần mở GUI desktop. Khi chưa cấu hình địa chỉ cố định, bản thử dùng Cloudflare Quick Tunnel, không cần mở cổng router hay đăng ký thẻ. Link thử có thể đổi mỗi lần mở lại và không có cam kết uptime ([Cloudflare](https://developers.cloudflare.com/tunnel/get-started/quick-tunnels/)). Dùng cửa sổ Địa chỉ web cố định để chuyển sang tunnel/địa chỉ đã được cấp. Google Drive vẫn cần chạy để tải lên.

Khoanh vùng xóa chữ gốc và quản lý Page nay có trên web. Sửa từng câu, giọng theo mẫu và chiến dịch vẫn thực hiện trên desktop. Web chưa có upload video/Drive input và chưa triển khai cloud queue chạy khi máy offline. Dữ liệu tài khoản/hàng đợi/thành phẩm riêng trong `AppData/Local/TeoDubWeb`, không đổi công việc/cấu hình desktop đã lưu. Hướng dẫn ngắn tại **TEO_WEB_HUONG_DAN.txt**. Cập nhật được thực hiện theo yêu cầu trong cuộc trò chuyện Codex này; không có tự động nâng cấp. Giữ thư mục dữ liệu khi cập nhật, đợi hàng đợi nghỉ trước khi khởi động lại web.

Phần web dùng Flask/Waitress, mật khẩu scrypt, phiên 8 giờ có thể thu hồi, CSRF/Origin check, cookie HttpOnly/SameSite/Secure trên HTTPS, giới hạn đăng nhập/gửi việc và Host allowlist chính xác. Máy chủ chỉ nghe loopback; tunnel công khai trang đăng nhập, mọi video đều yêu cầu tài khoản. Giới hạn thử: tối đa100 việc đang chờ/chạy,10 mỗi người, mỗi phiên tối đa2 giờ trước khi yêu cầu dừng. Xem chứng cứ kiểm thử và kiến trúc trong HANDOFF.

## Chỉnh cỡ chữ Sub Việt

Trong **Video đơn**, bật **Ghi Sub Việt trực tiếp vào hình**, rồi chỉnh ô **Cỡ chữ** cạnh màu chữ/độ đậm. Nhập hoặc bấm tăng/giảm từ **8 đến 36**, mặc định **13** như trước. Muốn chữ nhỏ hơn có thể thử9–11; số lớn hơn làm chữ lớn hơn. Đây là cỡ tương đối của bộ dựng phụ đề, không phải số pixel cố định trên mọi độ phân giải.

Cỡ chữ được áp dụng lúc **Lồng tiếng và xuất video**, sau bước xóa Sub gốc nếu đã bật. Tèo lưu lựa chọn cho phiên mới và nhớ khi mở lại. Muốn áp dụng cho chiến dịch mới, bấm **Lấy cấu hình từ tab Video đơn**. Phiên/chiến dịch đã lưu và **Tiếp tục phiên trước** giữ cấu hình cũ; thiếu trường cỡ chữ thì dùng13. Cỡ này không đổi nội dung/timing SRT, không cần dịch lại. Với Sub bật/tắt hoặc tệp SRT ngoài, cỡ hiển thị do trình phát quyết định.

Mở lại Tèo khi đang nghỉ để thấy ô mới.

## Xóa phụ đề dính trong hình bằng GPU (bản thử nghiệm)

Mở lại Tèo khi ứng dụng đang nghỉ. Trong **Video đơn**, làm theo luồng tách bước:

1. Dán link → **2. Tải video và dịch Sub**. Tèo chọn video gốc và SRT Việt vừa tạo; có thể xem/sửa lời trước.
2. Bấm **Lưu / sửa vùng xóa Sub gốc…**. Cửa sổ dùng đúng video đã tải, không có lựa chọn nguồn độc lập gây nhầm video.
3. Chọn số giây có chữ → **Xem hình gốc**, khoanh sát chữ gồm viền → **Thử GPU 3 giây** và mở clip để duyệt → **Lưu và bật xóa Sub**. Nút **Lưu vùng cho video này** vẫn có nếu chỉ muốn lưu mà chưa bật.
4. Trên màn hình chính, tick **Xóa Sub gốc trước khi chèn Sub Việt (GPU)**. Dòng trạng thái ghi rõ đã lưu vùng hay đã bật GPU và tên video. Tick khi chưa lưu sẽ báo lý do, không tự đoán vùng.
5. Bật **Ghi Sub Việt trực tiếp vào hình**, chọn màu/độ đậm, rồi **4. Lồng tiếng và xuất video**. Bản SRT Việt đã có được dùng lại, không gửi dịch lại. Hình được xóa chữ gốc bằng GPU trước khi chèn Sub Việt.

Lưu vùng và bật xử lý là hai bước riêng. Lưu vùng luôn giữ tọa độ dù chưa tick; bỏ tick không làm mất vùng. Tèo lưu riêng theo đường dẫn và SHA256 của video, nhớ trạng thái của video đó khi mở lại. Đổi video không dùng vùng cũ; chọn lại video có hồ sơ sẽ nạp vùng và trạng thái đã lưu. Video bị thay nội dung hoặc cấu hình bị đổi ở cửa sổ khác sẽ báo lỗi trước khi chạy. Chỉnh vùng và lưu lại cần tick áp dụng lại. Phiên đã bắt đầu vẫn dùng cấu hình bất biến; để thay vùng cho bản xuất mới hãy bấm Lồng tiếng và xuất video, không dùng Tiếp tục phiên cũ.

Nếu đã lưu vùng nhưng bước GPU đang tắt, khi bấm xuất Tèo hỏi rõ: **Có** để bật xóa rồi xuất, **Không** để giữ chữ gốc trong lần này, hoặc **Hủy** để kiểm tra. Không còn âm thầm bỏ qua vùng đã lưu. Nút **Lưu và bật xóa Sub** thực hiện cả hai bước; xuất sau đó không hỏi lại. Thông báo hoàn tất ghi rõ “đã chạy GPU xóa vùng Sub gốc” hoặc “KHÔNG chạy xóa Sub gốc”.

STTN dùng GPU NVIDIA để tái tạo vùng đã khoanh; không dùng API hay token Codex cho bước xóa chữ. Đây là xóa theo vùng ở mọi khung hình, **chưa tự nhận diện ngôn ngữ hoặc tự dò chữ**. Không bảo đảm xóa hoàn hảo: nền chuyển động, chân/tay hoặc vật thể bị chữ che có thể nhòe/méo, kể cả ở thời điểm không có chữ trong vùng. Tránh khoanh cả dải hình quá rộng; vùng tối đa25% diện tích. Chữ ngoài vùng khoanh vẫn giữ nguyên. Muốn giữ nguyên video thì tắt tùy chọn.

Mặc định tắt cho phiên cũ. Video gốc được giữ nguyên; xử lý hình trước rồi mới ghi Sub Việt, âm thanh gốc vẫn lấy từ tệp nguồn. Hai mức âm lượng và xếp lời không đổi. Mỗi phiên lưu vùng/mô hình riêng, tiếp tục không đổi cấu hình ngầm. Bản xóa hoàn tất có kiểm tra SHA được dùng lại; nếu dừng giữa bước AI, lần tiếp tục xử lý lại bước xóa chữ, không phải dịch/tạo giọng lại. Bản hình trung gian FFV1 giữ khung hình nhưng có thể chiếm nhiều GB trong thư mục phiên.

Vùng đã lưu riêng cho một video **không được sao chép sang chiến dịch mới**, để tránh xóa nhầm những video khác. Chiến dịch/phiên cũ vẫn giữ cấu hình đã chốt. Tab Video đơn có thanh cuộn bên phải để tới nút chạy khi Windows dùng cỡ chữ lớn.

Đã cài bộ GPU riêng `.venv-inpaint` trên máy này, không đổi môi trường TTS. Khi chuyển máy, cần NVIDIA/CUDA, driver phù hợp, ít nhất2,5GB VRAM trống và chạy `tools/setup_inpaint.ps1` trong PowerShell sau khi cài Tèo. Lần đầu tải PyTorch CUDA khoảng3,5GB cùng thư viện và mô hình66MB; cần thêm chỗ đĩa cho môi trường. Không tự chuyển sang CPU khi GPU lỗi, không tự gọi dịch vụ trả phí. Bộ này hiện dành cho bản chạy từ mã nguồn; chưa đóng gói chung vào bản EXE.

Thử thật trên RTX4070SUPER:90 khung hình/3giây nguồn576×1024, xử lý6,48giây, đỉnh bộ nhớGPU1,15GB (số đo một mẫu, không phải cam kết tốc độ). Đủ90khung, độ lệch mốc tối đa0,34ms; các điểm ảnh ngoài vùng chọn không đổi trong bản trung gian. Bản MP4 cuối vẫn qua mã hóa thông thường. Mẫu nằm tại `work/subtitle-clean-research/preview-clean.mp4`; chưa chạy xóa cả video dài.

Nguồn: [STTN gốc](https://github.com/researchmm/STTN), [Video Subtitle Remover](https://github.com/YaoFANGUK/video-subtitle-remover), [PyTorch CUDA](https://pytorch.org/get-started/previous-versions/). Mã/giấy phép, commit và SHA mô hình được ghi tại `teodub/vendor/sttn/NOTICE.md`. Bản này dùng STTN; không tích hợp ProPainter.

## Chỉnh âm lượng tiếng gốc và giọng lồng tiếng

Trong **Video đơn → Âm lượng khi xuất video**, kéo thanh hoặc nhập phần trăm riêng cho **Tiếng video gốc** và **Giọng lồng tiếng**. Cả hai nhận 0–200%: **0%** tắt nguồn tiếng đó; **100%** giữ mức ban đầu; **200%** tăng gấp đôi biên độ. Nút **Mặc định** đưa tiếng gốc về **10%** và giọng đọc về **100%**. Khi âm trộn quá lớn, bộ giới hạn đỉnh âm thanh của bản xuất vẫn hoạt động.

Âm lượng được áp dụng lúc ghép bản xuất; không thay tốc độ, cao độ hay lời đọc. **Nghe thử giọng / Nghe lại** vẫn phát mẫu ở mức 100% để chọn chất giọng. Tải video và dịch Sub riêng không phụ thuộc hai thanh này. Video không có âm thanh gốc thì thanh tiếng gốc không thêm âm thanh. Tắt giọng vẫn giữ phụ đề và cách căn thời lượng theo lời; bước tạo giọng của quy trình vẫn chạy.

Tèo nhớ lựa chọn cho lần mở sau. Mỗi phiên chốt hai mức âm lượng lúc bắt đầu; **Tiếp tục phiên trước** giữ mức đã chốt, không thay bản xuất hoặc tệp nguồn đã có. Để dùng cho **chiến dịch mới**, chỉnh ở Video đơn rồi bấm **Lấy cấu hình từ tab Video đơn** trước khi tạo chiến dịch. Chiến dịch đã lưu giữ nguyên mức cũ; phiên cũ chưa có trường âm lượng dùng tiếng gốc10%/giọng100% như trước.

Mở lại Tèo khi công việc đang chạy đã nghỉ để thấy hai thanh mới.

## Adam có sẵn trong VieNeu — miễn phí trên máy

Trong **Video đơn → Bộ giọng: Trên máy · miễn phí → Giọng đọc**, chọn **Adam** hoặc **Adam bựa** rồi bấm **Nghe thử giọng**. Đây là các giọng preset có thật trong VieNeu 3.8.3 đang cài, không cần nhập MP3. **Mô hình AI… → Tạo giọng → VieNeu v3 Turbo** có cả hai; Nano chỉ có Adam. Gợi ý bắt đầu với Turbo, tốc độ 1,00× để nghe chất giọng trước khi tăng tốc.

Theo [danh sách giọng của tác giả VieNeu](https://github.com/pnnbao97/VieNeu-TTS#available-voices), Adam là giọng Nam, Adam bựa là giọng Bắc và thuộc nhóm giọng tác giả đề xuất. **ADAM (theo mẫu)** là lựa chọn riêng, dùng tệp bạn nhập. Tên Adam không chứng minh đồng nhất với giọng Adam của ElevenLabs hoặc CapCut; hãy so sánh bản nghe thử. Bản này không kết nối hay tiêu hạn mức ElevenLabs.

Lựa chọn dùng được cho nghe thử, lồng tiếng và chiến dịch mới (bấm **Lấy cấu hình từ tab Video đơn**). Giọng và model được chốt theo từng phiên; bản giọng đã tạo và mẫu ADAM cũ được giữ nguyên. Khi mở lại app sau khi công việc đang chạy đã nghỉ, hai giọng mới sẽ xuất hiện. Mô hình lần đầu có thể cần tải; trên máy đã có model thì tạo giọng cục bộ.

## Lưu Google Drive qua thư mục đồng bộ — không cần JSON

Trong **Video đơn** hoặc **Chiến dịch**, bấm **Chọn thư mục Drive…**. Cách mặc định mới dùng **Google Drive cho máy tính** và dung lượng tài khoản đang có; không cần thiết lập Google Cloud, nhập thẻ hay chọn tệp JSON.

1. Cài [Google Drive cho máy tính chính thức](https://www.google.com/drive/download/) và tự đăng nhập tài khoản có 5 TB.
2. Trong ứng dụng Drive, thêm một thư mục thành phẩm trên máy để **đồng bộ với Google Drive**, hoặc chọn một thư mục đang có trong **Drive của tôi**. Trên máy hiện tại đã chuẩn bị `C:/Users/chungpc/Videos/Teo Drive` để có thể chọn làm thư mục nhận.
3. Trong Tèo, chọn đúng thư mục vừa bật đồng bộ, đánh dấu **Tôi đã bật đồng bộ thư mục này trong Google Drive**, bật **Tự chép thành phẩm…** rồi **Lưu lựa chọn**.
4. Chạy video / tạo chiến dịch mới. Sau khi xuất và kiểm tra thành công, Tèo giữ bản xuất tại nơi cũ và chép **MP4 + SRT** vào thư mục `Teo-<mã phiên>` bên trong nơi đồng bộ. Mỗi phiên có nơi nhận riêng, tiếp tục không tạo thêm bản và không ghi đè tệp khác cùng tên.

Tèo báo **Đã chép · kiểm tra Drive**; đây là xác nhận bản chép trên máy, **chưa phải xác nhận đã tải lên Google Drive**. Xem trạng thái tại biểu tượng Google Drive; mạng chậm/tắt Drive thì việc tải lên còn chờ. Tèo không tạo link chia sẻ hoặc thay quyền xem khi dùng cách này. Đồng bộ do ứng dụng Drive quản lý, gồm cả thay đổi/xóa tệp trong thư mục; giữ bản xuất riêng nếu cần bản dự phòng.

Nếu nơi nhận mất kết nối, hết dung lượng hoặc chép lỗi, video đã xuất vẫn giữ nguyên. Bấm **Tiếp tục phiên trước** / **Chạy–Tiếp tục chiến dịch** để chép tiếp, không dịch/tạo giọng/dựng lại phần đã hoàn tất. Lựa chọn mới không đổi các công việc/chiến dịch cũ. **Cách kết nối khác…** giữ cửa sổ OAuth/JSON cũ cho người đã thiết lập API, không phải bước bắt buộc.

Phần chép và giao diện đã được kiểm bằng dữ liệu thử riêng; chưa xác nhận đồng bộ lên tài khoản 5 TB thật khi Drive chưa được cài/đăng nhập. [Hướng dẫn Drive của Google](https://support.google.com/drive/answer/10838124?hl=vi).

## ADAM theo mẫu — tạo giọng trên máy

Chọn **Nhập mẫu ADAM…** trong Video đơn, chọn một đoạn âm thanh/video ADAM nói rõ 3–8 giây rồi **Lưu mẫu ADAM**. Tệp dài hơn lấy 8 giây đầu. Tèo lưu bản WAV riêng trong thư viện dữ liệu trên máy và tự chọn **ADAM (theo mẫu)** ở bộ giọng Trên máy. Sau đó bấm **Nghe thử giọng**; nếu phù hợp, dùng để lồng tiếng như các giọng khác. Hỗ trợ VieNeu Turbo/Nano, tốc độ và Kiểu đọc hiện có. Với chiến dịch mới, bấm **Lấy cấu hình từ tab Video đơn** sau khi chọn ADAM.

Chưa có mẫu ADAM đi kèm. Khi chưa nhập mẫu, mục ADAM hiện hướng dẫn và không cho tạo giọng; không dùng giọng khác để thay thế. Bạn cần tự chọn tệp mẫu ADAM có sẵn trên máy. Giọng do model cục bộ mô phỏng, không phải kết nối Adam gốc của ElevenLabs và không bảo đảm giống hệt. Không cần tài khoản ElevenLabs hoặc hạn mức dịch vụ giọng nói; model cục bộ có thể cần tải lần đầu như các giọng theo mẫu khác.

Mỗi lần nhập tạo phiên bản mẫu mới, giữ bản trước cho các công việc đã lưu. Mẫu được nhớ khi mở lại Tèo; sau nhập thành công không cần giữ tệp mẫu gốc ở chỗ cũ. Lỗi mẫu quá ngắn/im lặng/không đọc được không thay lựa chọn đang có. Không có đoạn ADAM thật thì chưa thể kiểm tra độ giống bằng nghe thử.

## Video đơn: chạy từng bước hoặc tự động toàn bộ

Trong tab **Video đơn**, dưới ô dán link có bốn nút:

1. **Tải video**: chỉ tải và kiểm tra video gốc, chưa dùng AI. Xong sẽ chọn tệp trên máy để dùng cho bước sau. Nếu bạn chọn video trên máy, nút chỉ kiểm tra tệp đó.
2. **Tải video và dịch Sub**: có thể bấm ngay sau khi dán link, hoặc dùng video đã tải ở bước 1. Nhận dạng tiếng Trung và dịch sang Việt bằng model AI đã chọn, rồi dừng. Tự điền SRT Việt vào form; chưa tạo giọng hoặc xuất video. Nút này tạo lượt dịch mới từ video, không lấy nội dung ở ô SRT có sẵn.
3. **Xem / sửa lời thoại**: mở bản lời đã chuẩn bị hoặc SRT Việt đang chọn. Chưa có lời thì báo bạn chạy bước 2; nút này không tự gửi dịch.
4. **Lồng tiếng và xuất video**: dùng SRT Việt đang chọn, giọng/tốc độ/kiểu đọc hiện tại để xuất thành phẩm. Thiếu SRT thì báo cần chuẩn bị lời trước, không tự dịch.

Bước 1 có thể bỏ qua nếu bấm bước 2 trực tiếp. Bước 3 là tùy chọn: không cần sửa thì chuyển từ bước 2 sang bước 4. Sau tải/dịch, Tèo dừng chờ bạn bấm bước tiếp theo. Chọn **Tự động toàn bộ** ở hàng dưới để tải/kiểm tra video, nhận dạng, dịch mới, tạo giọng và xuất liền mạch. Nút này **bỏ qua SRT đang chọn**, kể cả bản lời đã sửa của video trước; khi bắt đầu thành công ô SRT được bỏ chọn, tệp cũ vẫn giữ nguyên. Hoàn tất sẽ tự chọn video gốc và bản lời mới để xem/sửa. Muốn dùng nguyên lời đã sửa hoặc SRT có sẵn, bấm **4. Lồng tiếng và xuất video**. Bước dịch vẫn dùng hạn mức tài khoản Codex. Dừng/Tiếp tục giữ cấu hình và phần đã làm của từng phiên; mở lại tool không tự chạy bước kế tiếp. Phiên lỗi cũ vẫn giữ SRT cũ trong cấu hình: chọn lại video và bấm **Tự động toàn bộ** để tạo phiên mới thay vì tiếp tục phiên lỗi đó.

Chọn một câu trong bảng, sửa lời Việt ở ô bên dưới; dùng **Câu trước / Câu tiếp** để chuyển. Bấm **Lưu lời thoại để lồng tiếng**, sau đó **Lồng tiếng và xuất video**. Tèo dùng bản sửa cho giọng và Sub, không dịch lại. Bản sửa được lưu thành **một tệp SRT riêng**, giữ mốc/thứ tự câu; không ghi đè bản dịch hoặc thành phẩm cũ. Lời đã sửa vẫn được chọn khi mở lại tool. Nếu bật **Từ ngữ**, bảng thay từ vẫn áp dụng vào lời đã sửa; tắt mục đó nếu muốn giữ nguyên câu chữ.

Không để câu trống hoặc còn chữ Trung. Bản lời chuẩn bị/sửa được gắn với video nguồn để tránh dùng nhầm; đổi sang video khác cần bỏ SRT đang chọn hoặc chọn đúng bản lời. Lượt lồng tiếng mới tạo phiên riêng, không thay đổi thành phẩm đã có. Tải video mới sẽ xóa lựa chọn SRT khỏi form (tệp vẫn được giữ); dịch xong sẽ chọn bản lời mới.

## Tự lưu video thành phẩm lên Google Drive

Trong **Video đơn**, chọn **Kết nối / chọn nơi lưu…** cạnh checkbox **Tự lưu video thành phẩm lên Google Drive**. Kết nối tài khoản Google, tải danh sách hoặc **Tạo thư mục mới…**, chọn thư mục, bật tự lưu rồi **Lưu lựa chọn**. Tèo nhớ lựa chọn cho video mới; phiên đang làm/đang tiếp tục vẫn dùng tài khoản và thư mục đã chốt. Phần **Chiến dịch hàng loạt** có checkbox và nút kết nối tương ứng cho chiến dịch mới. Lựa chọn này không tự bật trên video/chiến dịch cũ hoặc tự tải video đã có chỉ vì mở app.

Sau khi xuất video thành công, Tèo tự tải **video MP4/MOV thành phẩm** vào thư mục Drive đã chọn, giữ nguyên tên tệp cùng ngày tải đã dùng trên máy. Bản gốc, SRT và các tệp kiểm tra vẫn ở máy; bản này chỉ gửi video thành phẩm. Cột **Google Drive** trong bảng chiến dịch hiện chờ tải, đang tải cùng phần trăm, đã lưu, cần kết nối lại hoặc lỗi. **Xem chi tiết / nguyên nhân** có lý do và link Drive sau khi xác nhận đúng tệp. Video đơn hiển thị kết quả Drive riêng dưới thông báo xuất video; tiến độ lồng tiếng không bị giảm khi bắt đầu upload.

Nếu mạng lỗi, thiếu dung lượng hoặc phiên Google hết hạn, video đã dựng trên máy vẫn hoàn tất. Với video đơn, dùng **Tiếp tục phiên trước** khi còn thiếu kết quả Drive; với chiến dịch đã bật tự lưu, dùng **Chạy / Tiếp tục** để kiểm tra và tiếp tục phần tải. Tèo giữ mã tệp/phiên tải, đối chiếu lại dữ liệu trước khi gửi tiếp, tránh tạo bản sao sau timeout; không dịch, tạo giọng hay dựng lại video đã kiểm tra hoàn tất. **Dừng** cũng dừng phần tải và giữ thông tin để tiếp tục. Không tự tải lại khi chỉ mở ứng dụng hoặc xem danh sách.

### Kết nối Google lần đầu

Lần đầu cần tệp kết nối OAuth của ứng dụng; Tèo chưa đi kèm một Google Cloud client dùng chung. Bạn thao tác trong tài khoản Google của mình theo [hướng dẫn chính thức của Google](https://developers.google.com/workspace/drive/api/quickstart/python):

1. Tạo/chọn dự án trong [Google Cloud Console](https://console.cloud.google.com/) và bật **Google Drive API**.
2. Trong **Google Auth platform**, thiết lập tên ứng dụng/thông tin liên hệ. Với tài khoản Gmail cá nhân, chọn **External**; nếu dùng chế độ Testing, thêm chính email sẽ đăng nhập vào **Test users**. Cấp quyền `https://www.googleapis.com/auth/drive.file` cho ứng dụng.
3. Vào **Clients → Create client → Desktop app**, tạo rồi tải tệp JSON. Chọn tệp bằng **Chọn tệp…** trong cửa sổ Google Drive của Tèo; không gửi tệp, mật khẩu hoặc khóa vào chat.
4. Bấm **Kết nối tài khoản Google…**, tự đăng nhập và cho phép trên trang Google được mở trong trình duyệt mặc định. Quay lại Tèo, chọn/tạo thư mục và lưu lựa chọn. Khi token còn hợp lệ, Tèo dùng lại kết nối cho những lần sau.

Tèo dùng [quyền theo từng tệp `drive.file`](https://developers.google.com/workspace/drive/api/guides/api-specific-auth), nên danh sách không phải toàn bộ thư mục Drive cá nhân. Có thể tạo thư mục nhận video ngay trong Tèo. Bản này chưa tích hợp Google Picker để cấp quyền thêm cho một thư mục cũ bất kỳ hoặc chọn Shared Drive. Không tự chia sẻ công khai tệp hoặc thay quyền của thư mục.

Theo [quy định OAuth của Google](https://developers.google.com/identity/protocols/oauth2#expiration), ứng dụng External ở chế độ Testing với quyền Drive có thể cần kết nối lại sau 7 ngày. Kết nối cũng có thể hết hạn hoặc bị thu hồi. Tèo lưu kết nối và địa chỉ phiên tải bằng mã hóa Windows; chuyển sang máy/tài khoản Windows khác cần kết nối lại. Không dùng phiên Google từ Codex, không cần mở Codex để chạy tải lên và không thêm lượt AI.

Kiểm thử bằng dữ liệu tạm, API Google giả lập và giao diện ẩn; **chưa đăng nhập hoặc tải lên Google thật**. Nguồn triển khai: [OAuth cho ứng dụng máy tính](https://developers.google.com/identity/protocols/oauth2/native-app), [upload có thể tiếp tục](https://developers.google.com/workspace/drive/api/guides/manage-uploads), [mã tệp tạo trước để tránh trùng](https://developers.google.com/workspace/drive/api/guides/create-file). Mở lại Tèo khi công việc đang chạy đã nghỉ để nạp các nút mới.

## Tùy chọn từ ngữ cho lời đọc và Sub

Trong **Video đơn**, bấm **Từ ngữ…**, bật **Bảng thay từ**, nhập mỗi dòng theo dạng `từ gốc => từ thay`, rồi **Xem thử → Lưu lựa chọn**. Có ví dụ `bán => báng` theo yêu cầu; mặc định tính năng **tắt**. Bạn có thể sửa câu thử và bảng, tối đa 50 quy tắc, 100 ký tự mỗi ô. Cả bản dịch AI và SRT Việt có sẵn đều dùng bảng khi bật. Đổi chữ có thể đổi phát âm hoặc nghĩa; cần nghe duyệt trước khi dùng.

Tèo giữ bản dịch AI gốc và chỉ tạo bản thay từ riêng trước bước tạo giọng. Lời đọc, SRT thành phẩm và Sub ghi vào hình dùng cùng nội dung đã thay. Kết quả cuối vẫn có **một SRT Việt**; bản gốc và chứng cứ nằm trong cache nội bộ để đối chiếu. Ghi chú bản dịch bổ sung các dòng trước/sau thay từ khi nguồn được dịch bằng Codex. Thay từ và **Xem thử** chạy tại chỗ, không gọi thêm AI hoặc dùng thêm token dịch.

Chỉ thay từ/cụm khớp trọn vẹn, có giữ chữ hoa; không thay liên tiếp từ vừa sinh ra. Cụm dài được ưu tiên. Các cụm kỹ thuật như **bán kính, bán cầu, bán đảo, bán dẫn, bán tự động, giá trị, giá đỡ, giá thể** được giữ nếu bạn chỉ đặt quy tắc từ đơn “bán”/“giá”; quy tắc ghi cả cụm có thể thay cụm đó. Đây là những ngoại lệ ngôn ngữ, không phải danh sách từ vi phạm của nền tảng.

Bảng được nhớ khi mở lại app và chốt riêng cho từng phiên. **Tiếp tục phiên trước** dùng đúng bảng đã lưu; sửa bảng chỉ áp dụng video mới. Với chiến dịch mới, bấm **Lấy cấu hình từ tab Video đơn** trước khi tạo chiến dịch. Chiến dịch/phiên cũ chưa có lựa chọn này mặc định tắt, không đổi nội dung đã làm. Cache kiểm nguồn/bảng/bản thay từ và chứng cứ; tệp đã bị sửa không được ghi đè hoặc dùng lại âm thầm.

**Đây là tùy chọn biên tập từ ngữ, không bảo đảm tránh giảm tương tác, tránh xử lý vi phạm hoặc giải quyết bản quyền.** Nguồn chính thức được kiểm không xác nhận một danh sách cấm chung chỉ gồm “mua, bán, giá”. [Meta nói về nội dung spam](https://about.fb.com/news/2025/04/cracking-down-spammy-content-facebook/) như caption không liên quan, hashtag quá nhiều và hành vi thao túng tương tác. [Meta nói về tính nguyên gốc](https://about.fb.com/news/2026/03/rewarding-original-creators-on-facebook/) rằng đăng lại và chỉnh sửa nhỏ như thêm Sub hoặc đổi tốc độ vẫn có thể bị giảm phân phối. Đổi cách viết không tạo quyền sử dụng video/nhạc của người khác; cần dùng nội dung bạn có quyền sử dụng. Tool không tự đăng bài hoặc kiểm chứng quyền chỉ vì bảng được bật.

Mở lại Tèo khi công việc nghỉ để nạp nút mới; không cần dừng worker đang chạy để cập nhật.

## Dịch sát nghĩa, tự nhiên tiếng Việt

Gợi ý cho yêu cầu giữ nghĩa và văn nói Việt: **GPT-6.1 Sol + suy luận Vừa** trong **Video đơn → Mô hình AI…**. Sol cân bằng năng lực và chi phí; Luna phù hợp công việc cần hiệu quả, Astra dành cho nội dung phức tạp theo [hướng dẫn chọn model OpenAI](https://developers.openai.com/api/docs/guides/model-selection). Đây là lựa chọn khởi đầu, chưa phải kết quả so sánh chất lượng trên video của bạn. Có thể giữ GPT-5.6 Sol nếu muốn dùng dòng 5.6. [Khả năng dùng model trong Codex](https://learn.chatgpt.com/docs/models) còn phụ thuộc tài khoản và phiên bản ứng dụng.

Phiên dịch mới dùng bộ hướng dẫn `zh-vi-context-v2`: đọc ngữ cảnh toàn bài, giữ phủ định/số liệu/điều kiện/mức độ chắc chắn, giữ sắc thái và độ trang trọng của người nói, chọn xưng hô từ ngữ cảnh rõ ràng, tránh tự đoán giới tính hoặc quan hệ. Thuật ngữ và thành ngữ được xét theo chủ đề; có ví dụ về lời chưa chắc chắn, rải mồi câu và giới hạn chống nước. Câu nhận dạng chưa rõ được ghi chú để duyệt, không tự bịa dữ kiện. Bản dịch vẫn phải đủ từng câu, đúng thứ tự và giữ mốc nguồn.

Đây là cải thiện hướng dẫn và ví dụ, **không huấn luyện lại trọng số mô hình** hoặc tự học từ mọi video đã chạy. [OpenAI khuyến nghị](https://developers.openai.com/api/docs/guides/model-optimization) dùng hướng dẫn rõ, ngữ cảnh, ví dụ đúng và đánh giá trên nội dung đại diện trước khi cân nhắc fine-tuning. Tèo dịch từ transcript, không trực tiếp nghe/xem video; model mạnh hơn không bảo đảm sửa được lời nguồn nhận dạng sai.

Lựa chọn model/default đang có được giữ nguyên. Chọn GPT-6.1 Sol rồi lưu để áp dụng cho **phiên mới**; tài khoản không hỗ trợ thì dừng, không tự đổi model. Phiên dịch đã chốt v1 tiếp tục bằng quy tắc v1/model đã lưu; cache và bản dịch cũ không bị đổi phiên bản hoặc gửi dịch lại. Không thêm lượt AI kiểm lại riêng hay dịch vụ trả phí. Nâng cấp này kiểm bằng CLI giả lập và giao diện Tk ẩn; chưa gửi yêu cầu GPT-6.1 Sol thật hoặc đo chất lượng bản dịch thực tế. Mở lại Tèo khi công việc nghỉ để nạp lựa chọn mới.

## Đăng video theo lịch bằng Chrome của Tèo

Luồng này dùng giao diện Facebook và hồ sơ Chrome của từng tài khoản đã lưu trong Tèo. Không cần Page token hoặc hoàn tất đăng ký Meta App để tạo lịch; tài khoản vẫn phải đăng nhập và có quyền đăng trên đúng Page. Phần kết nối API là lựa chọn riêng.

1. Trong **Chiến dịch hàng loạt**, bấm chuột phải vào dòng video **đã hoàn tất** → **Đăng video…**. Tèo chỉ nhận thành phẩm đã dựng và có dữ liệu kiểm tra của phiên đó, không lấy video gốc hoặc tệp đang xử lý. Video còn cần duyệt bản dịch phải hoàn tất bước duyệt trước.
   Menu này cũng có **Mở video** để xem thành phẩm bằng trình phát video trên máy. Mục này dùng đúng dòng đã bấm chuột phải, mở được cả thành phẩm đang chờ nghe duyệt; chưa có thành phẩm hoặc tệp đã mất thì nút bị khóa.
2. Chọn **tài khoản Facebook**, **Page**, nhập **caption cố định**, **hashtag cố định** và **giờ đăng** theo dạng `YYYY-MM-DD HH:MM`, giờ Việt Nam **UTC+7**. **Lưu mẫu caption / hashtag cho Page** nhớ nguyên nội dung bạn nhập để nạp lại lần sau; không dùng AI để viết lại mẫu.
3. Bấm **Xem trước lịch đăng**, kiểm đúng tài khoản, Page/ID/link, video, nội dung và giờ, rồi **Lưu lịch / Cho phép tự đăng**. Đây là bước cho phép Tèo tự chuẩn bị và bấm Đăng cho chính bài đó vào giờ đã duyệt. Bấm lặp không tạo hai lịch cho cùng video trên cùng Page.
4. Vào **Quản lý Page → chọn Page → Quản lý Page / Bài viết → Lịch đăng video** để xem thời gian, trạng thái và lý do. Có **Tạm dừng**, **Đổi giờ** và **Bật / Tiếp tục** trước lượt gửi. **Bỏ lịch chưa gửi** giữ lịch sử/tệp và cho phép tạo một lịch mới sau khi duyệt lại. Lịch đã có thao tác Chrome cần kiểm tra trên Facebook trước, không có nút tự gửi lại.

Trong bảng **Chiến dịch hàng loạt**, cột **Trạng thái đăng bài** nằm sau **Thời gian hoàn thành**, tự cập nhật mỗi giây: **Chưa đăng**, **Đang lên lịch**, **Đang đăng**, **Đăng thành công**. Lịch dừng/lỗi/kết quả chưa rõ có **Tạm dừng lịch**, **Đăng thất bại** hoặc **Cần kiểm tra**. Chọn **Xem chi tiết / nguyên nhân** để xem Page, tài khoản, giờ đăng, lý do và link bài đã xác nhận. Trạng thái lồng tiếng và tiến độ vẫn hiển thị riêng.

Nếu một video có lịch trên nhiều Page, cột chỉ báo thành công khi mọi lịch còn hiệu lực đã được xác nhận; đang dở có số như **đã đăng 1/2**. Lịch đã bỏ giữ trong phần chi tiết nhưng không tính vào số Page đang chờ. Trạng thái này đọc lịch và bằng chứng đã lưu của Tèo, không tự mở Facebook hoặc đăng lại. **Chưa đăng** nghĩa là chưa có lịch còn hiệu lực trong Tèo; bài bạn tự đăng ngoài Tèo chưa được kiểm tra. Lưu lịch chưa bật vẫn hiện **Đang lên lịch**; chi tiết cho biết chưa bật tự động. Thiếu bằng chứng hoặc video/nội dung bị đổi sẽ báo **Cần kiểm tra**, không báo thành công chỉ từ link nhập tay.

Giữ **Tèo mở và máy không ngủ** đến giờ đăng. Chrome có thể được Tèo mở bằng đúng hồ sơ tài khoản vào giờ đó. Đóng cửa sổ quản lý Page vẫn giữ lịch; đóng Tèo dừng lịch và mở lại cần bật tiếp các lịch chưa gửi. Lỡ giờ quá hai phút sẽ báo cần đổi giờ, không tự đăng dồn. Các bài của cùng tài khoản xử lý lần lượt; bài khác không được chen vào form đang chuẩn bị.

Trước khi tải lên, Tèo kiểm tra video bằng mã SHA-256 đã lưu cùng thành phẩm. Tèo giữ cố định Page/nội dung/tệp được duyệt, kiểm lại danh tính người đăng và tệp trong form, bấm Đăng một lần. Chỉ ghi **Đã xác nhận đăng** khi quan sát được bằng chứng và link bài mới thuộc Page, lưu cùng kết quả. Lỗi, yêu cầu đăng nhập/xác minh, nội dung bị đổi, không thấy phản hồi trong sáu phút hoặc kết quả chưa rõ đều dừng để kiểm tra. Không suy ra thành công chỉ từ nút Đăng hoặc form biến mất.

Trong **Soạn & đăng bài**, còn có lựa chọn **Cách đăng: Trình duyệt web** cho bài chữ/ảnh/video. Lưu bản nháp rồi xem trước để chuẩn bị trong Chrome. Sau khi có bản xem trước thực tế, bạn có thể tự bấm Đăng trong Chrome hoặc chọn Tèo bấm sau khi duyệt. Link ghi thủ công được đánh dấu **chưa xác minh**, không tự đổi thành đã đăng.

**Giới hạn hiện tại:** bộ điều khiển chỉ hỗ trợ form Tạo bài viết có một hộp thoại và nút Đăng rõ ràng; chỉ tự chuyển sang Page khi có nút **Switch now / Chuyển ngay** ở đúng Page đã xác minh. Trình tạo video/Reels nhiều bước hoặc giao diện khác sẽ báo chưa hỗ trợ và giữ Chrome để kiểm tra, không tự đoán nút. Các kiểm tra dùng dữ liệu tạm, Tk ẩn và trang Facebook giả lập; **chưa kiểm chứng đăng video trên Page thật**. Mở lại Tèo khi các công việc đang chạy đã nghỉ để nạp bản mới, giữ nguyên hồ sơ đã lưu.

Cách điều khiển web không bảo đảm tránh hạn chế tài khoản. Meta nói tự động truy cập/thu thập dữ liệu khi chưa được cho phép vi phạm điều khoản, và xử lý spam hoặc hành vi thao túng phân phối. Không có tỷ lệ khóa hoặc số bài/ngày an toàn đã được xác minh cho công cụ này. Xem [giải thích của Meta về tự động hóa](https://about.fb.com/news/2021/04/how-we-combat-scraping/) và [chính sách xử lý spam](https://about.fb.com/news/2025/04/cracking-down-spammy-content-facebook/).

Lịch/mẫu lưu tại `page-publishing/schedules.sqlite3`, bản nháp/bằng chứng trong `page-publishing/activity.sqlite3`. Không sao chép hồ sơ Chrome cá nhân, đọc cookies hoặc tự xử lý mật khẩu/SMS. Thiết lập API Meta đã tạm dừng tại bước xác minh số điện thoại theo yêu cầu của bạn, không tự tiếp tục từ lịch đăng web.

Kiểm bản nâng cấp: **258 bài đạt**, không skip, trong 43,078 giây. Log `work/facebook-browser-schedule-tests.log`; phạm vi gồm lịch/store/Tk ẩn, menu chiến dịch và routing đúng tài khoản, 12 trang Playwright giả lập, hồi quy API/browser/Page/app. Không dùng tài khoản, nội dung hoặc trình duyệt thật để đăng thử.

## Quản lý Page, bài viết và thông tin qua API Meta

Trong **Quản lý Page**, chọn tài khoản → chọn Page đã lưu → **Quản lý Page / Bài viết**. Mỗi Page có cửa sổ riêng, giữ đúng tài khoản kể cả khi bạn chuyển sang tài khoản khác ở tab chính. Có năm phần: **Thông tin Page**, **Soạn & đăng bài**, **Bài trên Facebook**, **Kết nối API**, **Lịch đăng video**.

**Mở Meta Developers** trên tab chính và các nút thiết lập trong cửa sổ Page dùng **chính Chrome của tài khoản trong Tèo**, không mở Chrome mặc định hay trình duyệt Codex. Nếu Chrome của tài khoản đang mở, đưa trang vào cùng phiên; nếu đang đóng, mở lại hồ sơ đã lưu. Facebook/Meta có thể yêu cầu đăng nhập hoặc xác minh lại. Nút chỉ mở trang, chưa tự tạo App hoặc cấp quyền. Trong bản Tèo cũ đang chạy, bạn có thể dán `https://developers.facebook.com/apps/` vào thanh địa chỉ của Chrome mở từ Tèo. Khi công việc nghỉ, đóng/mở lại Tèo để nạp các nút mới; giữ nguyên thư mục hồ sơ.

Hai nút **Mở Meta Developers trong Tèo** và **Mở công cụ lấy quyền API trong Tèo** báo tiến trình ngay trong cửa sổ Page. Khi Chrome đang khởi động hoặc Load Page, Tèo giữ yêu cầu và chờ thực hiện; các nút tạm khóa để tránh gửi trùng. Lỗi mở trang, phiên đang đóng hoặc không nhận được phản hồi đều có thông báo tại chỗ. Nếu báo **Đã mở** nhưng chưa thấy trang, chọn cửa sổ Chrome của tài khoản đó trên thanh tác vụ. Nếu báo phần điều khiển cũ, mở lại Tèo khi công việc nghỉ; hồ sơ đăng nhập đã lưu được giữ. Ô khóa truy cập Page còn trống thì **Kiểm tra / Lưu kết nối** chỉ hướng dẫn lấy khóa, chưa gửi yêu cầu API.

1. Hoàn tất đăng ký/xác minh nhà phát triển nếu Meta yêu cầu, tạo/chọn Meta App và cấp quyền cho đúng Page. Người dùng tự xử lý xác minh trong trình duyệt. Các quyền thường dùng là `pages_show_list`, `pages_read_engagement`, `pages_manage_posts`; điều kiện cấp quyền/xét duyệt phụ thuộc App và tài khoản. Không coi đăng nhập Chrome là cấp quyền API.
2. Trong **Kết nối API**, nhập **Page ID**, **Phiên bản API** của App và **khóa truy cập Page** (Page access token). Dùng nút mở công cụ cấp quyền trong cùng Chrome của Tèo. Không gửi khóa vào chat. Bấm **Kiểm tra / Lưu kết nối**: Tèo kiểm tra `/me` trả đúng Page ID/tên/danh mục rồi mã hóa khóa bằng Windows. Kiểm tra này xác nhận khả năng đọc và đúng Page; quyền đăng vẫn do Meta kiểm khi gửi. Đổi máy/tài khoản Windows hoặc hết hạn phải kết nối lại.
3. Lưu chủ đề/ghi chú trong **Thông tin Page**. Bấm **Cập nhật từ Meta** để lấy tên, danh mục, giới thiệu, người theo dõi, lượt thích và tối đa 25 bài gần nhất qua API. **Lịch sử thông tin** giữ 100 lần cập nhật gần nhất. Có tùy chọn tự cập nhật mỗi 5 phút khi cửa sổ Page mở, mặc định tắt và dừng khi có lỗi/hết hạn. Dấu **—** là chưa có số liệu, khác với **0**.
4. Trong **Soạn & đăng bài**, nhập nội dung, tùy chọn tối đa 10 ảnh JPG/PNG/WebP hoặc một video MP4/MOV, rồi **Lưu bản nháp**. Bấm **Xem trước / Đăng ngay**, kiểm tên Page API/ID/nội dung/tệp, sau đó **Đăng bài này** mới gửi thật. Gửi/chờ API chạy ngoài giao diện. Bản nháp và ghi chú dùng được trước khi kết nối API.
5. Tèo chỉ ghi **Đã đăng** khi Meta xác nhận và trả link bài/video. **Meta đang xử lý** giữ mã bài để bấm **Kiểm tra kết quả**, không tải lại. **Chưa rõ kết quả** khi mất mạng/gửi bị ngắt hoặc Meta báo lỗi xử lý: mở Page để đối chiếu, không tự gửi lại. Bài đã có mã Meta bị khóa gửi lại; ảnh tải xong ở chế độ chưa công khai được giữ mã nếu lượt tạo bài bị Meta từ chối rõ ràng. **Bỏ khỏi danh sách** chỉ ẩn bài trong Tèo, giữ lịch sử/tệp/bài Facebook.

Bản API đăng bài chữ, ảnh và video Page thông thường, chưa có lịch đăng qua API, Reels riêng, OAuth một nút hoặc số liệu reach/Insights. Lịch đăng qua Chrome là luồng riêng ở phần trên. Có bảng bài gần nhất cùng lượt thích/bình luận/chia sẻ và xem số liệu của bài đã gửi khi API trả được. Dữ liệu/khóa được tách theo tài khoản và Page, không tự đăng khi mở app. Hoạt động lưu tại `page-publishing/activity.sqlite3`, khóa mã hóa tại `page-publishing/credentials`; hồ sơ Chrome và dữ liệu video cũ không thay đổi.

Mặc định API **v26.0** theo [cấu hình SDK chính thức của Meta](https://github.com/facebook/facebook-python-business-sdk/blob/main/facebook_business/apiconfig.py), kiểm ngày 03/10/2026; cho sửa theo App. Endpoint/fields dựa [Page SDK](https://github.com/facebook/facebook-python-business-sdk/blob/main/facebook_business/adobjects/page.py), [Video SDK](https://github.com/facebook/facebook-python-business-sdk/blob/main/facebook_business/adobjects/advideo.py) và [mẫu cấp quyền/trạng thái của Meta](https://github.com/fbsamples/reels_publishing_apis/blob/main/fb_reels_publishing_api_sample/index.js); không dùng phiên bản cũ trong mẫu Reels cho các lệnh mới.

Kiểm **106 bài đạt**, gồm transport Graph giả lập, dữ liệu tạm, DPAPI thật với khóa giả, tải tệp bằng bộ nhớ giới hạn, khóa chống gửi trùng, xử lý kết quả chưa rõ, Tk ẩn và hồi quy browser/accounts/app. Log `work/page-management-api-tests.log`. **Chưa tạo Meta App, cấp quyền, gọi Graph trên tài khoản thật hoặc đăng bài thật.** Người dùng báo Meta đang yêu cầu đăng ký/xác minh; bước đó còn phải hoàn tất trước khi kiểm kết nối thực tế.

Bản sửa phản hồi nút mở Meta kiểm **117 bài đạt**, gồm chờ Chrome/Load Page, phản hồi theo đúng tài khoản và cửa sổ Page, lỗi/timeout, tránh bấm trùng và tương thích khi ứng dụng còn dùng phần điều khiển cũ. Log `work/meta-browser-response-tests.log`. Kiểm bằng Tk ẩn, dữ liệu tạm và browser/API giả lập; chưa thử mở Meta trên phiên Facebook thật hoặc đóng/mở lại ứng dụng đang chạy.

## Đăng nhập Facebook và lưu nhiều Fanpage

Tab mới **Quản lý Page** đã có chức năng mở Chrome để bạn tự đăng nhập Facebook, lưu hồ sơ và mở lại để kiểm tra:

Bảng **Danh sách tài khoản Facebook** nằm bên trái, Fanpage của tài khoản đang chọn nằm bên phải. Bảng tài khoản hiển thị **Tài khoản / Page đã lưu / Trình duyệt**. Chọn dòng để chuyển tài khoản và xem đúng các Page; nhấp đôi hoặc nhấn Enter để mở Facebook của tài khoản đó. Có thể tiếp tục dùng ô chọn nhanh phía trên; hai phần chọn được đồng bộ. Kéo vạch giữa hai bảng để điều chỉnh độ rộng, dùng thanh cuộn nếu có nhiều tài khoản.

Tài khoản đã thêm trước bản nâng cấp xuất hiện tự động trong bảng. Trạng thái **Chưa mở / Đang mở… / Đã mở / Đang Load Page / Đang đóng / Đã đóng / Có lỗi** nói về phiên Chrome do Tèo quản lý, không phải kết quả xác minh đăng nhập Facebook. **Page đã lưu** là số Page đã lưu trong Tèo, không phải tổng Page trên Facebook. Đăng nhập xong mà bảng Fanpage trống: dùng khung **Lưu Page bằng link** ngay trên bảng Fanpage, hoặc **chuột phải vào tài khoản → Load Page** để đọc tự động.

1. Bấm **Thêm tài khoản…**, đặt tên để nhận biết tài khoản trong Tèo. Đây chỉ là tên hiển thị, không cần nhập mật khẩu vào tool.
2. Bấm **Đăng nhập / Mở Facebook**, tự đăng nhập và xác minh trong cửa sổ Chrome. Dùng Google Chrome đã cài trên máy; không cần đăng nhập Google vào Chrome.
3. Trong khung **Lưu Page bằng link**, dán địa chỉ của Page vào **Link Page**, nhập **Tên Page (tùy chọn)** rồi bấm **Lưu link Page** hoặc Enter. Dòng **Lưu vào tài khoản** cho biết Page thuộc tài khoản nào. Có thể lưu khi Chrome đang mở hoặc đóng, không cần Load Page thành công.
4. Chọn Page đã lưu trong bảng → **Mở Page đã chọn**. Tèo dùng cửa sổ Chrome và phiên đăng nhập của tài khoản đó; nếu cửa sổ đã đóng, mở lại đúng hồ sơ đã lưu. **Sửa Page…** cập nhật tên/link/ID khi trình duyệt của tài khoản đang đóng. Một tài khoản có thể lưu nhiều Page; **Thêm Page…** vẫn có hộp nhập đầy đủ.
5. Khi xong, bấm **Đóng / Lưu phiên tài khoản này** hoặc tự đóng cửa sổ Chrome đó. Chờ Tèo báo đã đóng rồi mở lại khi muốn kiểm tra. Trình duyệt không có giới hạn 30 phút; bạn có thể để mở trong lúc dùng Tèo.

**Lưu link Page** chỉ lưu tại máy, không yêu cầu đăng nhập hoặc đọc Facebook. Nếu để trống tên, dùng phần tên trong link hoặc nhãn **Page <ID>**; đây không phải tên đã xác minh trên Facebook. Tự lấy ID từ link `profile.php?id=…`, link dạng số hoặc `/pages/<tên>/<ID>`, bỏ thông tin theo dõi. Link đã có trong tài khoản sẽ báo trùng và giữ dữ liệu cũ; link sai/lỗi lưu giữ nguyên nội dung để sửa. Link bài viết, nhóm, danh sách Page hoặc link chia sẻ rút gọn không dùng ở đây: mở đúng Page và sao chép địa chỉ trên thanh trình duyệt. Nội dung đang nhập giữ riêng theo tài khoản trong lúc app mở, không chuyển nhầm sang tài khoản khác; Page đã bấm Lưu được giữ sau khi đóng/mở app.

Đọc tự động là tùy chọn: **chuột phải vào tài khoản trong bảng bên trái → Load Page**. Tèo dùng Chrome đang mở của đúng tài khoản, hoặc mở lại hồ sơ đã lưu nếu đang đóng. Một tab riêng mở **Trang bạn quản lý**, đọc tên/link/Page ID nếu link có ID, tự cuộn và lưu vào bảng Fanpage. **Giữ cửa sổ Chrome mở cho tới khi có kết quả**; đóng trong lúc đọc sẽ dừng lượt đó. Không tải lại các tab khác đang nhập bài.

**Load Page** nhận diện thẻ Page theo màn hình tiếng Việt **Trang bạn quản lý** trong ảnh mẫu, hoặc **Pages you manage / Your pages** với nút **Create post / Advertise**. Tèo chỉ đọc nội dung đang hiển thị trên [trang Page của Facebook](https://www.facebook.com/pages/?category=your_pages), không lấy danh sách từ API riêng. Nếu cần đăng nhập/xác minh, bạn tự thao tác trong tab Load Page; lượt đọc chờ tối đa 3 phút. Lỗi, tab đóng sớm hoặc giao diện không nhận diện được sẽ báo nguyên nhân; không tự gửi lại. Chọn **Load Page** lần nữa sau khi xử lý.

Page có cùng link hoặc Page ID được gộp trong đúng tài khoản, không tạo bản trùng. Page mới được thêm; Page từng đọc tự động được cập nhật tên/link khi có ID để đối chiếu. Tên/link bạn đã nhập hoặc sửa bằng tay được giữ. Không xóa Page cũ khi lượt đọc thiếu hoặc không thành công. Lượt đọc tới giới hạn 1.000 Page hoặc hết thời gian có dữ liệu sẽ ghi rõ **có thể chưa đủ**; cuộn tới cuối phần đã tải cũng chưa chứng minh đã đọc toàn bộ Page của tài khoản. Kết quả và thông báo của lượt đã lưu được giữ khi mở lại Tèo.

**Mở Business Suite** dùng cùng hồ sơ với Facebook và các Page của tài khoản đang chọn. Khi Chrome đang mở, các nút mở trang đưa trang vào cùng cửa sổ/phiên; không mở một hồ sơ mới. Mở lại tab đã có chỉ đưa tab ra trước, không tải lại biểu mẫu đang nhập. **Bỏ khỏi danh sách** chỉ bỏ mục Page trong Tèo, không xóa Page Facebook.

**Mỗi tài khoản Facebook có cửa sổ Chrome riêng, có thể mở cùng lúc.** Mở tài khoản A, sau đó chọn/thêm tài khoản B rồi bấm Mở Facebook; Chrome của A vẫn giữ nguyên. Các cửa sổ dùng chung bản Chrome đã cài nhưng hồ sơ và dữ liệu đăng nhập riêng. Dòng trạng thái liệt kê tài khoản đang mở/đang khởi động/đang đóng. Để xem lại một cửa sổ, chọn đúng tài khoản rồi Mở Facebook hoặc Mở Page. Nút **Đóng / Lưu phiên tài khoản này** chỉ đóng tài khoản đang chọn; các tài khoản khác tiếp tục mở. Nếu một tài khoản mở lỗi, chọn tài khoản đó để xem nguyên nhân; không ảnh hưởng phiên khác.

Mỗi tài khoản có một hồ sơ riêng, các Page trong tài khoản dùng chung hồ sơ đó. Tên tài khoản/Page và link được lưu ở `page-publishing/accounts.json`; hồ sơ Chrome nằm tại `page-publishing/browser-profiles/<mã-tài-khoản>/chrome` trong thư mục dữ liệu Tèo. Đổi tên tài khoản không đổi hồ sơ. [Hồ sơ cố định của Playwright](https://playwright.dev/python/docs/api/class-browsertype#browser-type-launch-persistent-context) giữ dữ liệu phiên qua lần đóng/mở; đây là hồ sơ riêng trên Chrome đã cài, không phải hồ sơ Chrome cá nhân hoặc hồ sơ Douyin. Không xóa thư mục hồ sơ nếu muốn giữ phiên. Facebook vẫn có thể yêu cầu đăng nhập/xác minh lại.

Danh sách Page có thể đọc bằng Load Page hoặc nhập tay. Tèo chưa tự xác nhận danh tính tài khoản đăng nhập hoặc tổng Page. Cột kết nối ghi **Chưa kết nối API**, **Đã kết nối đọc API** hoặc **Cần kết nối lại** theo dữ liệu kết nối riêng; không coi mở/đóng Chrome là đăng nhập thành công hay đã có quyền đăng. Phần trình duyệt Facebook chạy riêng, có thể mở trong lúc lồng tiếng mà không khóa hàng đợi video. Hai cửa sổ Tèo không được mở đồng thời cùng một hồ sơ Facebook. Đóng Tèo sẽ yêu cầu đóng Chrome của tab này để lưu hồ sơ; Chrome cá nhân không bị đóng.

Nâng cấp Load Page kiểm **73 bài đạt** về nhận diện thẻ, gộp/lưu Page, nhiều tài khoản, lỗi/dừng và khung app. Có kiểm JavaScript trong Chrome mới, chạy ẩn với trang mẫu ngoại tuyến và chặn mọi yêu cầu mạng; luồng browser worker được giả lập, giao diện Tk ẩn. Chưa kiểm chứng lượt đọc trên Facebook thật hoặc quyền Page. Log ở `work/facebook-load-pages-tests.log`. Đóng/mở lại Tèo khi công việc hiện tại nghỉ để nạp tính năng mới; không dừng worker chỉ để cập nhật. Hồ sơ và tài khoản đã lưu vẫn được giữ.

Khung **Lưu Page bằng link** kiểm **64 bài đạt**, gồm lưu/mở lại, chống trùng, nhập riêng từng tài khoản, lỗi giữ nội dung, lưu trong lúc Load Page và mở link bằng cùng browser session. Kiểm Tk ẩn và browser giả lập, không sửa danh sách/hồ sơ thật hoặc truy cập Facebook. Log `work/facebook-page-links-tests.log`. Chưa xác minh mở Page thật trên tài khoản đang đăng nhập.

## Bước tiếp theo: kết nối đăng bài Page

Tính năng dự kiến gồm thêm Page qua đăng nhập/cấp quyền Facebook, nhận video đã hoàn tất từ chiến dịch, chuẩn bị bài viết, đặt lịch từng Page, theo dõi bài đã đăng và nguyên nhân lỗi. Hướng đề xuất dùng API Meta để chạy nền. [SDK chính thức của Meta](https://github.com/facebook/facebook-python-business-sdk/blob/main/facebook_business/adobjects/page.py) có luồng đăng bài chữ, video và Reels; [mẫu đăng Reels của Meta](https://raw.githubusercontent.com/fbsamples/reels_publishing_apis/main/fb_reels_publishing_api_sample/README.md) yêu cầu kiểm tra trạng thái sau gửi, không chỉ nhìn phản hồi success.

Đăng chữ, ảnh và video Page thông thường đã có trong **Quản lý Page / Bài viết** ở phần đầu tài liệu. Cần Meta App và quyền đăng nội dung trên Page; một link Page chưa đủ để kết nối đăng. Kết nối tài khoản thật còn chờ hoàn tất đăng ký/xác minh và cấp quyền. Lịch đăng, Reels riêng, nhận nội dung tự động từ chiến dịch và OAuth một nút chưa được triển khai. Không cần gửi mật khẩu Facebook trong chat.

## Chrome lưu đăng nhập Douyin

Không phải mọi phiên Douyin đều cần đăng nhập để đọc kênh. Bạn có thể chọn Chrome và bấm **Lấy danh sách** trước. Tèo tiếp tục cuộn khi còn tìm thấy video mới, kể cả trang còn lời nhắc đăng nhập; chỉ chờ bạn xử lý đăng nhập khi lời nhắc đi kèm việc không có link mới ít nhất 8 giây. Nút Đăng nhập thông thường trên đầu trang không được coi là bị chặn. Chưa bảo đảm phiên Chrome của Tèo có cùng kết quả với Chrome cá nhân.

Trong tab **Chiến dịch hàng loạt**, bấm **Đăng nhập Chrome**. Tèo chọn Google Chrome, mở cửa sổ để bạn tự đăng nhập Douyin và dùng một hồ sơ cố định cho các lần sau. Không cần đăng nhập tài khoản Google vào Chrome. Khi đăng nhập Douyin xong, bấm **Đã xong / Lưu phiên** trong Tèo hoặc đóng cửa sổ Chrome đó, đợi thông báo đóng xong rồi bấm **Lấy danh sách** với link kênh. Bước đăng nhập chờ tối đa 30 phút; không tự bắt đầu quét hoặc chạy chiến dịch.

Hồ sơ dùng chung giữa bước đăng nhập và đọc kênh là `douyin-channel-browser/chrome` trong thư mục dữ liệu Tèo. [Hồ sơ cố định của Playwright](https://playwright.dev/python/docs/api/class-browsertype#browser-type-launch-persistent-context) giữ dữ liệu phiên qua các lần đóng/mở. Đây là hồ sơ riêng của Tèo trên Chrome đã cài, không phải hồ sơ Chrome cá nhân đang mở. Không xóa thư mục này nếu muốn giữ phiên. Douyin có thể cho phiên hết hạn hoặc yêu cầu xác minh/đăng nhập lại; tool không bảo đảm đăng nhập một lần vĩnh viễn và không coi việc đóng cửa sổ là xác nhận đăng nhập thành công.

Nút đăng nhập tự tắt **Tự lấy khi dán** để bạn chủ động đăng nhập trước, rồi lấy danh sách; có thể bật lại sau. Nếu muốn đọc kênh trên 100 video, tăng **Tối đa** trước khi quét, ví dụ 300 cho kênh hiển thị 258 bài. Danh sách chỉ được báo đủ khi trang trả bằng chứng hết bài. Chrome là lựa chọn mặc định cho cấu hình mới; cấu hình trình duyệt đã lưu được giữ đến khi bạn bấm Đăng nhập Chrome hoặc đổi lựa chọn.

Đã kiểm 72 bài cho luồng mở/đóng, dùng lại đường dẫn hồ sơ, khóa tránh mở đồng thời và giao diện. Trình duyệt được giả lập trong kiểm thử; chưa kiểm chứng đăng nhập Douyin thật hoặc lấy toàn bộ kênh sau đăng nhập. Sau khi cập nhật, đóng/mở lại cửa sổ Tèo khi tiện để nạp nút mới; không cần dừng các worker chỉ để cập nhật hướng dẫn.

## Danh sách chiến dịch và chạy nhiều chiến dịch

Trong bảng video ở tab **Chiến dịch hàng loạt**, cột **Thời gian hoàn thành** nằm ngay cạnh **Tiến độ**. Cột ghi ngày giờ hoàn tất thực tế theo giờ Việt Nam (UTC+7), giữ nguyên khi tiếp tục hoặc mở lại tool. Video chưa xong/lỗi/tạm dừng hiện `—`. Video cũ lấy giờ từ sự kiện hoàn tất đã ghi nếu có; không có dữ liệu thì giữ `—`, không lấy ngày sửa tệp để đoán.

Vùng thông báo dưới trạng thái chiến dịch hiển thị nguyên nhân lỗi hoặc tạm dừng và gợi ý thao tác tiếp theo. Chọn dòng video rồi bấm **Xem chi tiết / nguyên nhân** để đọc đầy đủ lỗi, đường dẫn nhật ký, thời gian hoàn tất và tổng thời gian xử lý các lượt đã ghi nhận. Tổng thời gian xử lý không tính khoảng nghỉ giữa các lượt; nếu một lượt bị tắt đột ngột trước khi ghi dữ liệu, thời gian lượt đó có thể chưa được ghi. Bấm **Dừng** để tạm dừng và giữ các bước đã làm, **Chạy / Tiếp tục** để tiếp tục; video failed cần **Thử lại video lỗi**. Khi chưa xác định được nguyên nhân gián đoạn, thông báo ghi rõ điều đó. Lỗi khởi động được giữ trong vùng riêng thay vì bị cập nhật tiến độ ghi đè. Trong **Danh sách chiến dịch**, chọn dòng cũng xem được nguyên nhân dừng/lỗi ở phần chi tiết.

Chọn một dòng rồi bấm **Sửa chiến dịch** để đổi tên hiển thị và ghi chú. Đổi tên giữ nguyên thư mục, cấu hình giọng/model, thứ tự video, tiến độ và cache; tên thư mục đã tạo không đổi theo tên hiển thị. Các cấu hình lồng tiếng vẫn được chốt khi tạo chiến dịch để tiếp tục nhất quán.

Chọn một hoặc nhiều dòng rồi bấm **Xóa chiến dịch** để bỏ khỏi danh sách. Tool hỏi lại trước khi xóa; video gốc, thành phẩm và các bước đã làm được giữ nguyên. Bật **Hiện đã xóa**, chọn dòng rồi **Khôi phục** nếu muốn đưa lại vào danh sách. Chiến dịch đã xóa không chạy được. Nếu hàng đợi cũ chứa dòng đã xóa, cần khôi phục dòng đó hoặc chọn các chiến dịch còn lại để tạo hàng đợi mới. Đang xử lý thì các thao tác sửa/xóa bị khóa.

Nút **Tạo chiến dịch mới** hiện Đang lưu khi ghi dữ liệu. Những lần bấm lặp với cùng tên, danh sách, cấu hình và thư mục trong vòng 15 giây dùng lại bản vừa tạo, tránh tạo trùng do lag. Sau khoảng này có thể chủ động tạo chiến dịch mới. Hai chiến dịch trùng đã có từ bản cũ vẫn giữ nguyên để bạn chọn xóa bản dư.

Tab **Danh sách chiến dịch** tự hiển thị tất cả chiến dịch đã tạo, gồm tên, ngày tạo, trạng thái, số video hoàn tất/tổng, video lỗi và nguồn kênh. Có tìm kiếm, chọn tất cả, mở chi tiết và hai thư mục của từng chiến dịch. Danh sách tự cập nhật mỗi 2 giây; tạo chiến dịch ở tab **Chiến dịch hàng loạt** là đã lưu, không cần nhập lại sau khi mở app.

Giữ **Ctrl / Shift** để chọn nhiều dòng, rồi bấm **Chạy các chiến dịch đã chọn**. Tool lưu một hàng đợi theo thứ tự các dòng đang hiển thị và chạy **lần lượt**, mỗi chiến dịch giữ nguyên giọng, model, thư mục và cấu hình riêng. **Dừng hàng đợi** dừng cả video hiện tại và các chiến dịch chưa chạy; **Tiếp tục hàng đợi** dùng lại phần đã hoàn tất sau khi kiểm tra dữ liệu. Đóng/mở app chỉ khôi phục danh sách và trạng thái, không tự bắt đầu.

Video lỗi thông thường được giữ trong chiến dịch, tool chuyển sang chiến dịch tiếp theo sau khi xử lý hết danh sách. Lỗi hạn mức, đăng nhập hoặc gián đoạn tạm dừng toàn bộ hàng đợi và hiển thị lý do. Để thử lại video lỗi, **Mở chiến dịch** rồi dùng **Thử lại video lỗi** ở tab chi tiết; tiếp tục hàng đợi không tự gửi lại những video này. Bộ đếm **Hoàn tất** chỉ tính chiến dịch thành công, **Cần kiểm tra** tính chiến dịch còn lỗi.

Danh sách chiến dịch lưu tại `campaigns` trong thư mục dữ liệu Tèo; hàng đợi nhiều chiến dịch lưu riêng tại `campaign-batches`. Những chiến dịch đã lưu trước bản nâng cấp cũng hiện trong danh sách. Mỗi lần chạy mới từ các dòng đã chọn tạo một hàng đợi mới; nút Tiếp tục khôi phục hàng đợi gần nhất. Không chạy đồng thời với Video đơn hoặc một chiến dịch khác.

## Chiến dịch hàng loạt (tab riêng)

Tab **Video đơn** giữ luồng cũ. Tab **Chiến dịch hàng loạt** nhận nhiều link Douyin/TikTok hoặc một link kênh Douyin:

Mặc định bật **Tự nhận dạng link** và **Tự lấy khi dán**: dán link kênh rồi chờ khoảng một giây, tool tự mở phiên đọc nền và điền link từng video vào bảng. Danh sách hiện dần trong lúc quét. Dán nhiều link video sẽ tự lập danh sách và bỏ trùng. Có thể tắt Tự lấy khi dán để thao tác bằng nút Lấy danh sách. Quét chỉ đọc danh sách; vẫn cần Tạo chiến dịch mới rồi Chạy / Tiếp tục để tải và lồng tiếng.

Bảng có riêng **Video / Tiêu đề / Link video / Trạng thái / Tiến độ / Chi tiết**. Mỗi video cập nhật theo sự kiện thật: tải, kiểm nguồn, nhận dạng, dịch, tạo giọng, dựng/kiểm tra, hoàn tất hoặc lỗi. Phần trăm là tiến độ toàn bộ video do worker báo, không phải ước lượng thời gian còn lại. Video lỗi/dừng giữ lý do và phần trăm đã có; video gián đoạn hiện hướng dẫn Tiếp tục. Dùng nút **Sao chép link đã chọn** hoặc **Sao chép tất cả link** để lấy danh sách. Khi tạo chiến dịch, `links.txt` được lưu cạnh dữ liệu chiến dịch; bảng và trạng thái vẫn khôi phục khi mở lại app.

Mục **Trình duyệt đọc kênh** có **Tự chọn (Edge / Chrome)**, **Cốc Cốc (thử nghiệm)**, **Microsoft Edge**, **Google Chrome**. Khi chọn Cốc Cốc, tool bật **Hiện trình duyệt để đăng nhập/xác minh** để bạn nhìn cửa sổ đọc kênh; bạn có thể tắt nếu muốn chạy ẩn. Cả lựa chọn trình duyệt và hiện/ẩn được nhớ ngay khi đổi, không cần quét trước để lưu. Bản cấu hình cũ chọn Cốc Cốc mà chưa lưu hiện/ẩn sẽ hiện cửa sổ ở lần mở Tèo mới. Dán link rồi Lấy danh sách. Nếu trang cho xem bài mà không đăng nhập, tool đọc danh sách công khai đó; nếu trang yêu cầu đăng nhập hoặc dừng tải thêm, tool vẫn báo danh sách chưa đủ. Không bảo đảm đọc hết mọi kênh không đăng nhập.

Muốn mở **Cốc Cốc thường trên máy**, bấm **Mở Cốc Cốc trên máy** trong tab Chiến dịch hàng loạt. Nút dùng Cốc Cốc đã cài với cách chọn hồ sơ thông thường của trình duyệt, không tạo hồ sơ riêng cho Tèo. Có link kênh hợp lệ trong ô nguồn thì gửi link đó để mở; ô trống hoặc đang chứa danh sách link video thì chỉ mở trình duyệt. Nếu bạn dùng nhiều hồ sơ Cốc Cốc, nút chưa chỉ định một hồ sơ cụ thể. Tool báo đã gửi lệnh mở, không coi đó là xác nhận cửa sổ hoặc danh sách đã đọc thành công.

Nút này tắt **Tự lấy khi dán** và nhớ lựa chọn sau khi mở lại Tèo, tránh tự mở phiên đọc kênh riêng trong lúc bạn dùng trình duyệt thường. Bạn có thể chủ động bật lại tùy chọn. **Mở Cốc Cốc trên máy chỉ mở trình duyệt/trang**, chưa tự cuộn hoặc lấy link từ phiên cá nhân. Để đưa danh sách vào Tèo, dùng Nhập HTML kênh… sau khi tự lưu trang. Đang có lượt đọc kênh thì nút mở bị khóa; hoàn tất hoặc Dừng lượt đó trước. Việc mở không khởi chạy chiến dịch hay dịch/TTS.

Cốc Cốc dùng phiên riêng tại `douyin-channel-browser/coccoc` trong dữ liệu Tèo. Không nối vào tab Cốc Cốc cá nhân đang mở hoặc sao chép cookie/lịch sử. Các lựa chọn trình duyệt cụ thể đều có profile riêng; chế độ Tự chọn giữ profile Edge/Chrome cũ. Chọn Cốc Cốc mà không tìm thấy hoặc mở lỗi sẽ báo rõ, không âm thầm dùng Chrome. Lựa chọn được lưu khi quét/tạo chiến dịch; browser yêu cầu và browser đã chọn được lưu vào kết quả quét và manifest chiến dịch mới.

Nhật ký Tèo ngày 30/09/2026 lúc 11:28 ghi nhận **Cốc Cốc riêng đã đọc 18 video, rồi Douyin yêu cầu đăng nhập để xem tiếp**. Người dùng thấy phiên Cốc Cốc thường dùng cuộn được thêm video; chưa xác định chính xác dữ liệu phiên nào tạo ra khác biệt. Hai cửa sổ dùng hồ sơ riêng nên không bảo đảm thấy cùng danh sách. Codex chưa đọc trực tiếp được cửa sổ Cốc Cốc do giới hạn công cụ điều khiển; Cốc Cốc giữ nhãn thử nghiệm. [Tài liệu Playwright](https://playwright.dev/python/docs/api/class-browsertype#browser-type-launch-persistent-context) hỗ trợ chỉ định executable riêng nhưng không đảm bảo mọi phiên bản trình duyệt tùy chỉnh đều tương thích.

Đã sửa lỗi đóng cửa sổ giữa lúc đọc/chờ đăng nhập: các link đã đọc được vẫn đi vào bảng và có thể dùng để tạo chiến dịch, kèm thông báo **Danh sách chưa đủ kênh**. Nếu đóng trước khi có video, tool báo rõ chưa có danh sách. Lỗi khác hoặc bấm Dừng không được biến thành báo thành công; tool không tự mở lại hoặc gửi lại lượt quét. Bản sửa được kiểm bằng trình duyệt giả lập; chưa thử lại trên trang thật.

Nếu Cốc Cốc riêng của Tèo không mở được, có cách nhập từ trình duyệt bạn đang dùng: tự mở kênh, chọn **Tác phẩm**, cuộn để tải thêm video, bấm **Ctrl+S** lưu trang HTML. Trong tab Chiến dịch hàng loạt bấm **Nhập HTML kênh…**, chọn tệp vừa lưu, kiểm tra danh sách rồi Tạo chiến dịch mới. Nếu trang không lưu địa chỉ kênh, dán link đầy đủ `/user/…` trước khi nhập. Tool chỉ đọc link trong mục bài đăng của trang đã lưu, bỏ link gợi ý; số lượng phụ thuộc phần trang đã tải. Luôn báo danh sách chưa đủ kênh, không coi đây là cách lấy chắc chắn mọi video. Tệp HTML tối đa 32 MB. Không cần đóng trình duyệt cá nhân; tool không kết nối vào nó.

Mỗi lần đọc kênh có báo cáo `channel-scan-last.json` trong thư mục dữ liệu Tèo, ghi ngay trước bước mở và cập nhật trong lúc đọc, rồi lưu kết quả hoặc nguyên nhân lỗi. Bấm **Xem lỗi mở…** để xem bước cuối và nguyên nhân; nút này không gửi lại yêu cầu. Mở một trình duyệt được chờ tối đa 30 giây (Tự chọn có thể thử lần lượt hai trình duyệt). Nếu hai cửa sổ Tèo cùng đọc bằng một phiên, cửa sổ thứ hai báo đợi hoặc dừng lượt đang đọc; không tự đóng trình duyệt hay xóa dữ liệu. Dừng trong lúc đang mở sẽ đóng cửa sổ của lượt đó khi mở xong, trước khi vào kênh.

Reader cũng đã sửa ba lỗi: phản hồi thiếu dấu `/` cuối, bài có dữ liệu tác giả lỗi làm mất các bài sau, và bài ảnh bị thêm lại từ giao diện. Nhật ký và kiểm thử giả lập không được coi là bằng chứng đã lấy toàn bộ kênh thật.

1. Ở tab Video đơn, chọn giọng, model, tốc độ và kiểu Sub muốn dùng. Sang tab Chiến dịch, bấm **Lấy cấu hình từ tab Video đơn**. Cấu hình chỉ áp dụng khi tạo chiến dịch mới; chiến dịch đã lưu giữ nguyên lựa chọn ban đầu. SRT riêng của một video không được áp cho cả danh sách.
2. Đặt tên chiến dịch, chọn hai thư mục video gốc/video lồng tiếng. Giữ **Tự nhận dạng link**, dán link kênh hoặc nhiều link video. Cũng có thể chọn chế độ cụ thể bằng danh sách.
3. Chờ tự lấy danh sách hoặc bấm **Lấy danh sách**, xem các dòng video và link riêng. Kênh mặc định lấy tối đa 100 video, điều chỉnh từ 1 đến 1.000. Danh sách theo thứ tự trang trả về (có thể có video ghim), không hứa thứ tự ngày đăng. Có tùy chọn đảo danh sách trước khi tạo.
4. Nếu Douyin yêu cầu đăng nhập, bật **Hiện trình duyệt để đăng nhập/xác minh**, rồi Lấy danh sách lại. Bạn tự đăng nhập trong cửa sổ riêng; tool chờ tối đa 3 phút. Không cần gửi cookie/mật khẩu. Danh sách bị giới hạn luôn được báo là chưa đủ kênh; tool chỉ khẳng định hết danh sách công khai khi trang có bằng chứng hết trang.
5. Bấm **Tạo chiến dịch mới** để chốt danh sách/cấu hình, sau đó **Chạy / Tiếp tục**. Bước tạo không gửi lượt dịch hay tạo giọng. Dùng danh sách **Chiến dịch đã lưu** để mở lại tiến độ sau khi tắt app.

Mỗi video chạy tuần tự qua pipeline hiện tại: tải → nhận dạng → dịch → tạo giọng → dựng và kiểm tra. Bảng hiển thị số xong/tổng, số lỗi/chờ và bước hiện tại của từng video. Video lỗi thông thường được ghi lại rồi chuyển sang video sau; hết hạn mức hoặc cần đăng nhập sẽ tạm dừng để tránh thử cả kênh. **Thử lại video lỗi** chỉ chạy lại những video chưa thành công, không dựng lại video đã xong. **Dừng** giữ cache và danh sách; **Chạy / Tiếp tục** giữ số thứ tự cũ. Đóng app không dừng supervisor đang chạy; dùng Dừng nếu muốn tạm dừng.

Thư mục mỗi chiến dịch mang tên, ngày tạo và mã riêng. Video gốc đặt tên `Video 1_30-09-2026.mp4`, `Video 2_30-09-2026.mp4`…; thành phẩm ở thư mục tương ứng `Video 1_30-09-2026/`, có MP4 + một SRT + báo cáo. Ngày trong tên là ngày tạo chiến dịch để tên giữ nguyên khi tiếp tục qua ngày khác. Không ghi đè nguồn/thành phẩm đã tồn tại. Cùng ổ đĩa dùng liên kết cứng cho bản gốc đã kiểm tra để giảm bản sao; không sửa nội dung video.

Một ứng dụng chỉ xử lý một luồng video/chiến dịch tại một thời điểm. Quét và xem danh sách chưa tốn lượt dịch Codex. Khi bấm chạy, mỗi video dịch sử dụng hạn mức theo model đã chọn.

Kiểm chứng: đã đọc 19 video công khai từ kênh người dùng gửi; Douyin chặn đọc thêm bằng thông báo đăng nhập. Chưa kiểm chứng quét toàn bộ kênh sau đăng nhập. Đã chạy thật hai worker lồng tiếng và ghi Sub bằng nguồn/SRT cục bộ, tiếp tục lại giữ nguyên hash và thời gian sửa các thành phẩm; không gọi Codex hoặc tải video trong lượt kiểm tra này. Hàng đợi, lỗi, dừng, khóa cấu hình và giao diện có bài kiểm thử riêng.

Bản tự lấy khi dán đã kiểm qua vòng sự kiện GUI thật: link kênh người dùng tự khởi động quét, bảng hiện 19 link khi vẫn đang đọc kênh. Lần đọc mới kết thúc vì trang không tải thêm bài (`no_new_posts`), vẫn ghi rõ chưa đủ kênh. **287 kiểm thử đạt**; báo cáo `work/campaign-auto-tracking-verification.json`. Không khẳng định mọi kênh quét được toàn bộ hoặc kiểm chứng đăng nhập thành công.

Thư mục riêng trên máy hiện tại: `C:\Users\chungpc\Desktop\Teo Dub Studio`. Tool mới đã được chuyển ra khỏi dự án Shopee Affiliate; tool cũ giữ nguyên tại vị trí cũ.

Ứng dụng Windows tải video Douyin/TikTok hoặc nhận video trên máy rồi lồng tiếng Việt. Mặc định nhận dạng và giọng đọc chạy trên máy; chỉ bản lời cần dịch được gửi qua Codex đã đăng nhập. Dịch dùng hạn mức Codex, không cần API key. Có lựa chọn CapCut trực tuyến để kiểm tra riêng dịch vụ giọng của CapCut; phần này cần kết nối và tài khoản phù hợp, chưa được xem là đã hoạt động chỉ vì có tên giọng trong danh sách.

## Sử dụng

1. Mở **Teo Dub Studio.cmd** trên Desktop, hoặc **Mo Teo Dub Studio.cmd** trong thư mục dự án.
   Nếu mở trực tiếp `launch.py`, bản mới tự chọn môi trường riêng đã cài trong thư mục tool. Sau khi cập nhật, đóng cửa sổ cũ rồi mở lại. Nếu báo thiếu thành phần đọc kênh, dùng một trong hai file mở tool trên để tránh chạy nhầm Python hệ thống.
2. Dán link / đoạn chia sẻ hoặc chọn video có sẵn. Chọn thư mục video gốc và video lồng tiếng.
3. Chọn phiên bản giọng và giọng đọc. Bản **chạy trên máy** có bốn giọng có sẵn, **Cô Gái Hoạt Ngôn (theo mẫu)**, **Thanh Niên Tự Tin (theo mẫu)** và **Giọng từ mẫu** để chọn tệp riêng. Chọn **Kiểu đọc** và **Tốc độ giọng**, rồi bấm **Nghe thử giọng** để nghe đoạn ngắn với đúng lựa chọn đó; có **Nghe lại** và **Dừng nghe**. Không cần chọn video để thử giọng. Chọn tốc độ hình 0,9× hoặc 1,0× riêng với nhịp đọc.
4. Nếu đã có SRT Việt, chọn tệp rồi bấm **Lồng tiếng và xuất video** để dùng nguyên lời và bỏ qua dịch. Muốn dịch lời mới cho video đang chọn, bấm **Tự động toàn bộ**; SRT có sẵn được bỏ qua. Muốn chữ luôn hiện trên video, bật **Ghi Sub Việt trực tiếp vào hình**, rồi chọn **Trắng/Đen/Vàng** và **Chữ đậm/Chữ nhạt**. Có thể làm việc khác trong lúc xử lý nền.
5. Bấm **Mở kết quả** để nhận một **MP4** và một **SRT Việt**. Khi không bật Ghi Sub, phụ đề nhúng trong MP4 có thể bật/tắt trên trình phát hỗ trợ. Khi bật, Sub được ghi thẳng vào hình và MP4 không có thêm lớp phụ đề gây trùng. Chữ dính sẵn trong hình nguồn được giữ nguyên; tiếng gốc còn 10%, các đoạn lời đọc không đè nhau. Nghe duyệt bản dịch và cách đọc trước khi sử dụng.

**Dừng** giữ những phần đã hoàn tất. Nếu bước đang chạy không phản hồi, ứng dụng kết thúc worker của phiên sau khoảng 8 giây. **Tiếp tục phiên trước** dùng lại đúng cấu hình cũ và kiểm tra phần đã có; thay đổi trong biểu mẫu không thay đổi phiên được tiếp tục. Mở lại ứng dụng không tự chạy hoặc tự phát giọng thử.

Nếu lượt dịch Codex bị gián đoạn, **Tiếp tục** có thể gửi lại phần chưa hoàn tất một lần và dùng thêm hạn mức Codex. Các phần dịch đã hoàn tất được kiểm tra và dùng lại; ứng dụng không tự lặp yêu cầu dịch khi lỗi.

Tiến độ cập nhật trên một dòng; nhật ký chỉ giữ các bước chính và lỗi. Dữ liệu phiên nằm tại `%LOCALAPPDATA%\TeoDubStudio`; mô hình được tải một lần vào thư mục `models` của dự án. Lần tải đầu có thể lâu.

## Tăng tốc giọng lồng tiếng

Mục **Tốc độ giọng** có 11 mức, từ **0,80× đến 1,30×**, mỗi nấc **0,05×**: 0,80 · 0,85 · 0,90 · 0,95 · 1,00 · 1,05 · 1,10 · 1,15 · 1,20 · 1,25 · 1,30. Dưới1× đọc chậm hơn; trên1× đọc nhanh hơn. Mức này áp dụng cho cả giọng trên máy và CapCut trực tuyến, cho bản nghe thử lẫn video đầy đủ. Âm thanh được thay thời lượng bằng xử lý giữ cao độ. Mặc định vẫn1,00×. Với chiến dịch mới, bấm **Lấy cấu hình từ tab Video đơn** sau khi chọn tốc độ; chiến dịch cũ không tự đổi. Mở lại Tèo khi đang nghỉ để thấy danh sách mới.

Mục **Ghi Sub Việt trực tiếp vào hình** là tùy chọn cho từng video. Ba màu chữ đều có viền tương phản để dễ đọc: chữ Trắng/Vàng dùng viền đen, chữ Đen dùng viền trắng. **Chữ đậm** và **Chữ nhạt** thay đổi trọng lượng thật khi dựng. Lựa chọn được nhớ cho video mới và được giữ nguyên khi tiếp tục phiên dở; phiên cũ thiếu tùy chọn vẫn dùng phụ đề bật/tắt như trước. Tool luôn xuất thêm đúng một SRT Việt để chỉnh sửa hoặc đăng lại.

Tốc độ giọng là hệ số nhân thêm sau **Kiểu đọc**. Ví dụ, Quảng cáo năng động có nhịp cơ sở 1,06×; chọn Tốc độ giọng 1,10× cho nhịp cuối khoảng 1,166×. Khoảng nghỉ giữa các cụm vẫn theo Kiểu đọc, các đoạn vẫn được xếp nối tiếp và không chồng nhau. Tốc độ hình 0,9×/1,0× là lựa chọn riêng.

Phiên mới mặc định 1,00×. Mỗi phiên lưu tốc độ giọng vào cấu hình và cache riêng; **Tiếp tục phiên trước** giữ nguyên tốc độ đã chọn. Đổi tốc độ sẽ yêu cầu bản nghe thử hoặc lời đọc mới, không dùng nhầm WAV của tốc độ khác. Phiên cũ chưa có trường này tiếp tục ở 1,00×.

Đợt mở rộng05/10/2026 đã kiểm tra âm thanh tổng hợp bằng FFmpeg thật từ0,736× (Kể chuyện0,92× × tốc độ0,80×) đến1,378× (Quảng cáo1,06× × tốc độ1,30×), cùng các nấc trung gian: thời lượng thay đổi đúng, WAV48kHz và cao độ kiểm tra giữ nguyên. Dải lựa chọn tốc độ là0,80–1,30×; nhịp cuối còn nhân với Kiểu đọc như trước.

## Chọn mô hình cho cả ba bước AI

Bấm **Mô hình AI…**, chọn riêng từng bước rồi **Lưu lựa chọn**. Các lựa chọn được nhớ khi mở lại app và áp dụng cho phiên mới. **Tiếp tục phiên trước** luôn dùng mô hình đã lưu của phiên đó; không tự đổi model hoặc trộn kết quả của model khác.

| Bước | Lựa chọn đã tích hợp | Cách chọn |
|---|---|---|
| Nhận dạng lời Trung | Whisper Small, Medium, Large v3, Tự động | Tự động chọn Small/Medium/Large v3 theo bộ nhớ và chế độ GPU hỗ trợ. GPU không đủ cho model đã chốt thì dùng CPU, báo rõ và giữ model; Tự động mới dùng Medium trên CPU khi cần. |
| Dịch Trung → Việt | GPT-6.1 Sol; GPT-6 Luna/Sol/Astra; GPT-5.6 Luna/Terra/Sol; Mặc định Codex | Gợi ý sát nghĩa, văn nói Việt: GPT-6.1 Sol + Vừa. Luna ưu tiên hiệu quả, Astra cho nội dung khó. Giữ các lựa chọn 5.6 và mức suy luận Thấp/Vừa/Cao riêng. Khả năng dùng phụ thuộc tài khoản Codex. |
| Tạo giọng trên máy | VieNeu v3 Turbo, VieNeu v3 Nano | Turbo xuất 48 kHz; Nano nhẹ hơn, xuất 24 kHz rồi chuyển đúng sang 48 kHz để ghép video. Hai model có bộ giọng riêng. |

Phiên mới mặc định **Tự động / GPT-6 Luna / suy luận Thấp / VieNeu Turbo**. Giảm mức suy luận có thể giảm phần suy luận, **không làm ít đi số từ nguồn cần gửi dịch**; không có cam kết tiết kiệm bao nhiêu phần trăm hạn mức. ASR và TTS cục bộ dùng CPU/GPU/RAM, không dùng token Codex. Lần đầu chọn model có thể phải tải thêm vài GB; cần đủ dung lượng và mạng. Không tự chuyển sang API trả phí hoặc model tốn hơn khi gặp lỗi.

Nếu ưu tiên hạn mức, chọn **GPT-5.6 Luna + suy luận Thấp**. Theo tài liệu OpenAI, GPT-5.6 Luna dành cho khối lượng lớn cần hiệu quả; Terra cân bằng năng lực và chi phí; Sol là model mạnh nhất của dòng 5.6. Đây là định hướng lựa chọn, không phải cam kết số token hoặc hạn mức tài khoản. Ba model 5.6 có trong catalog của Codex CLI trên máy này; nếu tài khoản khác không có quyền dùng, tool dừng và báo lỗi thay vì tự chuyển model.

Turbo có Mai Anh, Trúc Ly, Thùy Dung, Ngọc Huyền. Nano có Mai Anh, Trúc Ly, Minh Quân, Mạnh Dũng. Cả hai vẫn có **Cô Gái Hoạt Ngôn (theo mẫu)**, **Thanh Niên Tự Tin (theo mẫu)** và **Giọng từ mẫu**. Nếu đổi model mà giọng cũ không được hỗ trợ, app báo rõ giọng mới đang chọn; hãy chọn lại hoặc nghe thử. Mục **Giọng đọc** và **Kiểu đọc** vẫn độc lập với mô hình. Hai tên theo mẫu là giọng được tái tạo từ mẫu đã có, không phải model CapCut gốc.

Đã thử thực tế cả ba model Whisper và cả ba lựa chọn dịch qua CLI; đã tạo giọng Nano với giọng có sẵn và mẫu nam/nữ. Luồng đầy đủ Small → Luna (Thấp) → Nano xuất clip thử 10,23 giây, đủ ba đoạn giọng và một SRT. Đây là kiểm chứng tích hợp, không bảo đảm lời nào cũng đúng: clip Small có lời nhận dạng sai đã được đánh dấu cần nghe duyệt. Chọn Large v3/Tự động cho nguồn khó và nghe kiểm tra trước khi dùng.

Tên gửi đến Codex được lưu ở `requested_model`; `observed_model` chỉ ghi khi CLI thật sự trả thông tin đó. Các lượt thử hiện tại được CLI chấp nhận nhưng không trả model thực tế trong sự kiện (`observed_model=null`); không lấy câu tự khai của model làm bằng chứng. Lựa chọn **Mặc định Codex** giữ hành vi tương thích cũ, không phải một model được ghim tên.

Nguồn chính: [faster-whisper](https://github.com/SYSTRAN/faster-whisper), [VieNeu-TTS](https://github.com/pnnbao97/VieNeu-TTS), [hướng dẫn GPT-5.6](https://developers.openai.com/api/docs/guides/latest-model?model=gpt-5.6), [GPT-6 Luna](https://developers.openai.com/api/docs/models/gpt-6-luna), [cấu hình Codex](https://learn.chatgpt.com/docs/config-file/config-basic). Hồ sơ kiểm tra: `work/model-selection-verification.json`.

## Hai phiên bản giọng

**Chạy trên máy:** miễn phí, không dùng CapCut để tạo lời mới. Hai giọng có hậu tố **(theo mẫu)** dùng đoạn lời sạch đã tạo trong các phiên CapCut trước của người dùng; VieNeu tạo lời mới dựa trên mẫu đó. Đây là giọng gần giống, không phải mô hình giọng CapCut gốc. Tệp mẫu và nguồn gốc lưu riêng tại `assets/voice-references/`, không sửa tệp hay dự án của tool cũ. Mỗi phiên giữ bản mẫu riêng và kiểm tra dấu vân tay để tiếp tục đúng giọng.

**Giọng từ mẫu:** chọn WAV, MP3, M4A hoặc video có một luồng tiếng nói. Nên dùng 3–8 giây lời rõ, một người nói, ít nhạc nền; tool chỉ lấy tối đa 8 giây đầu. Không cần nhập lời của mẫu. Mô hình bổ sung được tải lần đầu dùng giọng mẫu. Nghe thử để quyết định độ giống và cách phát âm có phù hợp không.

**CapCut trực tuyến (thử nghiệm):** tách riêng với bản theo mẫu, không điều khiển timeline CapCut PC. Trang chính thức hiện yêu cầu đăng nhập và có thể yêu cầu tín dụng. Tool không dùng danh tính thiết bị có sẵn từ thư viện bên thứ ba, không tự mua/gật đầu trừ tín dụng, và không âm thầm đổi sang giọng khác khi CapCut chưa dùng được. Kiểm tra khả năng dùng dịch vụ trước khi tải/dịch video. Nút mở CapCut cho phép người dùng tự đăng nhập trong phiên trình duyệt riêng; đăng nhập chưa bảo đảm dịch vụ tạo giọng miễn phí. Trạng thái và giới hạn thực tế xem `HANDOFF.md`.

## Chọn cách đọc cho từng video

| Kiểu đọc | Nhịp lời so với giọng gốc | Khoảng nghỉ tối thiểu giữa các cụm |
|---|---|---|
| Trò chuyện tự nhiên | 0,98× | 180 ms |
| Kể chuyện chậm rãi | 0,92× | 300 ms |
| Quảng cáo năng động | 1,06× | 120 ms |
| Theo bản cũ | 1,00× | 100 ms |

Phiên mới mặc định **Trò chuyện tự nhiên**. Ba cách đọc mới ghép những mảnh phụ đề còn dang dở để giọng đọc có đủ ngữ cảnh: tối đa ba mảnh, 220 ký tự, 12 giây thời gian gốc và khoảng cách giữa hai mảnh không quá 600 ms. Tool giữ toàn bộ từ theo thứ tự, không thêm tiếng cười hoặc thở dài; thành phẩm vẫn chỉ có **một SRT Việt**. Những câu đã có dấu kết thúc hoặc có dấu hiệu đổi người nói được giữ riêng. Câu gốc dài hơn giới hạn vẫn được giữ nguyên, không bị cắt bỏ nội dung.

Nhịp đọc được chỉnh bằng xử lý âm thanh giữ cao độ. Đây là các lựa chọn ngắt nghỉ và tốc độ, không phải nút diễn xuất cảm xúc. Sắc thái còn phụ thuộc giọng hoặc mẫu đã chọn; nên chọn **Kiểu đọc → Nghe thử giọng** trước khi bắt đầu video. Đoạn thử mới chỉ vài giây, có câu hỏi và dấu ngắt để dễ so sánh.

**Tiếp tục phiên trước** luôn dùng lựa chọn đã lưu. Phiên cũ chưa có thông tin cách đọc sẽ giữ **Theo bản cũ**, không tự đổi giọng hoặc tạo lại toàn bộ lời theo chế độ mới. Muốn đổi cách đọc cho video đó, bắt đầu một phiên mới.

Bốn mẫu so sánh độc lập, thông số và giới hạn đánh giá nằm trong `work/natural-voice-research/` (`01-giu-nhu-cu.wav`, `02-tro-chuyen.wav`, `03-ke-chuyen.wav`, `04-quang-cao.wav`, `research.md`). Nhận dạng lại bằng mô hình trên máy đọc đủ lời ở cả bốn mẫu; điều đó không chứng minh mẫu nào nghe hay hoặc có cảm xúc hơn. Bản tích hợp đã đạt232kiểm thử, thử nút nghe cả ba kiểu và xuất một video thật60,1giây, giữ đủ nội dung21dòng thành19cụm không chồng tiếng. Kết quả và các từ còn cần nghe duyệt ghi tại `work/natural-voice-verification.json`.

Theo [tài liệu VieNeu-TTS chính thức](https://github.com/pnnbao97/VieNeu-TTS), v3 Turbo lấy cách đọc từ giọng mẫu; tham số `style` bị bỏ qua. Tool không dùng tham số này để hứa hẹn hiệu ứng cảm xúc.

## Cài đặt từ mã nguồn trên máy mới

Bản nguồn hiện dùng Python 3.13 64-bit. Cần Microsoft Edge hoặc Chrome để tải link Douyin; nhận dạng/giọng đọc cần tải mô hình lần đầu. Máy cần cài Codex CLI và đăng nhập tài khoản trước khi dịch.

Mở PowerShell tại thư mục dự án:

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Nếu dùng GPU NVIDIA phù hợp, cài thêm thư viện GPU:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-gpu.txt
```

Sau đó mở **Mo Teo Dub Studio.cmd**. Nếu báo thiếu Codex hoặc chưa đăng nhập, hoàn tất cài đặt/đăng nhập Codex CLI rồi bấm **Tiếp tục phiên trước**. Nút **Hướng dẫn** nhắc lại những yêu cầu này.

Đây là bản chạy từ mã nguồn với `.venv` riêng. `scripts/build_windows.py` là công cụ đóng gói khi cần, chưa phải một bộ cài đã được kiểm chứng cho mọi máy.


### Sửa luồng video Facebook ngày 11/10/2026

Tèo nhận diện cả khung Reel dùng canvas và trình phát nằm trong shadow DOM. Tèo giữ thông tin tệp đã chọn khi Facebook làm mới hộp tải, tự đi tiếp qua trình chỉnh Reel và bỏ qua gợi ý thêm nút gọi điện. Ảnh xem trước thay đổi không còn bị coi là thay video; đường dẫn blob đổi khi chuyển bước phải có cùng thời lượng và kích thước hình đã đọc. Sau khi đăng, Tèo đối chiếu thông báo xử lý mới với đúng Page và toàn bộ caption trước khi lưu liên kết thành công. Một lượt không rõ kết quả sẽ không tự đăng lại.

Trong **Quản lý Page → Lịch đăng → Thêm bài đăng…**, chọn bài chữ hoặc video thành phẩm từ chiến dịch, nhập nội dung/caption/hashtag và giờ Việt Nam (UTC+7). **Lưu lịch** thêm vào danh sách để bật sau; **Lưu và bật lịch** cho phép Tèo tự đăng vào giờ đã chọn. Giữ Tèo mở và máy không ngủ. Video đã có lịch trên Page không được thêm trùng; caption của bài mới không đổi mẫu caption cố định của Page.
