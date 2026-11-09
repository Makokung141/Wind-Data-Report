import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import matplotlib.patches as mpatches
import numpy as np
import gspread
from google.colab import auth
from google.auth import default
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from datetime import datetime
import time
import warnings

# ปิดแจ้งเตือน UserWarning
warnings.filterwarnings("ignore", category=UserWarning)

# ==========================================
# 1. ยืนยันตัวตน
# ==========================================
print("กำลังขอสิทธิ์เข้าถึง Google Drive และ Sheets...")
auth.authenticate_user()
creds, _ = default()
gc = gspread.authorize(creds)
print("✅ ยืนยันตัวตนสำเร็จ!\n")

# ==========================================
# 2. อ่านหน้า "Control"
# ==========================================
CONFIG_SHEET_NAME = 'Wind_Downtimeplot'
try:
    main_book = gc.open(CONFIG_SHEET_NAME)
except gspread.exceptions.SpreadsheetNotFound:
    raise ValueError(
        f"❌ ไม่พบ Google Sheet ที่ชื่อ '{CONFIG_SHEET_NAME}'.\n"
        "กรุณาตรวจสอบชื่อชีตให้ถูกต้อง หรือตรวจสอบสิทธิ์การเข้าถึงชีต."
    )
control_sheet = main_book.worksheet("Control")
df_control = pd.DataFrame(control_sheet.get_all_records())
df_control['Select'] = df_control['Select'].astype(str).str.upper() == 'TRUE'
selected_sites = df_control[df_control['Select'] == True]['Site'].tolist()

if not selected_sites:
    raise ValueError("❌ กรุณาเลือก Site ในหน้า Control")

# ==========================================
# 3. ดึงข้อมูล
# ==========================================
all_months_data = []
target_folder_url = ""
for site in selected_sites:
    print(f"\n📍 เริ่มดึงข้อมูลของ Site: {site}")
    try:
        site_sheet = main_book.worksheet(site)
        folder_cell_val = site_sheet.acell('F1').value
        if folder_cell_val: target_folder_url = folder_cell_val

        # 🌟 อ่านค่า Commissioning Date จากเซลล์ F4
        comm_date_val = site_sheet.acell('F4').value
        comm_date = pd.to_datetime(comm_date_val, errors='coerce', dayfirst=True)
        if pd.notna(comm_date):
            print(f"   📅 Commissioning Date ของ {site}: {comm_date.strftime('%d/%m/%Y')}")
        else:
            print(f"   ⚠️ ไม่พบหรือรูปแบบ Commissioning Date ในเซลล์ F4 ไม่ถูกต้อง (จะไม่ถูกใช้กรองเวลา)")

        all_data = site_sheet.get_all_values()
        if len(all_data) <= 1:
            continue

        headers = all_data[0]
        url_idx = headers.index('FileUrl') if 'FileUrl' in headers else 0
        tab_idx = headers.index('TabName') if 'TabName' in headers else 1

        for row in all_data[1:]:
            if len(row) >= 4:
                is_checked = str(row[3]).strip().upper()
            else:
                is_checked = "FALSE"

            if is_checked != 'TRUE':
                continue

            url = row[url_idx] if len(row) > url_idx else ''
            tab = row[tab_idx] if len(row) > tab_idx else ''

            if not url or not str(url).startswith("http"): continue

            print(f"⏳ กำลังดึงข้อมูลและวิเคราะห์จากชีต: {tab} ...")

            # 🌟 หน่วงเวลา 1 วินาทีเพื่อป้องกัน Google API ล่ม (Error 503)
            time.sleep(1)

            try:
                raw_data = gc.open_by_url(url).worksheet(str(tab)).get_all_values(value_render_option='UNFORMATTED_VALUE')

                if len(raw_data) > 1:
                    df_month = pd.DataFrame(raw_data[1:])

                    while df_month.shape[1] < 52:
                        df_month[df_month.shape[1]] = None

                    target_indices = [0, 2, 7, 12, 16, 20, 24, 28, 32, 36, 40, 44, 48, 52]
                    target_names = [
                        'Timestamp',
                        'WS160NW', 'WS160SE', 'WS137NW', 'WS100NW', 'WS80NW', 'WS60NW', 'WS40NW',
                        'P155', 'WD152NW', 'WD131NW', 'WD78NW', 'T155', 'R155'
                    ]

                    df_month = df_month.iloc[:, target_indices]
                    df_month.columns = target_names

                    ts_raw = df_month['Timestamp']

                    # 1. แปลงค่าตัวเลขดิบ พร้อมกรองช่วงเวลาป้องกัน Overflow
                    ts_num = pd.to_numeric(ts_raw, errors='coerce')
                    ts_num = ts_num.where((ts_num > 30000) & (ts_num < 80000))

                    try:
                        dates_from_num = pd.to_datetime(ts_num, unit='D', origin='1899-12-30', errors='coerce')
                    except:
                        dates_from_num = pd.Series([pd.NaT] * len(ts_raw))

                    # 2. แปลงกรณีเป็นข้อความ (รองรับรูปแบบ วัน/เดือน/ปี)
                    try:
                        dates_from_str = pd.to_datetime(ts_raw.astype(str), errors='coerce', dayfirst=True)
                    except:
                        dates_from_str = pd.Series([pd.NaT] * len(ts_raw))

                    # 3. รวมผลลัพธ์
                    df_month['Timestamp'] = dates_from_num.fillna(dates_from_str)

                    # 🌟 ตัดข้อมูลที่ก่อนหน้า Commissioning Date (ถ้ามีค่า F4 กำหนดไว้)
                    if pd.notna(comm_date):
                        df_month = df_month[df_month['Timestamp'] >= comm_date]

                    temp_time = df_month['Timestamp'].dropna()

                    if not temp_time.empty:
                        m_name = temp_time.dt.strftime('%B').iloc[0]
                        y_num = temp_time.dt.year.iloc[0]
                        print(f"   => ✅ สำเร็จ! (ข้อมูลที่กำลังดึงคือ: เดือน {m_name} ปี {y_num})")
                    else:
                        print(f"   => ✅ สำเร็จ! แต่ไม่พบข้อมูล Timestamp ที่ถูกต้องในชีต {tab} (หรือถูกกรองออกเพราะก่อน Commissioning Date)")

                    all_months_data.append(df_month)
            except Exception as e:
                print(f"   => ❌ ไม่สามารถดึงข้อมูลชีต {tab} ได้ ({e})")
                continue
    except Exception as e:
        print(f"❌ เกิดข้อผิดพลาดกับ Site {site}: {e}")
        continue

