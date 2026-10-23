# Hướng dẫn sử dụng Local OmniVoice TTS API

## Luồng hoạt động

Client **không cần gọi** `/api/characters` để lấy hoặc chọn nhân vật.

Client tự lấy mã nhân vật từ game, ví dụ `thanh_hu_dao_truong`, rồi gửi thẳng mã đó cùng nội dung thoại tới `/api/tts`:

```text
Game lấy được mã nhân vật
        ↓
character = "thanh_hu_dao_truong"
        ↓
POST /api/tts
        ↓
Server tự tìm giọng trong characters.json
        ↓
Response WAV
```

Server tự tìm `ref_audio`, `ref_text`, ngôn ngữ và tham số sinh giọng. Client không gửi đường dẫn audio hoặc `ref_text`.

Để thử API bằng giao diện trình duyệt, mở `http://127.0.0.1:8011/`. UI này có thể tải danh sách mã gợi ý, nhập mã tùy ý, tạo, nghe và tải WAV. Đây là công cụ test; client game vẫn chỉ cần gọi `POST /api/tts`.

## 1. Khởi động server

Mở PowerShell tại thư mục `tts`:

```powershell
.\.venv\Scripts\Activate.ps1
python -m uvicorn app:app --host 127.0.0.1 --port 8011
```

Giữ cửa sổ này mở trong khi chơi game. API hoạt động cục bộ tại:

```text
http://127.0.0.1:8011
```

Lần chạy đầu có thể mất vài phút để tải và khởi tạo model.

## Bật/tắt cache WAV

Cache đang bật mặc định. Cách thuận tiện là sao chép `.env.example` thành `.env` ngay cạnh `app.py`, rồi sửa:

```dotenv
TTS_CACHE_ENABLED=false
```

File `.env` được nạp tự động theo vị trí `config.py`, nên không phụ thuộc thư mục terminal. Biến môi trường của hệ điều hành nếu đã đặt sẽ được ưu tiên hơn `.env`. Cũng có thể cấu hình trực tiếp trong PowerShell trước khi khởi động:

```powershell
$env:TTS_CACHE_ENABLED = "false"  # Tắt cache; mỗi request sẽ tổng hợp lại
python -m uvicorn app:app --host 127.0.0.1 --port 8011
```

Muốn bật lại, đặt `TTS_CACHE_ENABLED=true` trong `.env` hoặc dùng `$env:TTS_CACHE_ENABLED = "true"`. Chấp nhận `true/false`, `1/0`, `yes/no`, `on/off`. `/api/health` trả thêm trường `cache_enabled` để xác nhận cấu hình đang áp dụng. Khi tắt, WAV vẫn được tạo và trả về request hiện tại nhưng không được tái sử dụng ở request sau. Khởi động lại server sau khi đổi cấu hình.

## 2. API client cần dùng

```http
POST /api/tts
Content-Type: application/json; charset=utf-8
```

Request JSON:

```json
{
  "text": "Giác Viễn sư phụ khi lâm chung mới nói với ta.",
  "character": "thanh_hu_dao_truong"
}
```

Các trường bắt buộc:

- `text`: nội dung cần đọc.
- `character`: mã nhân vật do game/client cung cấp.

Response thành công:

- HTTP `200 OK`.
- `Content-Type: audio/wav`.
- Body là dữ liệu WAV nhị phân, không phải JSON.

Client đọc toàn bộ body rồi lưu thành WAV hoặc chuyển trực tiếp cho hệ thống phát âm thanh.

### PowerShell

```powershell
$body = @{
    text = "Giác Viễn sư phụ khi lâm chung mới nói với ta."
    character = "thanh_hu_dao_truong"
} | ConvertTo-Json

Invoke-WebRequest `
    -Method Post `
    -Uri http://127.0.0.1:8011/api/tts `
    -ContentType "application/json; charset=utf-8" `
    -Body ([Text.Encoding]::UTF8.GetBytes($body)) `
    -OutFile ".\output\test.wav"
