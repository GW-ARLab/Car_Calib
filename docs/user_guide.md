# Hướng dẫn sử dụng hệ thống car-calib

Tài liệu dành cho người vận hành xe: bật hệ thống, dùng dashboard, ghi và phát
lại route, chỉnh thông số (tune), deploy và xử lý sự cố. Chi tiết kỹ thuật của
từng module nằm trong các file `docs/*.md` khác (xem mục 9).

## 1. Hệ thống làm gì

Xe RC tự chạy theo làn: camera ở đầu xe nhìn các đường ron gạch (ô 60×60 cm),
thuật toán thị giác tìm hai vạch làn, suy ra hướng đi và điều khiển lái. Bánh
dẫn động ở phía trước, bánh đuôi (caster) được servo bẻ lái.

```text
[Camera] -> Raspberry Pi 5 (main_pi5.py: vision + PID + dashboard)
              | MQTT  car/raspi5/control
              v
        Raspberry Pi 4 (DataProcessingCenter, broker MQTT)
              | UART
              v
            ESP32 -> servo + động cơ + relay
```

- **Pi5** chạy container `car-calib-pi5`: đọc camera, phát hiện làn, tính góc
  lái, phát lệnh qua MQTT, phục vụ dashboard web (cổng 8080), ghi route.
- **Pi4** nhận lệnh MQTT và điều khiển ESP32. Pi5 không chạm GPIO.
- Mạng: dây LAN (192.168.10.x) nối các thiết bị nội bộ (Pi5 ↔ Pi4 ↔ broker);
  WiFi của Pi5 dùng để ra internet và để vào dashboard từ máy tính.

## 2. Bật hệ thống và mở dashboard

1. Cấp nguồn xe, đợi Pi5 và Pi4 khởi động. Container Pi5 tự chạy lại
   (`restart: unless-stopped`).
2. Mở trình duyệt: `http://<IP-Pi5>:8080/?token=<DASHBOARD_TOKEN>`.
   - `DASHBOARD_TOKEN` đặt trong `.env.pi5`; thiếu token thì dashboard không
     khởi động trên LAN. Không chia sẻ token ra ngoài.
   - Khi không chắc IP nào dùng được: LAN `192.168.10.20`, WiFi `192.168.0.10`.
3. Kiểm tra các pill trên đầu trang: **E-stop** (CLEAR), **Actuator** (ONLINE),
   **MQTT** (connected). MQTT mà disconnected nghĩa là Pi5 chưa nói chuyện
   được với broker trên Pi4: xe sẽ không nhận lệnh.
4. Bấm **⏻ bật xe** để cấp điện cho xe (xung 0,1 s); **⭘ tắt xe** để tắt
   (xung 3 s).

## 3. Giao diện dashboard

| Khu vực | Công dụng |
| --- | --- |
| Live stream | Hình camera kèm overlay: vùng ROI hình thang, hai vạch làn được chọn (nhãn LEFT/RIGHT), điểm hội tụ (VP), HUD trạng thái. Có chọn camera và nút quét lại. |
| Status | Pill trạng thái, các số đo (góc VP, sai số, lệnh cuối), biểu đồ theta/servo 30 giây gần nhất, thanh trạng thái FSM, nhật ký sự kiện. |
| Tab **Script** | Soạn kịch bản chạy theo bước, lưu preset, bắt đầu route, ghi hình, joystick khẩn cấp. |
| Tab **Tune** | Chỉnh thông số thị giác và điều khiển ngay khi xe đang chạy (mục 6). |
| Recent routes | Danh sách route đã ghi: xem tóm tắt, tải zip, xuất Excel, so sánh, xoá. |

### 3.1 Trạng thái điều khiển (FSM)

Thanh FSM và HUD hiển thị trạng thái hiện tại của bộ điều khiển lái:

| Trạng thái | Ý nghĩa | Lệnh lái |
| --- | --- | --- |
| `GAPPING` | Mất hình học (không tìm được cặp vạch làn) | Giữ thẳng (center) |
| `DANGER_LEFT` | Vạch phải đã lấn vào trong vùng an toàn: xe lệch trái | Lái phải một góc cố định (`nudge`) |
| `DANGER_RIGHT` | Vạch trái đã lấn vào trong vùng an toàn: xe lệch phải | Lái trái một góc cố định |
| `TRACKING_COAST` | Sai số nằm trong vùng chết (hysteresis) | Giữ thẳng |
| `TRACKING_PD` | Sai số vượt ngưỡng ngoài: bám làn bằng PD | Góc PD |

Quy ước lái: góc âm = rẽ trái, góc dương = rẽ phải.

## 4. Chạy xe

### 4.1 Chạy theo kịch bản (Script)

1. Chọn hành động, nhập thời lượng (giây), bấm **+ add step**. Các hành động:
   `forward`, `backward`, `left`, `right`, `turn_left`, `turn_right`,
   `straight` (bí danh của forward), `stop`.
   - `forward`/`straight`: tiến, lái do thị giác quyết định.
   - `left`/`right`: tiến và lái hết cỡ sang trái/phải.
   - `turn_left`/`turn_right`: xoay tại chỗ bằng động cơ, không dùng servo.
