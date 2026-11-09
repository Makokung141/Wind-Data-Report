from datetime import datetime
import math
import gspread
from google.auth import default
from google.colab import auth
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
import matplotlib.dates as mdates
import matplotlib.patches as mpatches
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import re

# 1. ยืนยันตัวตน
print("กำลังขอสิทธิ์เข้าถึง Google Drive และ Sheets...")
auth.authenticate_user()
creds, _ = default()
gc = gspread.authorize(creds)
print("✅ ยืนยันตัวตนสำเร็จ!\n")

# 2. อ่านหน้า "Control"
CONFIG_SHEET_NAME = "Wind_Downtimeplot"
try:
    main_book = gc.open(CONFIG_SHEET_NAME)
except gspread.exceptions.SpreadsheetNotFound:
    raise ValueError(
        f"❌ ไม่พบ Google Sheet ที่ชื่อ '{CONFIG_SHEET_NAME}'.\n"
        "กรุณาตรวจสอบชื่อชีตให้ถูกต้อง หรือตรวจสอบสิทธิ์การเข้าถึงชีต."
    )
control_sheet = main_book.worksheet("Control")
df_control = pd.DataFrame(control_sheet.get_all_records())
df_control["Select"] = df_control["Select"].astype(str).str.upper() == "TRUE"
selected_sites = df_control[df_control["Select"] == True]["Site"].tolist()

if not selected_sites:
    raise ValueError("❌ กรุณาเลือก Site ในหน้า Control")

# 3. ดึงข้อมูล
all_months_data = []
monthly_report_data = []  # สำหรับเก็บข้อมูลไปแสดงรายงานรายเดือน
target_folder_url = ""

for site in selected_sites:
    print(f"\n📍 เริ่มดึงข้อมูลของ Site: {site}")
    try:
        site_sheet = main_book.worksheet(site)
        folder_cell_val = site_sheet.acell("F1").value
        if folder_cell_val:
            target_folder_url = folder_cell_val

        # Read commissioning date from F4 of the site_sheet
        commissioning_date_str = site_sheet.acell("F4").value
        commissioning_date = None
        if commissioning_date_str:
            try:
                commissioning_date = pd.to_datetime(
                    commissioning_date_str, format="%d/%m/%Y", errors="coerce"
                )
                print(f"   📅 Commissioning Date ของ {site}: {commissioning_date_str}")
            except Exception as date_err:
                print(
                    f"   => ⚠️ Warning: ไม่สามารถแปลง Commisioning Date"
                    f" '{commissioning_date_str}' จาก F4 ของ Site {site} ได้"
                    f" ({date_err}). จะไม่ใช้การกรองข้อมูลด้วยวันที่ Commisioning."
                )
                commissioning_date = None

        all_data = site_sheet.get_all_values()
        if len(all_data) <= 1:
            continue

        headers = all_data[0]
        url_idx = headers.index("FileUrl") if "FileUrl" in headers else 0
        tab_idx = headers.index("TabName") if "TabName" in headers else 1

        for row in all_data[1:]:
            if len(row) >= 4:
                is_checked = str(row[3]).strip().upper()
            else:
                is_checked = "FALSE"

            if is_checked != "TRUE":
                continue

            url = row[url_idx] if len(row) > url_idx else ""
            tab = row[tab_idx] if len(row) > tab_idx else ""

            if not url or not str(url).startswith("http"):
                continue

            print(f"⏳ กำลังดึงข้อมูลและวิเคราะห์จากชีต: {tab} ...")

            try:
                df_month = pd.DataFrame(
                    gc.open_by_url(url).worksheet(str(tab)).get_all_records()
                )

                # แทนที่ค่าที่เป็นช่องว่าง ('', ' ', ฯลฯ) ให้เป็น NaN เพื่อไม่ให้นับเป็น Clean Data
                df_month.replace(r'^\s*$', np.nan, regex=True, inplace=True)

                # Convert 'Timestamp' to datetime early for consistent filtering
                df_month["Timestamp"] = pd.to_datetime(
                    df_month["Timestamp"], format="%d/%m/%Y, %H:%M:%S", errors="coerce"
                )
                df_month = df_month.dropna(subset=["Timestamp"])

                # Filter df_month by commissioning_date if it's valid
                if commissioning_date is not None and not pd.isna(commissioning_date):
                    original_rows = len(df_month)
                    df_month = df_month[df_month["Timestamp"] >= commissioning_date]
                    if len(df_month) < original_rows:
                        print(
                            f"   => ℹ️ กรองข้อมูลเดือน {tab} ก่อนวันที่ Commisioning Date"
                            f" ({commissioning_date.strftime('%Y-%m-%d')}) ออกไปแล้ว."
                            f" {original_rows - len(df_month)} แถวถูกกรองออก."
                        )

                temp_time = df_month["Timestamp"].dropna()
                if not temp_time.empty:
                    m_name = temp_time.dt.strftime("%B").iloc[0]
                    y_num = temp_time.dt.year.iloc[0]
                    month_year_str = temp_time.dt.strftime("%B %Y").iloc[0]

                    print(f"   => ✅ สำเร็จ! (ข้อมูลที่กำลังดึงคือ: เดือน {m_name} ปี {y_num})")

                    monthly_report_data.append({
                        "month_year": month_year_str,
                        "df": df_month.copy()
                    })
                else:
                    print(
                        f"   => ✅ สำเร็จ! แต่ไม่พบข้อมูล Timestamp ที่ถูกต้องในชีต {tab}"
                        " หลังจากกรองด้วย Commisioning Date"
                    )

                all_months_data.append(df_month)
            except Exception as e:
                print(f"   => ❌ ไม่สามารถดึงข้อมูลชีต {tab} ได้ ({e})")
                continue
    except Exception as e:
        print(f"❌ เกิดข้อผิดพลาดกับ Site {site}: {e}")
        continue