```

### cURL

```powershell
curl.exe -X POST "http://127.0.0.1:8011/api/tts" `
  -H "Content-Type: application/json; charset=utf-8" `
  --data-raw '{"text":"Giác Viễn sư phụ khi lâm chung mới nói với ta.","character":"thanh_hu_dao_truong"}' `
  --output ".\output\test.wav"
```

### Python

```python
from pathlib import Path

import httpx

response = httpx.post(
    "http://127.0.0.1:8011/api/tts",
    json={
        "text": "Giác Viễn sư phụ khi lâm chung mới nói với ta.",
        "character": "thanh_hu_dao_truong",
    },
    timeout=180,
)
response.raise_for_status()
Path("output/test.wav").write_bytes(response.content)
```

## 3. Cách server chọn giọng

Server tìm giá trị `character` trong `characters.json`:

```json
{
  "characters": {
    "thanh_hu_dao_truong": {
      "name": "Thanh Hư Đạo Trưởng",
      "ref_audio": "audio/thanh_hu_dao_truong.wav",
      "ref_text": "Bản chép lời chính xác của file audio tham chiếu.",
      "language": "Vietnamese",
      "enabled": true,
      "speed": 1.0,
      "parameters": {}
    }
  }
}
```

Với `character: "thanh_hu_dao_truong"`, server tự dùng `audio/thanh_hu_dao_truong.wav` và `ref_text` tương ứng.

`speed` mặc định `1.0`, lớn hơn `1.0` đọc nhanh hơn và nhỏ hơn `1.0` đọc chậm hơn. Đặt trường này trên char chính; request bằng alias tự kế thừa cấu hình speed của char sở hữu. Tham khảo [CHARACTER_SPEED.md](CHARACTER_SPEED.md) để biết chi tiết.

## 4. Nhiều mã game dùng chung một giọng

Khai báo các mã phụ trong `aliases`:

```json
{
  "characters": {
    "vu_van_dat": {
      "name": "Vũ Văn Dật",
      "aliases": ["npc_001", "story_vu_van_dat"],
      "ref_audio": "audio/vu_van_dat.wav",
      "ref_text": "Bản chép lời chính xác của file audio tham chiếu.",
      "language": "Vietnamese",
      "enabled": true,
      "parameters": {}
    }
  }
}
```

Các mã `vu_van_dat`, `npc_001` và `story_vu_van_dat` sẽ dùng chung giọng và cache. Client vẫn chỉ gửi mã nhận được từ game:

```json
{
  "text": "Nội dung cần đọc.",
  "character": "npc_001"
}
```

Alias không được trùng mã chính hoặc alias của nhân vật khác.

### Đồng bộ alias hàng loạt từ `char.csv`

CSV dùng ba cột `name`, `snake_key`, `voice`. Để kiểm tra trước mà chưa sửa JSON:

```powershell
python .\sync_char_aliases.py --dry-run
```

Để cập nhật `characters.json`:

```powershell
python .\sync_char_aliases.py
```

Script đưa từng `snake_key` vào `aliases` của khóa `voice`. Dòng có `voice=default` bị bỏ qua; alias thủ công không có trong CSV vẫn được giữ.

## 5. Giọng mặc định khi chưa có nhân vật

Client có thể gửi bất kỳ mã nhân vật nào lấy được từ game. Nếu mã đó chưa có trong `characters.json`, hoặc nhân vật đang có `enabled: false`, server tự dùng cấu hình `default` và vẫn trả WAV:

```json
{
  "text": "Nội dung cần đọc.",
  "character": "nhan_vat_chua_cau_hinh"
}
```

Client không cần bắt lỗi `404` hoặc tự gửi lại request bằng mã khác. Cấu hình `default` bắt buộc phải tồn tại, được bật và có audio hợp lệ:

```json
"default": {
  "name": "Giọng mặc định",
  "ref_audio": "audio/default.wav",
  "ref_text": "Bản chép lời chính xác của audio mặc định.",
  "language": "Vietnamese",
  "enabled": true,
  "parameters": {}
}
```

