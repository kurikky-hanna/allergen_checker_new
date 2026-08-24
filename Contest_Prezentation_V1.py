import calendar
import hashlib
import os
import re
import sqlite3
import sys
from datetime import datetime
import pandas as pd
import pdfplumber
import streamlit as st


def get_writable_path(filename):
    if getattr(sys, "frozen", False):
        base_dir = os.path.dirname(sys.executable)
    else:
        base_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base_dir, filename)


DB_PATH = get_writable_path("menu_data_V1.db")


# ===== DB 初期化 =====
def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS allergen (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT,
        lang TEXT,
        details TEXT
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS menu_allergens (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        date TEXT,
        dish TEXT,
        allergen TEXT
    )
    """)
    conn.commit()
    conn.close()


def clear_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("DELETE FROM allergen")
    cur.execute("DELETE FROM menu_allergens")
    conn.commit()
    conn.close()


# ===== アレルゲン列 =====
correct_columns = [
    "料理名/食品名",
    "分類",
    "卵",
    "乳",
    "小麦",
    "そば",
    "落花生",
    "えび",
    "かに",
    "くるみ",
    "アーモンド",
    "あわび",
    "いか",
    "いくら",
    "オレンジ",
    "カシューナッツ",
    "キウイ",
    "牛肉",
    "ごま",
    "さけ",
    "さば",
    "大豆",
    "鶏肉",
    "バナナ",
    "豚肉",
    "マカダミアナッツ",
    "もも",
    "やまいも",
    "りんご",
    "ゼラチン",
]

allergen_headers = correct_columns[2:]


# ===== table_13 作成 =====
def save_table13(all_dfs):
    try:
        dfs_filtered = [
            d for d in all_dfs if len(d.columns) == len(correct_columns)
        ]
        for d in dfs_filtered:
            d.columns = correct_columns

        if not dfs_filtered:
            return False

        df = pd.concat(dfs_filtered, ignore_index=True)
        conn = sqlite3.connect(DB_PATH)
        df.to_sql("table_13", conn, if_exists="replace", index=False)
        conn.close()
        return True
    except Exception:
        return False


# ===== PDF解析 =====
def build_date_dish_map(pdf_path, all_dfs):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    WEEKDAYS = ["月", "火", "水", "木", "金", "土", "日"]
    try:
        with pdfplumber.open(pdf_path) as pdf:
            for page_num, page in enumerate(pdf.pages):
                text = page.extract_text()
                if not text:
                    continue

                year = datetime.now().year
                year_match = re.search(r"(20\d{2})\s*年", text)
                if year_match:
                    year = int(year_match.group(1))
                else:
                    reiwa_match = re.search(r"令和\s*(\d+|元)\s*年", text)
                    if reiwa_match:
                        r_num = (
                            1
                            if reiwa_match.group(1) == "元"
                            else int(reiwa_match.group(1))
                        )
                        year = 2018 + r_num

                date_match = re.search(r"(\d+)月(\d+)日", text)
                if not date_match:
                    continue

                month = int(date_match.group(1))
                day = int(date_match.group(2))

                try:
                    dt = datetime(year, month, day)
                    w_str = WEEKDAYS[dt.weekday()]
                    date_str = f"{month}/{day}({w_str})"
                except ValueError:
                    date_str = f"{month}/{day}"

                if page_num >= len(all_dfs):
                    continue

                df_page = all_dfs[page_num]
                if df_page.shape[1] != len(correct_columns):
                    continue

                df_page.columns = correct_columns

                for _, row in df_page.iterrows():
                    dish = row["料理名/食品名"]
                    if pd.isna(dish):
                        continue

                    dish_str = str(dish).strip().replace("\n", "")
                    if any(k in dish_str for k in ["料理名", "食品名", "品名"]):
                        continue

                    dish_str = re.sub(r"【\s*特例\s*[:：][^】]*】", "", dish_str)
                    dish_str = re.sub(r"[（\(][^）\)]*[）\)]", "", dish_str)
                    dish_str = re.split(r"[／/]", dish_str)[0].strip()

                    if dish_str == "":
                        continue

                    amount = row["分類"]
                    if not pd.isna(amount):
                        try:
                            float(str(amount).strip())
                            continue
                        except ValueError:
                            pass

                    for allergen in allergen_headers:
                        cell = (
                            str(row.get(allergen, ""))
                            .strip()
                            .replace("\n", "")
                        )
                        if any(
                            mark in cell for mark in ["○", "▲", "☒", "O", "0"]
                        ):
                            cur.execute(
                                "INSERT INTO menu_allergens (date, dish, allergen) VALUES (?, ?, ?)",
                                (date_str, dish_str, allergen),
                            )
    except Exception as e:
        print(f"❌ エラー: {e}")
    finally:
        conn.commit()
        conn.close()


def insert_allergens_from_table1():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    for allergen in allergen_headers:
        try:
            cur.execute(
                f"SELECT COUNT(*) FROM table_13 WHERE `{allergen}` LIKE '%○%' OR `{allergen}` LIKE '%▲%'"
            )
            if cur.fetchone()[0] > 0:
                cur.execute(
                    "SELECT COUNT(*) FROM allergen WHERE name=? AND lang='ja'",
                    (allergen,),
                )
                if not cur.fetchone()[0]:
                    cur.execute(
                        "INSERT INTO allergen (name, lang, details) VALUES (?, 'ja', '')",
                        (allergen,),
                    )
                    cur.execute(
                        "INSERT INTO allergen (name, lang, details) VALUES (?, 'en', '')",
                        (allergen,),
                    )
        except Exception as e:
            print(f"⚠️ エラー: {e}")
    conn.commit()
    conn.close()


def process_pdf(pdf_path):
    init_db()
    clear_db()

    all_dfs = []
    try:
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                tables = page.extract_tables()
                for table in tables:
                    if table:
                        all_dfs.append(pd.DataFrame(table))
    except Exception as e:
        print(f"❌ エラー: {e}")
        return

    if not save_table13(all_dfs):
        return

    insert_allergens_from_table1()
    build_date_dish_map(pdf_path, all_dfs)


# ===== Streamlit UI =====
st.set_page_config(
    page_title="給食アレルゲン調査機", page_icon="🍔", layout="centered"
)

st.title("給食アレルゲン調査機（Streamlit版）")
uploaded = st.file_uploader("PDFを選んでください", type=["pdf"])

if uploaded:
    file_bytes = uploaded.getbuffer()
    file_hash = hashlib.md5(file_bytes).hexdigest()

    if (
        "processed_hash" not in st.session_state
        or st.session_state["processed_hash"] != file_hash
    ):
        temp_pdf_path = get_writable_path("temp_uploaded.pdf")
        with open(temp_pdf_path, "wb") as f:
            f.write(file_bytes)

        with st.spinner("PDFを解析中...少々お待ちください"):
            process_pdf(temp_pdf_path)

        st.session_state["processed_hash"] = file_hash

    conn = sqlite3.connect(DB_PATH)
    df_dates = pd.read_sql(
        "SELECT DISTINCT date FROM menu_allergens ORDER BY date", conn
    )
    df_allergen = pd.read_sql(
        "SELECT DISTINCT name FROM allergen WHERE lang='ja'", conn
    )

    detected_month = 7
    if not df_dates.empty:
        month_match = re.search(r"(\d+)/", df_dates["date"].iloc[0])
        if month_match:
            detected_month = int(month_match.group(1))

    st.write("---")
    selected_allergens = st.multiselect(
        "⚠️ カレンダーで警戒したいアレルゲンを選択してください（ボタンの色が変わります）",
        df_allergen["name"].tolist(),
        placeholder="アレルゲンを選択...",
    )

    danger_dates = []
    if selected_allergens:
        placeholders = ",".join(["?"] * len(selected_allergens))
        query = f"SELECT DISTINCT date FROM menu_allergens WHERE allergen IN ({placeholders})"
        df_danger = pd.read_sql(query, conn, params=selected_allergens)
        danger_dates = df_danger["date"].tolist()

    year = datetime.now().year
    month = detected_month

    st.subheader(f"📅 {year}年 {month}月 カレンダー")

    # 選択した日付の状態保持
    if "selected_day" not in st.session_state:
        st.session_state["selected_day"] = None

    # CSSでボタンの装飾
    st.markdown(
        """
        <style>
        /* ボタン内のマージン調整 */
        div[data-testid="column"] button {
            width: 100%;
            height: 50px;
            font-size: 16px !important;
            font-weight: bold !important;
            border-radius: 8px !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    week_days = ["月", "火", "水", "木", "金", "土", "日"]
    header_cols = st.columns(7)
    for idx, day_name in enumerate(week_days):
        header_cols[idx].markdown(
            f"<div style='text-align: center; font-weight: bold;'>{day_name}</div>",
            unsafe_allow_html=True,
        )

    st.write("---")

    cal = calendar.monthcalendar(year, month)

    for week in cal:
        cols = st.columns(7)
        for idx, day in enumerate(week):
            if day == 0:
                cols[idx].empty()
            else:
                search_pattern = f"{month}/{day}(%"
                df_day_menu = pd.read_sql(
                    "SELECT date FROM menu_allergens WHERE date LIKE ?",
                    conn,
                    params=[search_pattern],
                )

                with cols[idx]:
                    if not df_day_menu.empty:
                        date_label = df_day_menu["date"].iloc[0]
                        is_danger = date_label in danger_dates

                        # アレルゲン有無でボタンタイプを変更
                        btn_label = f"⚠️{day}" if is_danger else f"{day}"
                        btn_type = "primary" if is_danger else "secondary"

                        if st.button(
                            btn_label,
                            key=f"day_btn_{month}_{day}",
                            type=btn_type,
                            use_container_width=True,
                        ):
                            st.session_state["selected_day"] = day
                    else:
                        st.button(
                            f"{day}",
                            key=f"disabled_day_{month}_{day}",
                            disabled=True,
                            use_container_width=True,
                        )

    # -----------------------------
    # 選択された日の詳細を下に表示
    # -----------------------------
    selected_day = st.session_state["selected_day"]

    if selected_day is not None:
        search_pattern = f"{month}/{selected_day}(%"
        df_selected_day = pd.read_sql(
            "SELECT dish AS 料理名, allergen AS アレルゲン, date FROM menu_allergens WHERE date LIKE ?",
            conn,
            params=[search_pattern],
        )

        if not df_selected_day.empty:
            date_label = df_selected_day["date"].iloc[0]
            st.write("---")
            st.subheader(f"📅 【{date_label}】のアレルゲン一覧")

            grouped = df_selected_day.groupby("アレルゲン")["料理名"].unique()

            for allergen_name, dishes in grouped.items():
                if allergen_name in selected_allergens:
                    st.markdown(
                        f"<p style='color:#d32f2f; font-weight:bold; font-size:18px; margin-bottom:4px;'>🚨 📌 {allergen_name}（選択中のアレルゲン）</p>",
                        unsafe_allow_html=True,
                    )
                else:
                    st.markdown(f"**📌 {allergen_name}**")

                for dish in dishes:
                    st.write(f"└ {dish}")

    conn.close()

    st.write("---")
    st.markdown("⚪ **通常ボタン**：給食あり（安全）")
    st.markdown("🔴 **⚠️マーク（赤ボタン）**：選択したアレルゲンが含まれる日")

st.write("---")
st.write("製作者：木村 陸")
st.write('お問い合わせ: "rikukimura0603@gmail.com"')
