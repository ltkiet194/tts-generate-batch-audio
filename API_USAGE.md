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
      "parameters": {}
    }
  }
}
```

Với `character: "thanh_hu_dao_truong"`, server tự dùng `audio/thanh_hu_dao_truong.wav` và `ref_text` tương ứng.

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
