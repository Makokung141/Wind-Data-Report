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

                # Convert 'Timestamp' to datetime early for consistent filtering
                df_month["Timestamp"] = pd.to_datetime(
                    df_month["Timestamp"], format="%d/%m/%Y, %H:%M:%S", errors="coerce"
                )
                df_month = df_month.dropna(
                    subset=["Timestamp"]
                )  # Drop rows where Timestamp couldn't be parsed

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
                    print(f"   => ✅ สำเร็จ! (ข้อมูลที่กำลังดึงคือ: เดือน {m_name} ปี {y_num})")
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

# 4. ประมวลผล
if not all_months_data:
    raise ValueError(
        "❌ ไม่พบข้อมูลที่ถูกเลือก กรุณาตรวจสอบให้แน่ใจว่าในชีตย่อยมีแถวที่คอลัมน์"
        " D ถูกติ๊กถูกอยู่จริงๆ"
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

# กรองข้อมูลเอาเฉพาะข้อมูลที่ถึงแค่เวลาปัจจุบัน (ป้องกันข้อมูลอนาคตหลุดมา)
df_all_years = df_all_years[df_all_years["Timestamp"] <= pd.Timestamp.now()]

# --- หาวันที่เริ่มต้นและสิ้นสุด เพื่อนำไปแสดงผล ---
start_period = df_all_years["Timestamp"].min().strftime("%b %Y")
end_period = df_all_years["Timestamp"].max().strftime("%b %Y")
period_text = (
    start_period
    if start_period == end_period
    else f"{start_period} - {end_period}"
)
print(f"\n=======================================================")
print(f"📊 สรุป: ข้อมูลทั้งหมดที่นำมาวิเคราะห์คือช่วงเวลา {period_text}")
print(f"=======================================================\n")
# -----------------------------------------------

sensor_columns = [
    col for col in df_all_years.columns if col != "Timestamp" and "Avg" in col
]

# --- 1. ฟังก์ชัน get_sensor_info ดึงข้อมูลประเภท ความสูง และทิศทาง ---
def get_sensor_info(full_name):
    name_upper = full_name.upper()
    stype, height, suffix = "OTHER", 0.0, ""
    try:
        # ค้นหาความสูงจากตัวเลขที่ตามด้วย m หรือ M
        height_match = re.search(r"(\d+(\.\d+)?)[mM]", full_name)
        if height_match:
            height = float(height_match.group(1))

        # ตรวจสอบประเภทเซ็นเซอร์
        if "ANEM" in name_upper or "WS" in name_upper:
            stype = "WS"
        elif "VANE" in name_upper or "WD" in name_upper:
            stype = "WD"
        elif "RH" in name_upper:
            stype = "RH"
        # Modified: This is the updated condition for 'P' type sensors
        elif "MB" in name_upper or ("P" in name_upper and not any(s in name_upper for s in ["ANEM", "WS", "VANE", "WD", "RH", "C", "T", "ANALOG"])):
            stype = "P"
        elif "C" in name_upper or "T" in name_upper or "ANALOG" in name_upper:
            stype = "T"

        # ดึงทิศทางหรือ Suffix ท้ายชื่อ (ยกเว้น P, T, RH)
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

# --- 2. ฟังก์ชันจัดกลุ่มเซ็นเซอร์โดยปัดเศษความสูง ---
def get_base_sensor_key(col_name):
    stype, height, suffix = get_sensor_info(col_name)

    # ถ้าระบุประเภทไม่ได้ (OTHER) ให้ใช้ Fallback ตัดแค่รหัส Channel ออกตามโค้ดเดิม
    if stype == "OTHER":
        cleaned = col_name.strip()
        cleaned = re.sub(r"^(ch\d+|channel\d+|c\d+)[-_.\s]*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"[-_.\s]*(ch\d+|channel\d+|c\d+)$", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"[-_.\s]*(ch|channel|c)$", "", cleaned, flags=re.IGNORECASE)
        return cleaned

    # ถ้าตรวจพบชนิดความสูง ให้ทำการปัดเศษความสูงเป็นจำนวนเต็ม (เช่น 159.80 -> 160)
    rounded_height = int(round(height))

    # สร้าง Key กลางใหม่เพื่อจัดกลุ่ม (เช่น "WS_160m_N")
    key = f"{stype}_{rounded_height}m"
    if suffix:
        key += f"_{suffix}"

    return key


# --- 3. จัดกลุ่มคอลัมน์เซ็นเซอร์ ---
sensor_groups = {}
for sensor in sensor_columns:
    base_key = get_base_sensor_key(sensor)
    if base_key not in sensor_groups:
        sensor_groups[base_key] = []
    sensor_groups[base_key].append(sensor)

sensor_summary = []
group_stats = {}

for base_key, cols in sensor_groups.items():
    # สร้าง DataFrame ตรวจสอบความถูกต้องของแต่ละแชนเนลในกลุ่ม
    df_valid_matrix = pd.DataFrame()
    for col in cols:
        df_valid_matrix[col] = (
            pd.to_numeric(df_all_years[col], errors="coerce").notna().astype(int)
        )

    # ใช้ Logical OR (หากแชนเนลใดแชนเนลหนึ่งมีข้อมูลใช้งานได้ จะถือว่าช่วงเวลานั้นใช้งานได้ปกติ)
    combined_stat = df_valid_matrix.max(axis=1).values
    valid_count = combined_stat.sum()
    recovery = math.ceil((valid_count / len(df_all_years)) * 100)

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
    name = f"{st}{int(math.ceil(h))}{sfx} [{row['Recovery']:.2f}%]"
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

    # แท่งสุดท้ายของลูป
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

# --- ล็อคแกน X ไม่ให้แสดงผลเกินเดือนล่าสุดของข้อมูล ---
ax.set_xlim([df_all_years["Timestamp"].min(), df_all_years["Timestamp"].max()])
# ------------------------------------------------

# --- จัดการสีของข้อความและเส้นต่างๆ ให้เป็นสีเข้ม (ตัดกับพื้นขาว) ---
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

# --- อัปเดต Legend ---
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
# เปลี่ยนพื้นหลังรูปภาพเป็นสีขาว
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
