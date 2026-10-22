# Tốc độ đọc theo nhân vật

Thêm trường `speed` trực tiếp vào object nhân vật trong `characters.json`:

```json
{
  "name": "Ví dụ nhân vật",
  "ref_audio": "audio/nhan_vat.wav",
  "ref_text": "Bản chép lời đúng với audio tham chiếu.",
  "language": "Vietnamese",
  "enabled": true,
  "speed": 1.0,
  "parameters": {}
}
```

`speed` mặc định là `1.0`; giá trị lớn hơn `1.0` đọc nhanh hơn, nhỏ hơn `1.0` đọc chậm hơn. Giá trị phải là số hữu hạn lớn hơn `0`. Ví dụ `1.15` nhanh hơn mặc định một chút, còn `0.9` chậm hơn.

Alias không có cấu hình TTS riêng: khi client gửi alias, server phân giải alias về char sở hữu nên tự dùng cùng `speed`, `ref_audio`, `ref_text` và các tham số của char đó. Chỉnh `speed` ở char chính sẽ áp dụng cho toàn bộ alias của char. Sau khi sửa JSON, gọi `POST /api/characters/reload` để áp dụng mà không tải lại model, hoặc khởi động lại server.

Không đặt `speed` bên trong `parameters`; đây là trường chuyên biệt được server xác thực và truyền trực tiếp vào `model.generate`. Giá trị speed cũng được đưa vào khóa cache để WAV ở các tốc độ khác nhau không bị dùng nhầm.