# ==========================================
# 4. ประมวลผล (คำนวณและแสดงผลแยกตามรายเดือน)
# ==========================================
if not all_months_data:
    raise ValueError("❌ ไม่พบข้อมูลที่ถูกเลือก กรุณาตรวจสอบให้แน่ใจว่าในชีตย่อยมีแถวที่คอลัมน์ D ถูกติ๊กถูกอยู่จริงๆ")

df_all_years = pd.concat(all_months_data, ignore_index=True)

df_all_years['Timestamp'] = pd.to_datetime(df_all_years['Timestamp'], errors='coerce')
df_all_years = df_all_years.dropna(subset=['Timestamp']).sort_values('Timestamp').reset_index(drop=True)

df_all_years = df_all_years[df_all_years['Timestamp'] <= pd.Timestamp.now()]

if df_all_years.empty:
    raise ValueError("❌ ไม่มีข้อมูลเวลาเหลืออยู่เลย (อาจเป็นข้อมูลในอนาคตทั้งหมด หรือวันที่ผิดพลาด หรือถูกกรองออกทั้งหมด)")

start_period = df_all_years['Timestamp'].min().strftime('%b %Y')
end_period = df_all_years['Timestamp'].max().strftime('%b %Y')
period_text = start_period if start_period == end_period else f"{start_period} - {end_period}"

print(f"\n=======================================================")
print(f"📊 สรุปภาพรวมช่วงเวลาทั้งหมด: {period_text}")
print(f"=======================================================\n")

# สร้างคอลัมน์สำหรับจัดกลุ่มรายเดือน
df_all_years['YearMonth'] = df_all_years['Timestamp'].dt.to_period('M')

ordered_sensors = [
    'WS160NW', 'WS160SE', 'WS137NW', 'WS100NW', 'WS80NW', 'WS60NW', 'WS40NW',
    'WD152NW', 'WD131NW', 'WD78NW',
    'T155', 'P155', 'R155'
]

print("📋 รายละเอียด % Recovery ของแต่ละ Sensor (แยกตามรายเดือน):")
print("-------------------------------------------------------")

monthly_summary_data = []

for ym, group in df_all_years.groupby('YearMonth'):
    month_name = ym.strftime('%B %Y')
    print(f"\n📅 เดือน: {month_name} (จำนวนข้อมูลทั้งหมด: {len(group):,} แถว)")

    for sensor in ordered_sensors:
        group[sensor] = pd.to_numeric(group[sensor], errors='coerce')
        valid = group[sensor].notna().sum()
        total = len(group)
        recovery = round((valid / total) * 100, 2) if total > 0 else 0.0

        print(f"   • {sensor:<10} ➔ {recovery:>6}%  (ใช้งานได้ {valid}/{total})")

        monthly_summary_data.append({
            'YearMonth': str(ym),
            'Sensor': sensor,
            'Recovery': recovery
        })

