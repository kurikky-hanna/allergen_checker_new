import hashlib
import os
import re
import sqlite3
import sys
import pandas as pd
import pdfplumber
import streamlit as st
from datetime import datetime


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

allergen_headers = correct_columns[2:]  # アレルゲン項目28種


# ===== table_13 を作る処理 =====
def save_table13(all_dfs):
    try:
        dfs_filtered = []
        for d in all_dfs:
            if len(d.columns) == len(correct_columns):
                d.columns = correct_columns
                dfs_filtered.append(d)

        if not dfs_filtered:
            print("❌ 列数が一致するテーブルがありません")
            return False

        df = pd.concat(dfs_filtered, ignore_index=True)

        conn = sqlite3.connect(DB_PATH)
        df.to_sql("table_13", conn, if_exists="replace", index=False)
        conn.close()

        print("✅ table_13 作成完了")
        return True

    except Exception as e:
        print(f"❌ table_13 作成エラー: {e}")
        return False


# ===== PDF解析ロジック =====
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

                    if (
                        "料理名" in dish_str
                        or "食品名" in dish_str
                        or "品名" in dish_str
                    ):
                        continue

                    dish_str = re.sub(r"【\s*特例\s*[:：][^】]*】", "", dish_str)
                    dish_str = re.sub(r"[（\(][^）\)]*[）\)]", "", dish_str)
                    dish_str = re.split(r"[／/]", dish_str)[0].strip()

                    if dish_str == "":
                        continue

                    amount = row["分類"]
                    if not pd.isna(amount):
                        amount_str = str(amount).strip()
                        try:
                            float(amount_str)
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
        print(f"❌ build_date_dish_map エラー: {e}")

    finally:
        conn.commit()
        conn.close()


# ===== アレルゲン一覧登録 =====
def insert_allergens_from_table1():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    for allergen in allergen_headers:
        try:
            cur.execute(
                f"SELECT COUNT(*) FROM table_13 "
                f"WHERE `{allergen}` LIKE '%○%' OR `{allergen}` LIKE '%▲%'"
            )
            count = cur.fetchone()[0]
            if count > 0:
                cur.execute(
                    "SELECT COUNT(*) FROM allergen WHERE name=? AND lang='ja'",
                    (allergen,),
                )
                exists = cur.fetchone()[0]
                if not exists:
                    cur.execute(
                        "INSERT INTO allergen (name, lang, details) VALUES (?, 'ja', '')",
                        (allergen,),
                    )
                    cur.execute(
                        "INSERT INTO allergen (name, lang, details) VALUES (?, 'en', '')",
                        (allergen,),
                    )
        except Exception as e:
            print(f"⚠️ {allergen} の処理中にエラー: {e}")
    conn.commit()
    conn.close()


# ===== Streamlit メイン処理 =====
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
                        df = pd.DataFrame(table)
                        all_dfs.append(df)
    except Exception as e:
        print(f"❌ pdfplumber 抽出エラー: {e}")
        return

    if not save_table13(all_dfs):
        print("❌ table_13 の作成に失敗しました")
        return

    insert_allergens_from_table1()
    build_date_dish_map(pdf_path, all_dfs)
    print("🎉 PDF 処理完了！")