## 6. Kiểm tra server — tùy chọn

Client không bắt buộc gọi endpoint này. Có thể dùng khi khởi động mod hoặc debug:

```http
GET /api/health
```

```powershell
Invoke-RestMethod http://127.0.0.1:8011/api/health
```

Response mẫu:

```json
{
  "status": "ok",
  "model_loaded": true,
  "device": "cuda:0"
}
```

Chỉ gửi request TTS khi `model_loaded` là `true`. Nếu `device` là `cpu`, API vẫn hoạt động nhưng chậm hơn.

## 7. Nạp lại cấu hình — không dành cho client game

Endpoint này chỉ dùng sau khi người quản trị sửa `characters.json` hoặc thêm audio mới:

```http
POST /api/characters/reload
```

```powershell
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8011/api/characters/reload
```

Model không bị tải lại. Nếu cấu hình mới lỗi, server trả `422` và giữ cấu hình hợp lệ trước đó.

Client game không cần gọi `/api/characters` hoặc `/api/characters/reload` trong luồng phát thoại.

## 8. Mã lỗi

| HTTP | Ý nghĩa |
|---:|---|
| `200` | Thành công; body chứa WAV |
| `400` | Thiếu `text`, thiếu `character`, text rỗng hoặc JSON không hợp lệ |
| `422` | Cấu hình reload hoặc audio tham chiếu không hợp lệ |
| `503` | Model chưa sẵn sàng, hàng đợi bận hoặc request quá timeout |
| `500` | Model gặp lỗi tổng hợp; xem log server |

Khi có lỗi, response là JSON thay vì WAV:

```json
{
  "detail": "Nội dung mô tả lỗi"
}
```

Mã nhân vật không tồn tại hoặc bị vô hiệu hóa không trả `404/422`; server dùng giọng `default`.

## 9. Tương thích client cũ

Client mới nên dùng `character`. Server vẫn chấp nhận tên trường cũ `char`:

```json
{
  "text": "Nội dung cần đọc.",
  "char": "thanh_hu_dao_truong"
}
```

## Tab thiết kế giọng trong Web UI

Mở `http://127.0.0.1:8011/` và chuyển sang tab **Thiết kế giọng**. Chọn thuộc tính giọng, nhập câu mẫu sẽ được tổng hợp, rồi tạo preview để nghe. Câu mẫu này sẽ được lưu nguyên văn làm `ref_text` cho WAV sinh ra.

Trong danh sách chỉ hiện **alias**, không hiện các char chính đã tồn tại. Chọn một alias làm char chính; có thể đánh dấu thêm alias phụ. Khi nhấn **Lưu thành nhân vật**, server sẽ tạo `audio/<alias-chính>.wav`, thêm object nhân vật với alias chính làm khóa vào `characters.json`, và chuyển các alias phụ được chọn sang nhân vật mới. Các alias đã chọn được gỡ khỏi nhân vật sở hữu cũ. Sau đó cấu hình được nạp lại mà không tải lại model.

Các char chính hiện có không thể chọn hoặc ghi đè. Nếu alias đã bị thay đổi trong lúc mở UI, thao tác lưu bị từ chối và cần tải lại danh sách. Preview chưa lưu nằm trong `output/voice_design_previews` và được giới hạn số lượng.

WAV Voice Design sau khi lưu được dùng làm audio tham chiếu cho TTS nhân vật. Giọng clone có thể khác preview đôi chút; nên nghe thử lại bằng tab **Thử giọng** sau khi lưu.

## Tab tạo nhân vật từ audio

Tab **Tạo nhân vật** tạo char chính mới từ audio WAV do bạn upload. Mã char được tự lấy từ tên file bỏ `.wav` (ví dụ `bo_vi.wav` → `bo_vi`); tên file cần theo dạng chữ thường, số, `_` hoặc `-`. Nhập tên hiển thị và transcript chính xác của audio (`ref_text`). Tốc độ không bắt buộc; để trống sẽ dùng `1.0`. Câu nghe thử đã được điền mặc định; nếu gửi trống, server vẫn tự dùng câu mặc định. Có thể chọn checkbox alias từ các char đang bật để chuyển sang char mới, nhưng danh sách alias được phép rỗng.

