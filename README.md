# mdf2sql

Công cụ khôi phục dữ liệu SQL Server: đọc file `.mdf` và xuất ra file `.sql` chạy
lại được từ đầu đến cuối mà không báo lỗi — nạp ngược vào **SQL Server**, hoặc sang
**MySQL / MariaDB / phpMyAdmin**.

Viết cho tình huống thực tế của hệ thống bãi xe: máy tính ở bãi mất điện hoặc treo,
SQL Server không khởi động lại được, chỉ còn lại file `.mdf` trong ổ đĩa và thường
là **không có file log `.ldf`**.

## Tool làm được gì

- Đọc được cả file `.mdf` bị tắt đột ngột (dirty shutdown), không cần file `.ldf`.
- Tự phát hiện và sửa lỗi cấu trúc bằng `DBCC CHECKDB` khi file có trang dữ liệu hỏng.
- Xuất ra file `.sql` gồm đầy đủ bảng, dữ liệu, khoá chính, index, khoá ngoại,
  view, thủ tục, trigger, sắp xếp đúng thứ tự để import không vướng phụ thuộc.
- Xuất được **hai loại cú pháp**: T-SQL cho SQL Server, hoặc MySQL/MariaDB cho
  phpMyAdmin. Chọn ngay trên giao diện, hoặc bằng `--dialect mysql`.
- Giữ nguyên tiếng Việt có dấu, giá trị ngày giờ và số tiền đúng đến từng đơn vị.
- **Không bao giờ đụng vào file gốc.** Mọi thao tác chạy trên bản sao.

## Cần gì để chạy

| Thành phần | Ghi chú |
|---|---|
| Windows | đã kiểm thử trên Windows 11 |
| Python 3.9 trở lên | tải ở python.org, nhớ tick "Add Python to PATH" |
| SQL Server | bản Express hoặc LocalDB đều được, đều miễn phí |
| ODBC Driver 18 for SQL Server | `winget install Microsoft.msodbcsql.18` |
| pyodbc | file `Chay_tool.bat` tự cài lần đầu |

Nếu máy chưa có SQL Server, cách nhẹ nhất là cài **SQL Server Express LocalDB**
(khoảng 60 MB, chạy user-mode, không cần cấu hình dịch vụ):

```bash
curl -L -o SqlLocalDB.msi "https://download.microsoft.com/download/3/8/d/38de7036-2433-4207-8eae-06e247e17b25/SqlLocalDB.msi" && msiexec /i SqlLocalDB.msi /qn IACCEPTSQLLOCALDBLICENSETERMS=YES
```

Phiên bản SQL Server cài trên máy phải mới hơn hoặc bằng phiên bản đã tạo ra file
`.mdf`. SQL Server 2022 đọc được file từ 2008 trở lên. Chạy lệnh `info` bên dưới
để biết file của bạn thuộc phiên bản nào.

## Cách dùng

### Giao diện

Bấm đúp vào **`Chay_tool.bat`**. Trình duyệt sẽ mở giao diện ở `127.0.0.1`.
Chọn file `.mdf`, bấm **Chuyển đổi**, chờ vài chục giây.

### Dòng lệnh

```bash
python -m mdf2sql info "D:\ITS\database\giuxe.mdf"
```

```bash
python -m mdf2sql convert "D:\ITS\database\giuxe.mdf" -o "D:\backup\giuxe.sql"
```

Các tuỳ chọn hay dùng:

| Tuỳ chọn | Ý nghĩa |
|---|---|
| `--schema-only` | chỉ xuất cấu trúc bảng, bỏ dữ liệu |
| `--target-db TEN` | đổi tên database khi import lại |
| `--skip-blobs` | bỏ cột nhị phân lớn cho file nhẹ đi |
| `--no-repair` | không tự động sửa lỗi cấu trúc |
| `--server .\SQLEXPRESS` | chỉ định instance SQL Server cụ thể |
| `--dialect mysql` | xuất cú pháp MySQL/MariaDB thay vì T-SQL |
| `--gzip` | nén thành `.sql.gz`, phpMyAdmin nạp thẳng được |

## Import file .sql trở lại

Mở bằng SQL Server Management Studio rồi bấm Execute (F5), hoặc:

```bash
sqlcmd -S .\SQLEXPRESS -E -f 65001 -i "D:\backup\giuxe.sql"
```

File dùng mã UTF-8 kèm BOM nên tiếng Việt hiển thị đúng trong cả SSMS và sqlcmd.

## Import sang MySQL / MariaDB / phpMyAdmin

Chọn **MySQL / MariaDB** ở phần "Nơi bạn sẽ import file .sql" trên giao diện, hoặc:

```bash
python -m mdf2sql convert "D:\ITS\database\giuxe.mdf" -o "D:\backup\giuxe.sql" --dialect mysql --gzip
```

Rồi vào phpMyAdmin, tab **Import**, chọn file, bấm Go. File đã có sẵn lệnh
`CREATE DATABASE` nên không cần tạo database trước. Hoặc chạy dòng lệnh:

```bash
mysql -u root -p < giuxe.sql
```

File MySQL **không có BOM** (có BOM là phpMyAdmin báo lỗi cú pháp ngay dòng đầu).
Thêm `--gzip` khi file vượt `upload_max_filesize` của PHP: bộ dữ liệu 585 nghìn
dòng nặng 86 MB rút còn 11 MB.

### Chuyển kiểu dữ liệu sang MySQL