# ===== 画面設定 =====
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

    # DBデータ取得
    conn = sqlite3.connect(DB_PATH)
    df_dates = pd.read_sql(
        "SELECT DISTINCT date FROM menu_allergens ORDER BY date", conn
    )
    df_allergen = pd.read_sql(
        "SELECT DISTINCT name FROM allergen WHERE lang='ja'", conn
    )

    detected_month = 7
    if not df_dates.empty:
        first_date_str = df_dates["date"].iloc[0]
        month_match = re.search(r"(\d+)/", first_date_str)
        if month_match:
            detected_month = int(month_match.group(1))

    # 💡 【機能追加】警戒したいアレルゲンの選択ボックスを上部に設置！
    st.write("---")
    selected_allergens = st.multiselect(
        "⚠️ カレンダーでハイライト（赤く表示）したいアレルゲンを選択してください",
        df_allergen["name"].tolist(),
        placeholder="アレルゲンを選択...",
    )

    import calendar

    year = datetime.now().year
    month = detected_month

    st.subheader(f"📅 {year}年 {month}月 カレンダー")

    # 該当アレルゲンが含まれる日付リストを事前に取得
    danger_dates = []
    if selected_allergens:
        placeholders = ",".join(["?"] * len(selected_allergens))
        query = f"SELECT DISTINCT date FROM menu_allergens WHERE allergen IN ({placeholders})"
        df_danger = pd.read_sql(query, conn, params=selected_allergens)
        danger_dates = df_danger["date"].tolist()

    # カレンダーのHTML生成
    week_days = ["月", "火", "水", "木", "金", "土", "日"]
    cal = calendar.monthcalendar(year, month)

    calendar_html = """
    <style>
    .calendar-grid {
        display: grid;
        grid-template-columns: repeat(7, 1fr);
        gap: 6px;
        width: 100%;
        margin-bottom: 15px;
    }
    .calendar-header {
        text-align: center;
        font-weight: bold;
        padding: 6px 0;
    }
    .calendar-day {
        text-align: center;
        padding: 10px 2px;
        border-radius: 8px;
        background-color: #f8f9fa;
        font-size: 14px;
        color: #333;
    }
    .calendar-day-safe {
        background-color: #e2f0d9;
        font-weight: bold;
    }
    .calendar-day-danger {
        background-color: #f8d7da;
        color: #721c24;
        font-weight: bold;
        border: 2px solid #f5c6cb;
    }
    .calendar-empty {
        padding: 10px 2px;
    }
    </style>
    """

    calendar_html += '<div class="calendar-grid">'

    for day_name in week_days:
        calendar_html += f'<div class="calendar-header">{day_name}</div>'

    for week in cal:
        for day in week:
            if day == 0:
                calendar_html += '<div class="calendar-empty"></div>'
            else:
                search_pattern = f"{month}/{day}(%"

                # その日に給食があるかチェック
                df_check = pd.read_sql(
                    "SELECT date FROM menu_allergens WHERE date LIKE ?",
                    conn,
                    params=[search_pattern],
                )

                if not df_check.empty:
                    current_date_str = df_check["date"].iloc[0]
                    # 選択したアレルゲンが含まれている日か判別
                    if current_date_str in danger_dates:
                        calendar_html += f'<div class="calendar-day calendar-day-danger">⚠️ {day}</div>'
                    else:
                        calendar_html += f'<div class="calendar-day calendar-day-safe">{day}</div>'
                else:
                    calendar_html += f'<div class="calendar-day">{day}</div>'

    calendar_html += "</div>"
    st.markdown(calendar_html, unsafe_allow_html=True)

    # 凡例表示
    col1, col2 = st.columns(2)
    col1.markdown("🟩 **緑色**：給食あり（安全）")
    col2.markdown("🟥 **赤色（⚠️）**：選択したアレルゲンが含まれる日")

    st.write("---")

    # 日付選択セレクトボックス
    available_dates = df_dates["date"].tolist()
    selected_date = st.selectbox(
        "🔍 詳細を確認したい日付を選んでください", available_dates
    )

    if selected_date:
        df_day_menu = pd.read_sql(
            """
            SELECT dish AS 料理名, allergen AS アレルゲン 
            FROM menu_allergens 
            WHERE date = ?
            """,
            conn,
            params=[selected_date],
        )

        st.subheader(f"【{selected_date}】のアレルゲン一覧")
        grouped = df_day_menu.groupby("アレルゲン")["料理名"].unique()

        for allergen_name, dishes in grouped.items():
            # 選択中のアレルゲンは強調表示
            if allergen_name in selected_allergens:
                st.markdown(
                    f"<h4 style='color: red;'>🚨 📌 {allergen_name}（選択中のアレルゲン）</h4>",
                    unsafe_allow_html=True,
                )
            else:
                st.markdown(f"**📌 {allergen_name}**")

            for dish in dishes:
                st.write(f"└ {dish}")

    conn.close()

st.write("---")
st.write("製作者：木村 陸")
st.write('お問い合わせ: "rikukimura0603@gmail.com"')
