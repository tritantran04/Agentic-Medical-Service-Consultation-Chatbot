from flask import Flask, render_template, request, redirect, url_for, flash
import os
import gspread
import pandas as pd
from google.oauth2.service_account import Credentials
from werkzeug.utils import secure_filename
from pyngrok import ngrok

# ====== Flask App Setup ======
app = Flask(__name__)
app.secret_key = "secret-key-123"

# ====== Google Sheet Setup ======
scopes = ['https://www.googleapis.com/auth/spreadsheets']
creds = Credentials.from_service_account_file('credentials.json', scopes=scopes)
client = gspread.authorize(creds)
app.config['TEMPLATES_AUTO_RELOAD'] = True

SHEET_ID = '1tA48K1GlPuRkJ_9iGnoZaVcaNSgBA7-CsSwtMKUXG1A'  # thay bằng sheet ID của bạn
sheet_packages = client.open_by_key(SHEET_ID).worksheet('packages')
sheet_services = client.open_by_key(SHEET_ID).worksheet('services')
# ====== ROUTE: Trang chủ (Form thêm gói khám) ======

@app.route('/', methods=['GET'])
def index():
    try:
        # Đọc dữ liệu từ sheet "services"
        sheet_services = client.open_by_key(SHEET_ID).worksheet('services')
        services = sheet_services.get_all_records()
        print("✅ Dịch vụ đọc được:", services)  # <-- để kiểm tra
    except Exception as e:
        services = []
        print("❌ Lỗi đọc sheet services:", e)
        flash(f'Lỗi khi tải danh sách dịch vụ: {e}')

    return render_template('index.html', services=services)


# ====== ROUTE: Xử lý form thêm gói khám ======
@app.route('/package', methods=['POST'])
def add_package():
    try:
        id_val = request.form.get('id')
        name = request.form.get('name')
        description = request.form.get('description')
        price = request.form.get('price')
        services_ids = request.form.get('services', '')

        # Kiểm tra thông tin bắt buộc
        if not all([name, description, price]):
            flash('⚠️ Vui lòng điền đủ thông tin bắt buộc (tên, mô tả, giá).')
            return redirect(url_for('index'))

        # Nếu không nhập ID thì tự động set = số dòng hiện có + 1
        if not id_val:
            existing = sheet_packages.get_all_records()
            id_val = len(existing) + 1

        # Đảm bảo kiểu dữ liệu
        try:
            price = int(price)
        except ValueError:
            flash('⚠️ Giá tiền phải là số nguyên hoặc số thực!')
            return redirect(url_for('index'))

        
        sheet_packages.append_row([
            id_val,
            name,
            description,
            price,
            services_ids
        ])

        flash('Đã thêm gói khám thành công!')
    except Exception as e:
        flash(f'❌ Lỗi khi thêm gói khám: {e}')
    return redirect(url_for('index'))


# ====== ROUTE: Xem dữ liệu ======
# @app.route('/view', methods=['GET'])
# def view_packages():
#     try:
#         records = sheet_packages.get_all_records()
#         total_packages = len(records)
#     except Exception as e:
#         records, total_packages = [], 0
#         flash(f'Lỗi khi lấy dữ liệu: {e}')
#     return render_template('view.html', packages=records, total_packages=total_packages)

@app.route('/view', methods=['GET'])
def view_data():
    try:
        # Đọc dữ liệu từ Google Sheets
        sheet_packages_local = client.open_by_key(SHEET_ID).worksheet('packages')
        sheet_services_local = client.open_by_key(SHEET_ID).worksheet('services')
        packages = sheet_packages_local.get_all_records()
        services = sheet_services_local.get_all_records()
    except Exception as e:
        packages, services = [], []
        flash(f'❌ Lỗi khi tải dữ liệu: {e}')
        return redirect(url_for('index'))

    return render_template('view.html', packages=packages, services=services)

# ====== ROUTE: Xóa gói khám ======
@app.route('/delete/<int:row>', methods=['POST'])
def delete_package(row):
    try:
        # +1 vì Google Sheet có dòng header
        sheet_packages.delete_rows(row + 1)
        flash('🗑️ Đã xóa gói khám thành công!')
    except Exception as e:
        flash(f'❌ Lỗi khi xóa gói khám: {e}')
    return redirect(url_for('view_packages'))

# ====== ROUTE: Trang cập nhật gói khám ======
@app.route('/update', methods=['GET', 'POST'])
def update_package():
    try:
        records = sheet_packages.get_all_records()
        sheet_services_local = client.open_by_key(SHEET_ID).worksheet('services')
        services = sheet_services_local.get_all_records()
    except Exception as e:
        records, services = [], []
        flash(f'Lỗi khi tải dữ liệu: {e}')
        return redirect(url_for('index'))

    if request.method == 'POST':
        action = request.form.get('action')
        selected_id = request.form.get('package_id')

        # Tìm row index dựa trên ID
        all_rows = sheet_packages.get_all_records()
        row_index = None
        for i, row in enumerate(all_rows):
            if str(row['ID']) == str(selected_id):
                row_index = i + 2  # +2 vì sheet có header và index bắt đầu từ 1
                break

        if not row_index:
            flash('⚠️ Không tìm thấy gói khám.')
            return redirect(url_for('update_package'))

        if action == 'update':
            # Cập nhật thông tin

            name = request.form.get('name')
            description = request.form.get('description')
            price = request.form.get('price')
            services_ids = request.form.get('services', '')

            # Kiểm tra dữ liệu bắt buộc
            if not all([name, description, price]):
                flash('⚠️ Vui lòng điền đủ thông tin bắt buộc.')
                return redirect(url_for('update_package'))

            try:
                price = int(price)
            except ValueError:
                flash('⚠️ Giá tiền phải là số!')
                return redirect(url_for('update_package'))

            # Cập nhật sheet
            sheet_packages.update(f'B{row_index}:E{row_index}', [[name, description, price, services_ids]])

            flash('Cập nhật gói khám thành công!')
            return redirect(url_for('update_package'))
        elif action == 'delete':
            sheet_packages.delete_rows(row_index)
            flash('🗑️ Đã xóa gói khám!')
            return redirect(url_for('update_package'))

    return render_template('update_package.html', packages=records, services=services)









# ====== AUTO NGROK STARTUP ======
def start_ngrok():
    """Tự động khởi động ngrok"""
    try:
        public_url = ngrok.connect(5000)
        print(f"🌐 Public URL: {public_url}")
    except Exception as e:
        print(f"⚠️ Lỗi khi khởi động ngrok: {e}")

# ====== MAIN ======
if __name__ == '__main__':
    app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 0
    # start_ngrok()
    app.run(debug=True)