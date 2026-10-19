# Wandering Sword — Local OmniVoice TTS API

REST API chạy cục bộ cho model [`kjanh/KhanhTTS-OmniVoice`](https://huggingface.co/kjanh/KhanhTTS-OmniVoice), hỗ trợ clone giọng theo cấu hình nhân vật. Model được tải đúng **một lần** lúc server khởi động.

## Chuẩn bị

- Windows, Python 3.11 và GPU NVIDIA được khuyến nghị.
- Môi trường hiện tại đã có `.venv`; cài thư viện bằng:

```powershell
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Nếu bản `torch` mặc định không nhận CUDA, cài wheel phù hợp theo hướng dẫn chính thức của PyTorch rồi kiểm tra:

```powershell
python -c "import torch; print(torch.cuda.is_available(), torch.version.cuda)"
```

Lần chạy đầu cần Internet để tải model khoảng vài GB từ Hugging Face. Những lần sau dùng model trong cache cục bộ.

## Cấu hình giọng

1. Chép WAV thật vào thư mục `audio/`.
2. Sửa `characters.json`: đặc biệt, thay mọi `ref_text` mẫu bằng **bản chép lời chính xác** của WAV tương ứng.
3. Khởi động server.

Các file WAV mẫu không được tạo giả trong repository. Vì ba mục minh họa trong `characters.json` đang bật, server sẽ chủ động báo lỗi cho đến khi có đủ ba file WAV. Có thể xóa mục chưa dùng hoặc đặt `enabled: false`, nhưng file tham chiếu của mọi mục vẫn được kiểm tra để phát hiện cấu hình hỏng sớm.

Muốn thêm nhân vật, chỉ cần thêm một object dưới `characters` và file WAV; không sửa Python. `char` là khóa JSON; `aliases` tùy chọn cho phép nhiều mã game dùng chung một giọng và cache. `language`, `ref_audio` và `ref_text` được lấy từ object nhân vật. `parameters` được chuyển thẳng vào `model.generate`, ngoại trừ bốn khóa dành riêng `text`, `language`, `ref_audio`, `ref_text`.

Nếu client gửi mã chưa được cấu hình hoặc nhân vật bị tắt, server tự dùng nhân vật `default` thay vì trả `404/422`. Vì vậy `default` bắt buộc phải tồn tại, bật và có audio hợp lệ.

Mọi đường dẫn tương đối đều neo vào thư mục chứa `app.py`, không phụ thuộc terminal đang đứng ở đâu.

## Chạy

Từ thư mục bất kỳ:

```powershell
& "D:\Games\Wandering.Sword.v1.24.32\game\Wandering_Sword\Binaries\Win64\ue4ss\Mods\DialogueReader\tts\.venv\Scripts\python.exe" -m uvicorn app:app --app-dir "D:\Games\Wandering.Sword.v1.24.32\game\Wandering_Sword\Binaries\Win64\ue4ss\Mods\DialogueReader\tts" --host 127.0.0.1 --port 8011
```

Chỉ bind `127.0.0.1` để API không bị mở ra mạng LAN. Swagger UI: <http://127.0.0.1:8011/docs>.

Có thể ép thiết bị trước khi chạy:

```powershell
$env:TTS_DEVICE = "cuda:0"  # hoặc "cpu"; mặc định "auto"
```

## API

Tổng hợp và lưu kết quả trả về:

```powershell
$body = @{ text = "An đắc ỷ thiên kiếm, khóa hải trảm trường kình."; character = "thanh_hu_dao_truong" } | ConvertTo-Json
Invoke-WebRequest -Method Post -Uri http://127.0.0.1:8011/api/tts -ContentType application/json -Body $body -OutFile output\line.wav
```

- `POST /api/tts` — trả trực tiếp `audio/wav`.
- `GET /api/characters` — danh sách nhân vật và trạng thái.
- `POST /api/characters/reload` — nạp lại JSON nguyên tử, không tải lại model; nếu file mới lỗi thì snapshot cũ vẫn hoạt động.
- `GET /api/health` — trạng thái model, thiết bị và sample rate.

Kết quả được cache theo text, nhân vật, tham số, model và SHA-256 của file giọng mẫu. Cache dùng LRU gần đúng và mặc định giới hạn 1 GiB. Một executor đơn luồng đóng vai trò hàng đợi inference để tránh nhiều request tranh VRAM; request cache vẫn trả ngay.

## Biến môi trường

| Biến | Mặc định | Ý nghĩa |
|---|---:|---|
| `TTS_MODEL_ID` | `kjanh/KhanhTTS-OmniVoice` | Model Hugging Face |
| `TTS_MODEL_REVISION` | `main` | Revision/commit Hugging Face; nên ghim commit để cache tái lập tuyệt đối |
| `TTS_DEVICE` | `auto` | `cuda:0`, `cpu`, hoặc tự chọn |
| `TTS_MODEL_SAMPLE_RATE` | `24000` | Sample rate thực của OmniVoice |
| `TTS_OUTPUT_SAMPLE_RATE` | `24000` | Sample rate WAV; audio được resample nếu khác model |
| `TTS_MAX_TEXT_LENGTH` | `2000` | Giới hạn ký tự/request |
| `TTS_CACHE_ENABLED` | `true` | Bật cache WAV |
| `TTS_CACHE_MAX_MB` | `1024` | Dung lượng cache tối đa |
| `TTS_REQUEST_TIMEOUT_SECONDS` | `120` | Thời gian chờ gồm cả thời gian trong hàng đợi |
| `TTS_MODEL_CACHE_DIR` | trống | Thư mục cache model truyền cho `from_pretrained` |

## Kiểm thử

Chạy toàn bộ test không cần GPU/model:

```powershell
python -m pytest -q --basetemp=.pytest_tmp
```

Bộ test mock kiểm tra health, danh sách nhân vật, WAV PCM có thể giải mã, mã lỗi, cache, fingerprint audio, quota cache, reload nguyên tử, resampling và nhiều request qua hàng đợi một luồng.

Kiểm thử inference thật chỉ chạy khi server thật đã được khởi động và có một nhân vật hợp lệ:

```powershell
$env:TTS_REAL_TEST_CHAR = "tieu_han_quang"
$env:TTS_REAL_TEST_URL = "http://127.0.0.1:8011"
python -m pytest -q --basetemp=.pytest_tmp tests/test_real_inference.py
```

Test thật tải WAV từ API và mở bằng bộ giải mã WAV của Python. Không đặt `TTS_REAL_TEST_CHAR` thì test được đánh dấu skip; kết quả mock không đồng nghĩa GPU/model thật đã được xác nhận.

## Lưu ý

Chỉ clone giọng khi có quyền sử dụng giọng mẫu. Model card mô tả đây là model nghiên cứu/thử nghiệm; cần tự kiểm tra điều khoản của model và các thành phần upstream trước khi dùng thương mại.

python -m uvicorn app:app --host 127.0.0.1 --port 8011