print("\n-------------------------------------------------------")

# คำนวณภาพรวมทั้งหมด (Global Summary) สำหรับนำไปใช้วาดกราฟ
sensor_summary = []
for sensor in ordered_sensors:
    df_all_years[sensor] = pd.to_numeric(df_all_years[sensor], errors='coerce')
    valid = df_all_years[sensor].notna().sum()
    recovery = round((valid / len(df_all_years)) * 100, 1)
    sensor_summary.append({'Sensor': sensor, 'Recovery': recovery})

df_summary = pd.DataFrame(sensor_summary)

# ==========================================
# 5. วาดกราฟ
# ==========================================
ROW_SPACING = 15
MIN_DOWNTIME_WIDTH_DAYS = 0.5

fig_height = max(10, len(df_summary) * 1.5)
fig, ax = plt.subplots(figsize=(16, fig_height), dpi=300)
times = df_all_years['Timestamp'].values
yticks, ylabels = [], []

for i, row in df_summary.iterrows():
    y = (len(df_summary) - i) * ROW_SPACING
    yticks.append(y)

    name = f"{row['Sensor']} [{row['Recovery']}%]"
    ylabels.append(name)

    stat = df_all_years[row['Sensor']].notna().astype(int).values
    c_start, c_v = None, None
    for j in range(len(stat)):
        if c_start is None:
            c_start, c_v = times[j], stat[j]
        elif stat[j] != c_v:
            dur = (times[j-1] - c_start) / np.timedelta64(1, 'D')

            y_min, y_height = y - 3.5, 7

            if c_v == 1:
                face_color = 'forestgreen' if row['Recovery'] >= 90 else '#2a445e'
            else:
                face_color = '#E0E0E0'

            plot_dur = dur
            if c_v != 1 and dur < MIN_DOWNTIME_WIDTH_DAYS:
                plot_dur = MIN_DOWNTIME_WIDTH_DAYS

            ax.broken_barh([(mdates.date2num(c_start), plot_dur)], (y_min, y_height), facecolors=face_color)
            c_start, c_v = times[j], stat[j]

    dur = (times[-1] - c_start) / np.timedelta64(1, 'D')
    y_min, y_height = y - 3.5, 7

    if c_v == 1:
        face_color = 'forestgreen' if row['Recovery'] >= 90 else '#2a445e'
    else:
        face_color = '#E0E0E0'

    plot_dur = dur
    if c_v != 1 and dur < MIN_DOWNTIME_WIDTH_DAYS:
        plot_dur = MIN_DOWNTIME_WIDTH_DAYS

    ax.broken_barh([(mdates.date2num(c_start), plot_dur)], (y_min, y_height), facecolors=face_color)

ax.set_xlim([df_all_years['Timestamp'].min(), df_all_years['Timestamp'].max()])

ax.set_title(f'Downtime Plot: {selected_sites[0]} ({period_text})', fontsize=24, pad=15, loc='left', color='#333333')

ax.set_yticks(yticks)
ax.set_yticklabels(ylabels, fontsize=16, color='#333333')
ax.xaxis.set_major_locator(mdates.MonthLocator())
ax.xaxis.set_major_formatter(mdates.DateFormatter('%b %Y'))
plt.xticks(rotation=90, fontsize=14, color='#333333')

ax.tick_params(axis='x', colors='#333333')
ax.tick_params(axis='y', colors='#333333')
for spine in ax.spines.values():
    spine.set_edgecolor('#333333')

ax.legend(handles=[
              mpatches.Patch(color='forestgreen', label='Recovery 90-100%'),
              mpatches.Patch(color='#2a445e', label='Recovery 80-90%'),
              mpatches.Patch(color='#E0E0E0', label='Missing Data (Downtime)')],
          loc='upper center', bbox_to_anchor=(0.5, -0.1), ncol=3, fontsize=16, frameon=False, labelcolor='#333333')

plt.tight_layout()
plt.savefig('downtime_plot_output.png', bbox_inches='tight', transparent=False, facecolor='white')
plt.close()

# ==========================================
# 6. อัปโหลด
# ==========================================
drive_service = build('drive', 'v3', credentials=creds)
f_id = target_folder_url.split("folders/")[1].split("?")[0] if "folders/" in target_folder_url else None
meta = {'name': f'downtime_plot_{selected_sites[0]}.png', 'parents': [f_id]} if f_id else {'name': f'downtime_plot_{selected_sites[0]}.png'}
drive_service.files().create(body=meta, media_body=MediaFileUpload('downtime_plot_output.png')).execute()
print(f"🎉 อัปโหลดกราฟของช่วง {period_text} ไปยัง Google Drive สำเร็จ!")
