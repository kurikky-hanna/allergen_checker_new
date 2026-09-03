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

# 🟢 アレルギー表示対象（特定原材料8品目＋特定原材料に準ずるもの20品目＝計28品目）
ALL_28_ALLERGENS = [
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
        allergen TEXT,
        mark TEXT
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
correct_columns = ["料理名/食品名", "分類"] + ALL_28_ALLERGENS
allergen_headers = ALL_28_ALLERGENS


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

                        found_mark = None
                        if any(m in cell for m in ["○", "O", "0"]):
                            found_mark = "○"
                        elif any(m in cell for m in ["▲", "☒"]):
                            found_mark = "▲"

                        if found_mark:
                            cur.execute(
                                "INSERT INTO menu_allergens (date, dish, allergen, mark) VALUES (?, ?, ?, ?)",
                                (date_str, dish_str, allergen, found_mark),
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

st.title("給食アレルゲン調査機🍔")

uploaded = st.file_uploader(
    "学校から配布された「食物アレルギー原因食品一覧表」のPDFをアップロードしてください。",
    type=["pdf"],
)
st.write(
    "このアプリがPDFを読み取り、「いつ・どの料理に・どのアレルゲンが含まれているか」を調べられます。"
)
st.write("⚠️ 大切なお知らせ")
st.write("このアプリの結果だけで判断せず、必ず学校から配布された原本の資料も確認してください。",
    "PDFの形式や記載方法によっては、正しく読み取れない場合があります。",
    "※このアプリはアレルギーの有無や安全性を保証するものではありません。"
    )
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
    detected_allergens = df_allergen["name"].tolist()

    unused_allergens = [
        item for item in ALL_28_ALLERGENS if item not in detected_allergens
    ]

    detected_month = 7
    if not df_dates.empty:
        month_match = re.search(r"(\d+)/", df_dates["date"].iloc[0])
        if month_match:
            detected_month = int(month_match.group(1))

    with st.expander(
        f"📋 今月（{detected_month}月）のアレルゲン使用状況のチェック結果を見る",
        expanded=False,
    ):
        st.markdown(
            f"**🔴 今月の給食で検出されたアレルゲン（{len(detected_allergens)}項目）**"
        )
        st.info(
            "、".join(detected_allergens)
            if detected_allergens
            else "検出なし"
        )

        st.markdown(
            f"**🟢 今月は使用されていない安全なアレルゲン（{len(unused_allergens)}項目）**"
        )
        st.caption(
            "※以下の食品は今月の給食メニュー・原材料表に記載がありませんでした。"
        )
        st.success(
            "、".join(unused_allergens) if unused_allergens else "なし"
        )

    # ===== タブ設定 =====
    tab1, tab2 = st.tabs(["🔍 アレルゲンで検索", "📅 カレンダー表示"])

    # -----------------------------
    # Tab 1: アレルゲン検索
    # -----------------------------
    with tab1:
        st.subheader("🔍 条件から探す")

        target_allergens = st.multiselect(
            "調べたいアレルゲンを選択してください（全28項目から選択可能）",
            ALL_28_ALLERGENS,
        )

        # 💡 マークによる絞り込み機能の追加
        mark_filter = st.radio(
            "表示する種類を選択",
            ["すべて", "○（直接使用）のみ", "▲（コンタミ等）のみ"],
            horizontal=True
        )

        if target_allergens:
            selected_detected = [
                a for a in target_allergens if a in detected_allergens
            ]
            selected_unused = [
                a for a in target_allergens if a in unused_allergens
            ]

            if selected_unused:
                unused_str = "・".join(selected_unused)
                st.success(
                    f"🟢 **【安心】{unused_str}** は、今月の給食献立には一切含まれていません。"
                )

            if selected_detected:
                placeholders = ",".join(["?"] * len(selected_detected))
                
                # ラジオボタンの選択に応じてSQLのWHERE句を変更
                mark_condition = ""
                if mark_filter == "○（直接使用）のみ":
                    mark_condition = "AND mark = '○'"
                elif mark_filter == "▲（コンタミ等）のみ":
                    mark_condition = "AND mark = '▲'"

                query = f"""
                    SELECT date, dish, allergen, mark
                    FROM menu_allergens
                    WHERE allergen IN ({placeholders}) {mark_condition}
                    ORDER BY 
                        CAST(substr(date, 1, instr(date, '/') - 1) AS INTEGER),
                        CAST(substr(date, instr(date, '/') + 1, instr(date, '(') - instr(date, '/') - 1) AS INTEGER)
                """
                df_result = pd.read_sql(query, conn, params=selected_detected)

                if not df_result.empty:
                    allergens_in_res = df_result["allergen"].unique()
                    for allergen_val in allergens_in_res:
                        df_allergen_items = df_result[
                            df_result["allergen"] == allergen_val
                        ]

                        with st.popover(
                            f"⚠️ {allergen_val}（{len(df_allergen_items)}件）",
                            use_container_width=True,
                        ):
                            st.markdown(
                                f"<h3 style='color:#d32f2f;'>🚨 【{allergen_val}】が含まれる給食一覧</h3>",
                                unsafe_allow_html=True,
                            )

                            grouped_by_date = df_allergen_items.groupby(
                                "date", sort=False
                            )

                            for date_val, group in grouped_by_date:
                                st.markdown(f"**📅 {date_val}**")
                                for _, row in group.iterrows():
                                    mark_str = (
                                        f" [{row['mark']}]"
                                        if row["mark"]
                                        else ""
                                    )
                                    st.write(f"└ {row['dish']}{mark_str}")
                                st.markdown(
                                    "<div style='margin-bottom: 8px;'></div>",
                                    unsafe_allow_html=True,
                                )
                else:
                    st.info("条件に一致するメニューは見つかりませんでした。")

    # -----------------------------
    # Tab 2: カレンダー表示
    # -----------------------------
    with tab2:
        selected_allergens = st.multiselect(
            "⚠️ 警戒するアレルゲンを選択（全28項目から選択可能）",
            ALL_28_ALLERGENS,
            placeholder="アレルゲンを選択...",
        )

        danger_dates = []
        if selected_allergens:
            selected_unused = [
                a for a in selected_allergens if a in unused_allergens
            ]
            if selected_unused:
                st.info(
                    f"🟢 選択されたアレルゲンのうち「**{'・'.join(selected_unused)}**」は今月一度も給食に使用されません。"
                )

            placeholders = ",".join(["?"] * len(selected_allergens))
            query = f"SELECT DISTINCT date FROM menu_allergens WHERE allergen IN ({placeholders})"
            df_danger = pd.read_sql(query, conn, params=selected_allergens)
            danger_dates = df_danger["date"].tolist()

        year = datetime.now().year
        month = detected_month

        st.subheader(f"📅 {year}年 {month}月 カレンダー")

        week_days = ["月", "火", "水", "木", "金", "土", "日"]
        header_cols = st.columns(7)
        for idx, day_name in enumerate(week_days):
            header_cols[idx].markdown(
                f"<div style='text-align: center; font-weight: bold; font-size: 12px; color: #777;'>{day_name}</div>",
                unsafe_allow_html=True,
            )

        cal = calendar.monthcalendar(year, month)

        for week in cal:
            cols = st.columns(7)
            for idx, day in enumerate(week):
                if day == 0:
                    cols[idx].empty()
                else:
                    search_pattern = f"{month}/{day}(%"
                    df_day_menu = pd.read_sql(
                        """
                        SELECT dish AS 料理名, allergen AS アレルゲン, mark AS マーク, date
                        FROM menu_allergens
                        WHERE date LIKE ?
                        """,
                        conn,
                        params=[search_pattern],
                    )

                    with cols[idx]:
                        if not df_day_menu.empty:
                            date_label = df_day_menu["date"].iloc[0]
                            is_danger = date_label in danger_dates

                            btn_label = f"⚠️{day}" if is_danger else f"{day}"
                            btn_type = "primary" if is_danger else "secondary"

                            with st.popover(
                                btn_label,
                                type=btn_type,
                                use_container_width=True,
                            ):
                                st.markdown(f"### 📅 【{date_label}】")

                                grouped = df_day_menu.groupby(
                                    ["アレルゲン", "マーク"]
                                )

                                for (
                                    allergen_name,
                                    mark_val,
                                ), group in grouped:
                                    mark_str = (
                                        f"（{mark_val}）" if mark_val else ""
                                    )
                                    if allergen_name in selected_allergens:
                                        st.markdown(
                                            f"<p style='color:#d32f2f; font-weight:bold; font-size:15px; margin-bottom:2px;'>🚨 📌 {allergen_name}{mark_str}</p>",
                                            unsafe_allow_html=True,
                                        )
                                    else:
                                        st.markdown(
                                            f"**📌 {allergen_name}{mark_str}**"
                                        )

                                    for dish in group["料理名"].unique():
                                        st.write(f"└ {dish}")
                        else:
                            st.button(
                                f"{day}",
                                key=f"disabled_day_{month}_{day}",
                                disabled=True,
                                use_container_width=True,
                            )

        st.write("---")
        st.markdown("⚪ **数字のみ**：給食あり（安全）")
        st.markdown(
            "🔴 **⚠️マーク（赤ボタン）**：選択したアレルゲンが含まれる日"
        )
        st.markdown(
            "※ **（○）**：原材料に使用 / **（▲）**：コンタミネーション等の可能性"
        )

    conn.close()

st.write("---")
st.write("製作者：木村 陸")
st.write('お問い合わせ: "rikukimura0603@gmail.com"')
