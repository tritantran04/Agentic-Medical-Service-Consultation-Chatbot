# import json
# from typing import List, Dict

# # with open("service_agent/updated_services.json", 'r', encoding='utf-8') as f:
# #     DATA = json.load(f)

# # ====== Google Sheet Setup ======
# scopes = ['https://www.googleapis.com/auth/spreadsheets']
# creds = Credentials.from_service_account_file('credentials.json', scopes=scopes)
# client = gspread.authorize(creds)
# app.config['TEMPLATES_AUTO_RELOAD'] = True

# SHEET_ID = '1tA48K1GlPuRkJ_9iGnoZaVcaNSgBA7-CsSwtMKUXG1A'  # thay bằng sheet ID của bạn
# sheet_packages = client.open_by_key(SHEET_ID).worksheet('packages')
# sheet_services = client.open_by_key(SHEET_ID).worksheet('services')


# with open("service_agent/updated_services.json", 'r', encoding='utf-8') as f:
#     DATA = json.load(f)

# def load_packages(data) -> Dict[int, Dict]:
#     packages = data["packages"]
#     # only take id, name, description, price
#     packages = [{k: v for k, v in pkg.items() if k in ["id", "name", "description", "price", "services_ids"]} for pkg in packages]
#     return packages

# PACKAGES = load_packages(DATA)

import gspread
from google.oauth2.service_account import Credentials
from typing import List, Dict

# ====== GOOGLE SHEET SETUP ======
SHEET_ID = '1tA48K1GlPuRkJ_9iGnoZaVcaNSgBA7-CsSwtMKUXG1A'  # <-- thay bằng sheet thật của bạn
SCOPES = ['https://www.googleapis.com/auth/spreadsheets']

creds = Credentials.from_service_account_file("service_agent/credentials.json", scopes=SCOPES)
client = gspread.authorize(creds)

sheet_packages = client.open_by_key(SHEET_ID).worksheet('packages')
sheet_services = client.open_by_key(SHEET_ID).worksheet('services')

print("✅ Kết nối thành công Google Sheet")

# ====== ĐỌC DỮ LIỆU TỪ GOOGLE SHEET ======
def load_data_from_sheet() -> Dict:
    """Đọc toàn bộ dữ liệu từ hai sheet packages & services."""
    packages = sheet_packages.get_all_records()
    services = sheet_services.get_all_records()

    # Chuyển ID và services_ids về đúng kiểu dữ liệu
    for pkg in packages:
        pkg["ID"] = int(pkg["ID"]) if pkg.get("ID") else None
        # Tách chuỗi '1, 2, 3' → [1,2,3]
        if isinstance(pkg.get("services_ids"), str):
            pkg["services_ids"] = [int(s.strip()) for s in pkg["services_ids"].split(",") if s.strip().isdigit()]
        else:
            pkg["services_ids"] = []
    for s in services:
        s["ID"] = int(s["ID"]) if s.get("ID") else None

    return {"packages": packages, "services": services}

DATA = load_data_from_sheet()


# ====== GIỐNG CODE GỐC CỦA BẠN (chỉ bỏ đọc file JSON) ======
def load_packages(data: Dict) -> List[Dict]:
    packages = data["packages"]
    # Lọc các cột cần thiết
    packages = [
        {k: v for k, v in pkg.items() if k in ["ID", "Name", "Description", "Price", "services_ids"]}
        for pkg in packages
    ]
    return packages

PACKAGES = load_packages(DATA)

def convert_packages_to_str(packages: List[Dict]) -> str:
    pkg_strs = []
    for pkg in packages:
        pkg_str = (
            f"ID: {pkg.get('ID', '(không có ID)')}\n"
            f"Mô tả: {pkg.get('Description', '(không có mô tả)')}\n"
            f"Giá: {pkg.get('Price', 0):,} VND"
        )
        pkg_strs.append(pkg_str)
    return "\n\n".join(pkg_strs)

def get_package_by_id(data: Dict = DATA, ids: List[int] = []) -> str:
    packages = data.get("packages", [])
    services = data.get("services", [])

    selected = []
    for pkg in packages:
        if pkg.get("ID") in ids:
            # Chuyển chuỗi dịch vụ thành list
            raw_services = pkg.get("Services", "")
            if isinstance(raw_services, str):
                try:
                    service_ids = [int(s.strip()) for s in raw_services.split(",") if s.strip()]
                except ValueError:
                    service_ids = []
            else:
                service_ids = raw_services or []

            # Lấy tên dịch vụ đi kèm
            service_names = []
            for sid in service_ids:
                if 0 < sid <= len(services):
                    service_names.append(services[sid - 1].get("Name", f"Dịch vụ {sid}"))

            pkg_str = (
                f"ID: {pkg.get('ID')}\n"
                f"Tên gói: {pkg.get('Name')}\n"
                f"Mô tả: {pkg.get('Description')}\n"
                f"Giá: {pkg.get('Price'):,} VND\n"
                f"Dịch vụ đi kèm:\n  - " + "\n  - ".join(service_names) + "\n"
            )
            selected.append(pkg_str)

    return "\n\n".join(selected)

# TEST
if __name__ == "__main__":
    print("📦 Danh sách gói khám:")
    print(convert_packages_to_str(PACKAGES))

    print("\n🔍 Thông tin gói có ID = 1:")
    print(get_package_by_id(DATA, [2]))