Nhấn **Tạo preview nghe thử** để model tổng hợp câu thử bằng WAV và `ref_text` vừa cung cấp. Nghe preview, sau đó nhấn **Lưu nhân vật và alias**. WAV upload gốc được lưu thành `audio/<char>.wav`; preview không dùng làm audio tham chiếu. Nếu có alias được chọn, server gỡ chúng khỏi các char chủ cũ và đưa vào char mới. Cấu hình cùng audio được kiểm tra trước khi thay thế; char chính đã tồn tại sẽ không bị ghi đè. File upload giới hạn 50 MiB và phải là WAV hợp lệ.

Endpoints nội bộ của tab gồm `POST /api/characters/create/preview` (multipart gồm `audio`, `ref_text`, `preview_text`, `speed`; server suy ra `char_id` từ tên file), `GET /api/characters/create/previews/{preview_id}` và `POST /api/characters/create/save` (gửi `preview_id`, `name`, `aliases`). Client game không cần gọi các endpoint này.

## Tab quản lý alias

Tab **Quản lý alias** cho phép chọn char nguồn, đánh dấu một hoặc nhiều alias bằng checkbox (có nút chọn/bỏ chọn tất cả), rồi chọn char đích. Nhấn **Chuyển alias và lưu JSON** để cập nhật `characters.json`; server xác thực toàn bộ danh sách và nạp cấu hình mới nguyên tử, không tải lại model. Nếu một alias không hợp lệ thì cả nhóm không được lưu. Char chính không bị đổi, chỉ quyền sở hữu alias thay đổi. Char đích phải đang được bật.

Tab **Chỉnh sửa nhân vật** cho phép tìm char chính bằng key, tên hiển thị hoặc bất kỳ alias nào đang thuộc char đó. Có thể sửa tên, `ref_text`, language, `speed`, `enabled`, `parameters` và danh sách alias. Nút **Nghe thử** dùng ngay cấu hình đang nhập nhưng chưa ghi JSON. Khi nhấn lưu, server tạo `characters.backup-edit-*.json`, xác thực toàn bộ cấu hình, thay file nguyên tử và reload danh sách nhân vật mà không tải lại model.

Nếu mở nhân vật bằng một alias, tab sẽ hiện mục **Chuyển alias sang giọng khác**. Chọn char chính đích trong dropdown rồi lưu; alias được gỡ khỏi char hiện tại và chuyển sang char đích, sau đó tự sử dụng audio, `ref_text`, speed và parameters của char mới. Key của các char chính không bị thay đổi.

Nút **Kiểm tra mất nhân vật / alias** so sánh `characters.json` với backup gần nhất và toàn bộ file `characters.backup*.json`. Báo cáo phân biệt mã mất hoàn toàn, mã mới, alias đã chuyển chủ, char chính/alias đổi loại và backup bị lỗi. Việc một alias chỉ chuyển sang char khác không bị báo nhầm là mất.

Các endpoint giao diện sử dụng là `GET /api/characters/manage` và `POST /api/characters/aliases/move` với JSON gồm `source_char`, `aliases` (mảng), `target_char`. Endpoint quản lý này dành cho giao diện cục bộ, không cần gọi từ client game.

## 10. Cache

Nếu nội dung, giọng nhân vật, model và tham số không thay đổi, server trả WAV đã cache mà không chạy inference lần nữa. Mã chính và alias của cùng một nhân vật sử dụng chung cache. Client không cần quản lý cache.

## 11. Tóm tắt dành cho client

Client chỉ cần thực hiện một request:

```http
POST http://127.0.0.1:8011/api/tts
Content-Type: application/json
```

```json
{
  "text": "Nội dung lời thoại",
  "character": "mã_nhân_vật_từ_game"
}
```

Nếu HTTP `200`, body là file WAV. Client không cần gọi `/api/characters`.