| SQL Server | MySQL | Ghi chú |
|---|---|---|
| `nvarchar(n)` / `varchar(n)` | `VARCHAR(n)` | bảng dùng `utf8mb4`, tiếng Việt giữ nguyên |
| `nvarchar(max)` / `text` | `LONGTEXT` | |
| `datetime` | `DATETIME(3)` | giữ đủ mili giây |
| `datetime2(n)` | `DATETIME(n)` | tối đa 6 chữ số |
| `money` | `DECIMAL(19,4)` | |
| `bit` | `TINYINT(1)` | |
| `uniqueidentifier` | `CHAR(36)` | |
| `varbinary(max)` / `image` | `LONGBLOB` | |
| `IDENTITY(s,i)` | `AUTO_INCREMENT` | đặt lại mốc bằng `ALTER TABLE ... AUTO_INCREMENT` |

Những thứ MySQL không có tương đương thì tool ghi ra dạng chú thích ở cuối file
kèm lý do, thay vì bỏ im lặng: view, thủ tục, trigger, hàm, kiểu tự định nghĩa,
ràng buộc `CHECK` dùng cú pháp riêng của SQL Server, cột tính toán.

Vài khác biệt cần biết trước:

- Tên bảng trong file giữ nguyên chữ hoa chữ thường, nhưng MySQL trên Windows mặc
  định hạ hết về chữ thường (`lower_case_table_names=1`).
- Tên dài quá 64 ký tự bị rút gọn kèm hậu tố băm để không trùng nhau.
- Cột `char(n)` của SQL Server luôn được đệm dấu cách cho đủ `n`; MySQL cắt các dấu
  cách cuối khi đọc ra. Nội dung thật không đổi.
- Khoá chính, khoá ngoại và index được tạo **sau** khi đổ dữ liệu, nên file nạp
  nhanh hơn và không vướng thứ tự phụ thuộc giữa các bảng.

## Tool xử lý file hỏng thế nào

Khi gắn file vào SQL Server, tool thử lần lượt từ cách nhẹ đến cách mạnh và dừng
ở cách đầu tiên thành công:

1. **Gắn kèm file log** nếu còn `.ldf`. Chính xác nhất, không mất gì.
2. **Gắn và tự dựng lại log** khi thiếu `.ldf` nhưng database đã tắt sạch sẽ.
3. **Cứu hộ**: tạo một database rỗng cùng tên file, đưa offline, tráo file `.mdf`
   thật vào, bật chế độ khẩn cấp rồi dựng lại log từ đầu. Đây là cách duy nhất
   chạy được khi file `.mdf` cũ hơn phiên bản SQL Server đang chạy, vì lúc đó
   `DBCC CHECKDB` bị từ chối với lỗi 946 còn `ALTER DATABASE REBUILD LOG` thì không.

Gắn xong, tool chạy `DBCC CHECKDB`. Nếu có lỗi cấu trúc thì sửa bằng
`REPAIR_ALLOW_DATA_LOSS` rồi kiểm tra lại lần nữa.

Khi đọc dữ liệu, nếu gặp trang đĩa hỏng thì tool giữ lại phần đã đọc được và tự
đọc tiếp từ bản ghi cuối cùng thay vì bỏ cả bảng. Những dòng có giá trị vượt kích
thước cột (dữ liệu rác sinh ra từ trang hỏng) được tách sang file `*_dong-loi.sql`
để file chính luôn import sạch.

## Vì sao file .sql sinh ra import không lỗi

- Thứ tự trong file: tạo bảng, đổ dữ liệu, rồi mới tới khoá chính, index, check,
  khoá ngoại. Nhờ vậy không bao giờ vướng thứ tự phụ thuộc giữa các bảng.
- Cột `IDENTITY` được bọc trong `SET IDENTITY_INSERT`, cột tính toán và
  `rowversion` bị loại khỏi câu `INSERT` vì không thể ghi trực tiếp.
- Ngày giờ ghi theo chuẩn ISO 8601 nên không phụ thuộc `DATEFORMAT` của máy đích.
- Chuỗi dài hơn 3500 ký tự được nối từ nhiều mảnh bằng `CAST(... AS nvarchar(max))`
  để không bị SQL Server cắt ngầm ở mốc 4000 ký tự.
- Trước khi tạo khoá chính, tool đếm giá trị trùng; nếu có thì ghi câu lệnh đó ra
  dạng chú thích kèm cảnh báo, thay vì để file chết giữa chừng.
- Khoá ngoại có dòng mồ côi được tạo ở chế độ `WITH NOCHECK`.

## Cấu trúc mã nguồn

```
mdf2sql/
  dbconn.py     dò SQL Server trên máy, mở kết nối, đọc boot page của .mdf
  attacher.py   sao chép file ra vùng làm việc rồi gắn vào SQL Server
  introspect.py đọc schema từ các view hệ thống sys.*
  emit.py       chuyển giá trị Python thành literal T-SQL an toàn
  mysql.py      sinh script MySQL/MariaDB: kiểu dữ liệu, literal, thứ tự câu lệnh
  reader.py     đọc dữ liệu chịu được trang đĩa hỏng, dùng chung cho cả hai dialect
  convert.py    điều phối toàn bộ quy trình và sinh file .sql
  server.py     máy chủ web cục bộ cho giao diện
  cli.py        giao diện dòng lệnh
  web/          giao diện: index.html, app.css, app.js
```

## Về bảo mật

Máy chủ web chỉ lắng nghe trên `127.0.0.1` và đòi một mã thông hành ngẫu nhiên
sinh ra mỗi lần khởi động, nên trang web khác đang mở trên máy không gọi được API.
Không có dữ liệu nào rời khỏi máy của bạn.

## Giấy phép

MIT.
