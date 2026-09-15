# Ghép trang hồ sơ nghiệm thu AI vào FE

Trang nghiệm thu do gateway phục vụ, gateway chạy ở `123.16.179.18:8131`. Toàn bộ showcase (trang,
API, TensorBoard) nằm chung dưới một tiền tố `/showcase`.

## 1. Link mở tab

```html
<a href="http://123.16.179.18:8131/showcase-demo/" target="_blank" rel="noopener">Hồ sơ nghiệm thu AI</a>
```

Link này vào thẳng gateway theo IP nên trình duyệt hiện IP public và là HTTP. Chỉ dùng để test.
Khi đã thêm nginx (mục 2), đổi href thành `https://ias.id.vn/showcase-demo/`.

## 2. Thêm route vào nginx

Vì toàn bộ showcase nằm dưới `/showcase` nên chỉ cần một dòng. Thêm vào server block `ias.id.vn`
(cùng file đang có `/api/ais/`), rồi reload:

```nginx
location ^~ /showcase { proxy_pass http://123.16.179.18:8131; proxy_buffering off; }
```

```bash
nginx -t && systemctl reload nginx
```

`proxy_buffering off` để log bench và TensorBoard cập nhật realtime, không bị nginx gom cục.