# 4. ประมวลผลภาพรวม
if not all_months_data:
    raise ValueError(
        "❌ ไม่พบข้อมูลที่ถูกเลือก กรุณาตรวจสอบให้แน่ใจว่าในชีตย่อยมีแถวที่คอลัมน์ D ถูกติ๊กถูกอยู่จริงๆ"
    )

df_all_years = pd.concat(all_months_data, ignore_index=True)
df_all_years["Timestamp"] = pd.to_datetime(
    df_all_years["Timestamp"], format="%d/%m/%Y, %H:%M:%S", errors="coerce"
)
df_all_years = (
    df_all_years.dropna(subset=["Timestamp"])
    .sort_values("Timestamp")
    .reset_index(drop=True)
)

# กรองข้อมูลเอาเฉพาะข้อมูลที่ถึงแค่เวลาปัจจุบัน
df_all_years = df_all_years[df_all_years["Timestamp"] <= pd.Timestamp.now()]

# --- หาวันที่เริ่มต้นและสิ้นสุด เพื่อนำไปแสดงผล ---
start_period = df_all_years["Timestamp"].min().strftime("%b %Y")
end_period = df_all_years["Timestamp"].max().strftime("%b %Y")
period_text = (
    start_period if start_period == end_period else f"{start_period} - {end_period}"
)

print(f"\n=======================================================")
print(f"📊 สรุปภาพรวมช่วงเวลาทั้งหมด: {period_text}")
print(f"=======================================================\n")

print(f"📋 รายละเอียด % Recovery ของแต่ละ Sensor (แยกตามรายเดือน):")
print(f"-------------------------------------------------------")

# ฟังก์ชันช่วยหา Sensor info (เรียกใช้ซ้ำได้)
def get_sensor_info(full_name):
    name_upper = full_name.upper()
    stype, height, suffix = "OTHER", 0.0, ""
    try:
        height_match = re.search(r"(\d+(\.\d+)?)[mM]", full_name)
        if height_match:
            height = float(height_match.group(1))

        if "ANEM" in name_upper or "WS" in name_upper:
            stype = "WS"
        elif "VANE" in name_upper or "WD" in name_upper:
            stype = "WD"
        elif "RH" in name_upper:
            stype = "RH"
        elif "MB" in name_upper or ("P" in name_upper and not any(s in name_upper for s in ["ANEM", "WS", "VANE", "WD", "RH", "C", "T", "ANALOG"])):
            stype = "P"
        elif "C" in name_upper or "T" in name_upper or "ANALOG" in name_upper:
            stype = "T"

        if stype not in ["P", "T", "RH"]:
            parts = full_name.split("_")
            for part in parts:
                if part.upper() in [
                    "N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
                    "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW",
                ]:
                    suffix = part.upper()
                    break
    except Exception:
        pass
    return stype, height, suffix