2. Có thể nhập khoảng cách (m) để hệ thống tính tốc độ trung bình.
3. **save current** lưu thành preset; **load**/**delete** để dùng lại hoặc xoá.
4. Bấm **▶ bắt đầu route**: chạy kịch bản và tự ghi route. Bấm lần nữa để dừng.
5. Giới hạn an toàn: tối đa 64 bước, mỗi bước 30 s, tổng 300 s
   (`ROUTE_MAX_*` trong `.env.pi5`).

### 4.2 Ghi route khi không có kịch bản

**● bắt đầu record** giữ phiên ghi mở kể cả khi không chạy kịch bản (ví dụ lái
tay rồi xem lại). Bấm lần nữa để đóng; route chỉ được chốt khi không còn
kịch bản nào đang chạy.

### 4.3 Joystick khẩn cấp

Mục **Emergency joystick** cho lái tay ngay trên dashboard (kéo núm). Chỉ dùng
khi cần can thiệp; nhả núm thì xe về trạng thái an toàn.

### 4.4 Dừng khẩn cấp (E-stop)

Khi E-stop được nhấn, pill chuyển trạng thái và xe dừng. Chỉ reset được khi
nút đã nhả: dùng nút reset E-stop trên dashboard. Nếu vẫn không reset được,
kiểm tra nút vật lý trước.

### 4.5 Phát hiện vật cản

Nếu `OBJECT_DETECTION_ENABLED=true` (cần model ONNX trong
`$PI5_DEST_DIR/data/models`), Pi5 phát lệnh STOP khi thấy người (hoặc mọi vật
nếu `OBJECT_STOP_ON_ANY_DETECTION=true`) trong vùng gần xe, và tạm dừng kịch
bản đang chạy.

## 5. Route đã ghi

Mỗi route là một thư mục `route-*` trong `/opt/car-calib-pi5/data/routes` trên
Pi5 (kèm file `.zip` để tải nhanh):

| File | Nội dung |
| --- | --- |
| `route_summary.json` | Tóm tắt, tune đã dùng (`extra_meta.calib_tune`), kết quả chấp nhận/loại |
| `route_frames.csv` | Số đo từng khung hình |
| `route.avi` | Video có overlay |
| `route_raw.avi` | Video gốc không overlay (chỉ khi `ROUTE_RAW_VIDEO_ENABLED=true`) |

Trên dashboard: bấm một route để xem tóm tắt; chọn nhiều route để **Download
zip**, **Export .xlsx** hoặc **So sánh**. Route chỉ được chấp nhận khi đủ số
khung hình và ít lỗi phần cứng/khoảng trống (xem `CLAUDE.md`). Dữ liệu cũ bị
dọn sau `DATA_RETENTION_DAYS` (7 ngày) hoặc khi vượt `DATA_MAX_BYTES` (2 GB).

> Không sửa hay làm giả dấu thời gian của dữ liệu thực nghiệm thật.

## 6. Chỉnh thông số (tab Tune)

Chỉ áp dụng được khi xe đứng yên (base đang STOP). Quy trình: chỉnh giá trị →
**Apply** (áp dụng ngay) → thử → **Save** (ghi vào `calib_tune.json`, giữ qua
lần khởi động sau). **Reset saved** quay về bản đã lưu, **Reset defaults** về
mặc định trong env.

| Nhóm | Thông số | Ý nghĩa |
| --- | --- | --- |
| Vision | ROI height / top / bottom width | Vùng ảnh được xét (hình thang). ROI nhỏ quá thì dễ mất vạch → `GAPPING` nhiều |
| Vision | Canny low/high, Hough threshold / min line / max gap | Phát hiện cạnh và đoạn thẳng |
| Vision | Min abs slope | Loại các đoạn gần nằm ngang |
| Vision | VP horizon row | Hàng ảnh nơi các vạch làn hội tụ (mặc định `-518` với ảnh 640×480). Chỉ đổi khi chỉnh góc nghiêng camera hoặc độ phân giải; đo lại bằng một video đi thẳng. Lệch cỡ 100 px trở lên sẽ chọn sai cặp vạch |
| Steering | PID Kp / Kd | Độ mạnh phản ứng của bộ lái PD |
| Steering | VP inner / outer | Ngưỡng vùng chết (hysteresis) của sai số hướng |
| Steering | Danger margin | Bề rộng dải cấm ở hai mép ảnh (px). Vạch chạm dải này thì vào `DANGER_*` |
| Steering | Danger nudge | Góc lái cố định khi ở `DANGER_*` |

Lưu ý quan trọng về **Danger margin**: làn rộng khoảng 352 px ở mép dưới ảnh
640 px. Vùng an toàn còn lại là `640 − 2×margin`; nếu nhỏ hơn bề rộng làn thì
`TRACKING_PD` không bao giờ xảy ra và xe chỉ chạy kiểu nẩy trái–phải. Với
camera hiện tại, margin quanh 60 cho kết quả tốt trên video thử; margin từ
145 trở lên khiến tracking bất khả thi.

Nếu tab Tune thiếu một thông số mới sau khi cập nhật hệ thống, tải lại trang
(Ctrl+F5).

## 7. Phát lại route để thử thông số (offline)

Cho phép thử đổi thông số trên một route đã ghi mà không cần cho xe chạy lại.

```bash
python scripts/replay_route.py <thư_mục_route> --env-file .env.pi5
python scripts/replay_route.py <thư_mục_route> --env-file .env.pi5 --set hough_threshold=30
python scripts/replay_route.py <thư_mục_route> --env-file .env.pi5 --csv replay.csv --json
```

- Script áp dụng tune đã lưu trong route trước, `--set KEY=VALUE` ghi đè từng
  giá trị để so sánh A/B.
- Dùng `route_raw.avi` nếu có. Route cũ chỉ có `route.avi` (đã vẽ overlay) cho
  số liệu bị lệch vì overlay tạo thêm cạnh mà xe chưa từng thấy.
- Kết quả in các số đo ổn định của lần chạy thật và của bản phát lại, cùng độ
  khớp góc lái.

## 8. Cài đặt, deploy và kiểm thử

### 8.1 Cấu hình

Sao chép `.env.pi5` và đặt: `PI5_HOST`, `PI5_USER`, `PI5_PASSWORD` (bỏ trống
nếu dùng khoá SSH), `MQTT_BROKER_HOST` (địa chỉ Pi4, dùng IP vì mDNS không
phân giải trong container), `DASHBOARD_TOKEN` (chuỗi ngẫu nhiên dài). Không
commit mật khẩu hay token thật.

### 8.2 Deploy lên Pi5

```bash
./deploy_pi5_remote.sh            # nén repo → SCP → build → chạy container
./deploy_pi5_remote.sh --no-build # chỉ tải lên và khởi động lại
./deploy_pi5_remote.sh --yes      # bỏ qua bước hỏi xác nhận
```

Script giữ 3 bản release gần nhất, đổi symlink `current` sang bản mới và chạy
`docker compose -f docker-compose.pi5.yml up`. Script chỉ đợi build tối đa
120 s, nếu build lâu hơn thì vào Pi5 kiểm tra `docker compose ... logs`.
Deploy làm khởi động lại container nên chỉ làm khi xe đứng yên.

### 8.3 Kiểm thử trên máy dev

```bash
python -m pytest tests -q
```

Hiện có 9 test lỗi sẵn trong `tests/test_unified_calibration_characterization.py`
(không liên quan thay đổi gần đây); các test còn lại phải pass.

## 9. Xử lý sự cố

| Hiện tượng | Nguyên nhân thường gặp | Cách xử lý |
| --- | --- | --- |
| Không mở được dashboard | Sai IP/token, container chưa chạy | Thử cả IP LAN và WiFi; trên Pi5: `docker ps`, `docker logs car-calib-pi5` |
| Pill MQTT disconnected | Sai `MQTT_BROKER_HOST`, Pi4/broker chưa chạy, dây LAN | Kiểm tra IP broker, ping Pi4 từ Pi5 |
| Xe không nhận lệnh nhưng MQTT ổn | E-stop đang chốt, hoặc Pi4 ở chế độ tay | Reset E-stop; kiểm tra `car/control/mode` trên Pi4 |
| Luôn `GAPPING` | ROI quá nhỏ, `VP horizon row` lệch, ánh sáng xấu | Mở rộng ROI, đo lại horizon, kiểm tra overlay |
| Xe nẩy trái–phải, ít `TRACKING_PD` | Danger margin quá lớn | Giảm margin (mục 6) |
| Tab Tune thiếu thông số mới | Tab mở từ trước lúc deploy | Ctrl+F5 |
| Camera không lên | Sai chỉ số camera | Chọn camera ở mục Live stream, hoặc đặt `MAIN_CAMERA_INDEX` |
| Vòng lặp bị giật theo chu kỳ | Tải CPU (nghi ngờ do model phát hiện vật cản chạy chung với giới hạn `PI5_CPU_LIMIT`) | Tắt tạm `OBJECT_DETECTION_ENABLED`, hoặc tăng giới hạn CPU |

## 10. Tài liệu liên quan

| File | Nội dung |
| --- | --- |
| `README.md` | Phương pháp hiệu chỉnh hiện tại và bản đồ repo |
| `system_architecture.md` | Đường xử lý chung và trách nhiệm module |
| `docs/vision.md`, `docs/unified_calibration_components.md` | Thị giác: chọn cặp vạch, hình học VP |
| `docs/control.md`, `docs/models.md` | Bộ điều khiển lái và trạng thái robot |
| `docs/calibration_evaluation.md` | Đánh giá trên video ghi sẵn |
| `docs/docker_images.md` | Các image Docker và cách chạy |
| `docs/esp32_mqtt_bridge.md`, `docs/python_mqtt_bridge.md` | Cầu nối MQTT / ESP32 |
| `docs/LOG_VISUALIZATION_GUIDE.md` | Xem log và vẽ biểu đồ |
