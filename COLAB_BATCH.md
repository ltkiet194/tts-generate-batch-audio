# Sinh toàn bộ WAV bằng Google Colab

Chế độ này không mở REST API. Colab nạp model một lần, sinh tuần tự toàn bộ câu thoại và lưu WAV lên Google Drive. Khi chơi game chỉ cần đọc các WAV đã tạo.

## 1. Chuẩn bị trên Google Drive

Tạo thư mục:

```text
MyDrive/WanderingSwordVoice/tts/
├── batch_generate.py
├── character_manager.py
├── config.py
├── tts_engine.py
├── characters.json
├── requirements-colab.txt
├── dialogues.csv
└── audio/
    └── ... các WAV tham chiếu ...
```

`dialogues.csv` dùng UTF-8 và có đúng ba cột bắt buộc:

```csv
id,char,text
dialogue_0001,thanh_hu_dao_truong,Thiên địa vô cực.
dialogue_0002,au_duong_hai,Các hạ muốn đi đâu?
dialogue_0003,unknown_npc,Xin hãy dừng bước.
```

- `id`: khóa duy nhất và được dùng để đặt tên WAV.
- `char`: mã chính hoặc alias trong `characters.json`.
- `text`: nội dung cần đọc.
- `char` không tồn tại hoặc bị tắt sẽ dùng giọng `default` và được đánh dấu trong manifest.
- Có thể dùng JSONL thay CSV; mỗi dòng là `{"id":"...","char":"...","text":"..."}`.

## 2. Bật GPU trong Colab

Chọn **Runtime → Change runtime type → GPU**. Sau đó chạy từng cell dưới đây.

```python
from google.colab import drive
drive.mount('/content/drive')
```

```python
%cd /content/drive/MyDrive/WanderingSwordVoice/tts
```

Không cài lại Torch từ `requirements.txt`, vì Colab đã có bản Torch tương thích CUDA:

```python
!pip install -q -r requirements-colab.txt
```

Kiểm tra GPU:

```python
import torch
assert torch.cuda.is_available(), "Colab chưa được cấp GPU"
print(torch.__version__, torch.version.cuda, torch.cuda.get_device_name(0))
```

## 3. Cấu hình phiên chạy

Đặt biến môi trường ngay trong notebook. Output nằm trên Drive để không mất khi phiên Colab bị ngắt; model cache mặc định nằm ở `/content` để đọc nhanh:

```python
import os

os.environ.update({
    "TTS_DEVICE": "cuda:0",
    "TTS_MODEL_ID": "kjanh/KhanhTTS-OmniVoice",
    "TTS_MODEL_REVISION": "main",
    "TTS_MODEL_CACHE_DIR": "/content/model-cache",
    "TTS_CHARACTERS_FILE": "characters.json",
    "TTS_DEFAULT_CHARACTER": "default",
    "TTS_BATCH_INPUT": "dialogues.csv",
    "TTS_BATCH_OUTPUT_DIR": "/content/drive/MyDrive/WanderingSwordVoice/generated_audio",
    "TTS_BATCH_CHECKPOINT_EVERY": "10",
})
```

Nếu muốn tránh tải lại model ở phiên sau, đổi `TTS_MODEL_CACHE_DIR` thành thư mục trên Drive. Cách đó bền hơn nhưng tải model từ Drive vào GPU chậm hơn.

## 4. Chạy thử rồi chạy toàn bộ

Sinh thử 3 câu trước:

```python
!python batch_generate.py --limit 3 --retry-failed
```

Nghe thử ngay trong Colab:

```python
from IPython.display import Audio, display
display(Audio('/content/drive/MyDrive/WanderingSwordVoice/generated_audio/dialogue_0001.wav'))
```

Khi kết quả đúng, chạy toàn bộ:

```python
!python batch_generate.py --retry-failed
```

## Tiếp tục sau khi bị ngắt

Chạy lại đúng lệnh trên. Script xác minh WAV, text, char và phiên bản model trong `manifest.json`:

- WAV hợp lệ và dữ liệu không đổi: bỏ qua.
- WAV thiếu/hỏng hoặc text/char/model thay đổi: sinh lại.
- Lỗi của từng dòng được ghi vào `failed.json`; thêm `--retry-failed` để thử lại.
- `Ctrl+C`/dừng cell sẽ lưu checkpoint sau câu đang sinh.

Output:

```text
generated_audio/
├── dialogue_0001.wav
├── dialogue_0002.wav
├── manifest.json
└── failed.json
```

Mỗi mục manifest lưu `char_requested`, `char_resolved`, trạng thái fallback, text, model và thời gian sinh để có thể kiểm tra bộ audio sau này.

## Lệnh tùy chọn

```bash
python batch_generate.py \
  --input /content/drive/MyDrive/data/dialogues.jsonl \
  --output /content/drive/MyDrive/WanderingSwordVoice/generated_audio \
  --checkpoint-every 5 \
  --retry-failed
```

Không chạy nhiều tiến trình batch cùng ghi vào một thư mục output. Một tiến trình đã dùng inference tuần tự nên an toàn hơn cho VRAM Colab.