def get_base_sensor_key(col_name):
    stype, height, suffix = get_sensor_info(col_name)
    if stype == "OTHER":
        cleaned = col_name.strip()
        cleaned = re.sub(r"^(ch\d+|channel\d+|c\d+)[-_.\s]*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"[-_.\s]*(ch\d+|channel\d+|c\d+)$", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"[-_.\s]*(ch|channel|c)$", "", cleaned, flags=re.IGNORECASE)
        return cleaned
    rounded_height = int(round(height))
    key = f"{stype}_{rounded_height}m"
    if suffix:
        key += f"_{suffix}"
    return key

# แสดงรายงานรายเดือนตามรูปแบบที่ต้องการ
for item in monthly_report_data:
    m_str = item["month_year"]
    df_m = item["df"]
    total_rows = len(df_m)

    print(f"\n📅 เดือน: {m_str} (จำนวนข้อมูลทั้งหมด: {total_rows:,} แถว)")

    m_sensor_cols = [col for col in df_m.columns if col != "Timestamp" and "Avg" in col]

    m_sensor_groups = {}
    for sensor in m_sensor_cols:
        b_key = get_base_sensor_key(sensor)
        if b_key not in m_sensor_groups:
            m_sensor_groups[b_key] = []
        m_sensor_groups[b_key].append(sensor)

    for b_key, cols in m_sensor_groups.items():
        df_v_mat = pd.DataFrame()
        for col in cols:
            df_v_mat[col] = pd.to_numeric(df_m[col], errors="coerce").notna().astype(int)

        valid_cnt = df_v_mat.max(axis=1).sum()
        rec_pct = (valid_cnt / total_rows * 100) if total_rows > 0 else 0.0

        st, h, sfx = get_sensor_info(b_key)
        sensor_display_name = f"{st}{int(math.ceil(h))}{sfx}"

        print(f"- {sensor_display_name.ljust(10)}   ➔   {rec_pct:5.1f}%   (ใช้งานได้ {valid_cnt:,}/{total_rows:,})")

print("\n" + "="*55 + "\n")

sensor_columns = [
    col for col in df_all_years.columns if col != "Timestamp" and "Avg" in col
]

# --- จัดกลุ่มคอลัมน์เซ็นเซอร์ (ภาพรวมทั้งหมด) ---
sensor_groups = {}
for sensor in sensor_columns:
    base_key = get_base_sensor_key(sensor)
    if base_key not in sensor_groups:
        sensor_groups[base_key] = []
    sensor_groups[base_key].append(sensor)

sensor_summary = []
group_stats = {}

for base_key, cols in sensor_groups.items():
    df_valid_matrix = pd.DataFrame()
    for col in cols:
        df_valid_matrix[col] = (
            pd.to_numeric(df_all_years[col], errors="coerce").notna().astype(int)
        )

    combined_stat = df_valid_matrix.max(axis=1).values
    valid_count = combined_stat.sum()

    # 📌 ปรับเปลี่ยนเป็นการคำนวณ % Recovery ภาพรวมให้เป็นทศนิยม 1 ตำแหน่ง
    recovery = (valid_count / len(df_all_years) * 100) if len(df_all_years) > 0 else 0.0

    sensor_summary.append({"Sensor_Key": base_key, "Recovery": recovery})
    group_stats[base_key] = {"stat": combined_stat, "recovery": recovery}


df_summary = pd.DataFrame(sensor_summary)
df_summary["Type"], df_summary["H"], df_summary["Suffix"] = zip(
    *df_summary["Sensor_Key"].apply(get_sensor_info)
)
df_summary["Type"] = pd.Categorical(
    df_summary["Type"], categories=["WS", "WD", "T", "P", "RH"], ordered=True
)
df_summary = df_summary.sort_values(
    by=["Type", "H", "Suffix"], ascending=[False, True, False]
).reset_index(drop=True)


# 5. วาดกราฟ
ROW_SPACING = 15
MIN_DOWNTIME_WIDTH_DAYS = 0.5  # ความกว้างขั้นต่ำของช่องโหว่

fig_height = max(10, len(df_summary) * 1.5)
fig, ax = plt.subplots(figsize=(16, fig_height), dpi=300)
times = df_all_years["Timestamp"].values
yticks, ylabels = [], []

for i, row in df_summary.iterrows():
    y = i * ROW_SPACING
    yticks.append(y)

    st, h, sfx = get_sensor_info(row["Sensor_Key"])
    # 📌 แสดงผลค่า % Recovery เป็นทศนิยม 1 ตำแหน่งบนแกน Y ของกราฟ
    name = f"{st}{int(math.ceil(h))}{sfx} [{row['Recovery']:.1f}%]"
    ylabels.append(name)

    stat = group_stats[row["Sensor_Key"]]["stat"]
    c_start, c_v = None, None
    for j in range(len(stat)):
        if c_start is None:
            c_start, c_v = times[j], stat[j]
        elif stat[j] != c_v:
            dur = (times[j - 1] - c_start) / np.timedelta64(1, "D")

            y_min, y_height = y - 3.5, 7

            if c_v == 1:
                face_color = "forestgreen" if row["Recovery"] >= 90 else "#2a445e"
            else:
                face_color = "#E0E0E0"

            plot_dur = dur
            if c_v != 1 and dur < MIN_DOWNTIME_WIDTH_DAYS:
                plot_dur = MIN_DOWNTIME_WIDTH_DAYS

            ax.broken_barh(
                [(mdates.date2num(c_start), plot_dur)],
                (y_min, y_height),
                facecolors=face_color,
            )
            c_start, c_v = times[j], stat[j]

    dur = (times[-1] - c_start) / np.timedelta64(1, "D")
    y_min, y_height = y - 3.5, 7

    if c_v == 1:
        face_color = "forestgreen" if row["Recovery"] >= 90 else "#2a445e"
    else:
        face_color = "#E0E0E0"

    plot_dur = dur
    if c_v != 1 and dur < MIN_DOWNTIME_WIDTH_DAYS:
        plot_dur = MIN_DOWNTIME_WIDTH_DAYS

    ax.broken_barh(
        [(mdates.date2num(c_start), plot_dur)],
        (y_min, y_height),
        facecolors=face_color,
    )

ax.set_xlim([df_all_years["Timestamp"].min(), df_all_years["Timestamp"].max()])

ax.set_title(
    f"Downtime Plot: {selected_sites[0]} ({period_text})",
    fontsize=24,
    pad=15,
    loc="left",
    color="#333333",
)

ax.set_yticks(yticks)
ax.set_yticklabels(ylabels, fontsize=16, color="#333333")
ax.xaxis.set_major_locator(mdates.MonthLocator())
ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %Y"))
plt.xticks(rotation=90, fontsize=14, color="#333333")

ax.tick_params(axis="x", colors="#333333")
ax.tick_params(axis="y", colors="#333333")
for spine in ax.spines.values():
    spine.set_edgecolor("#333333")

ax.legend(
    handles=[
        mpatches.Patch(color="forestgreen", label="Recovery 90-100%"),
        mpatches.Patch(color="#2a445e", label="Recovery 80-90%"),
        mpatches.Patch(color="#E0E0E0", label="Missing Data (Downtime)"),
    ],
    loc="upper center",
    bbox_to_anchor=(0.5, -0.1),
    ncol=3,
    fontsize=16,
    frameon=False,
    labelcolor="#333333",
)

plt.tight_layout()
plt.savefig(
    "downtime_plot_output.png",
    bbox_inches="tight",
    transparent=False,
    facecolor="white",
)
plt.close()

# 6. อัปโหลด
drive_service = build("drive", "v3", credentials=creds)
f_id = (
    target_folder_url.split("folders/")[1].split("?")[0]
    if "folders/" in target_folder_url
    else None
)
meta = (
    {"name": f"downtime_plot_{selected_sites[0]}.png", "parents": [f_id]}
    if f_id
    else {"name": f"downtime_plot_{selected_sites[0]}.png"}
)
drive_service.files().create(
    body=meta, media_body=MediaFileUpload("downtime_plot_output.png")
).execute()
print(f"🎉 อัปโหลดกราฟของช่วง {period_text} ไปยัง Google Drive สำเร็จ!")
