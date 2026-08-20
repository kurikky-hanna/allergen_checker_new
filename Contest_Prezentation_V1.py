import hashlib
import os
import re
import sqlite3
import sys
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

    try:
        with pdfplumber.open(pdf_path) as pdf:
            for page_num, page in enumerate(pdf.pages):
                text = page.extract_text()
                if not text:
                    continue

                date_match = re.search(r"(\d+)月(\d+)日", text)
                if not date_match:
                    continue

                month = int(date_match.group(1))
                day = int(date_match.group(2))
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

                    # 💡 ヘッダーの残骸（項目名）を完全ガード
                    if any(
                        keyword in dish_str
                        for keyword in [
                            "料理名",
                            "食品名",
                            "品名",
                            "アレルゲン",
                            "特定原材料",
                            "分類",
                        ]
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
                            continue  # 数値が入っている行（食材行）はスルー
                        except ValueError:
                            pass

                    for allergen in allergen_headers:
                        cell = (
                            str(row.get(allergen, "")).strip().replace("\n", "")
                        )

                        if cell == allergen or "特定原材料" in cell:
                            continue

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


# ===== Streamlit から呼ぶメイン処理 =====
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


# ===== Streamlit 画面設定 =====
st.set_page_config(
    page_title="給食アレルゲン調査機", page_icon="🍔", layout="centered"
)

st.title("給食アレルゲン調査機（Streamlit版）")
uploaded = st.file_uploader("PDFを選んでください", type=["pdf"])
st.write("食物アレルギー原因食品一覧表のPDFを入手して")

if uploaded:
    # 💡 【重複読み込み防止】ファイルのハッシュ値で同一ファイルかチェック
    file_bytes = uploaded.getbuffer()
    file_hash = hashlib.md5(file_bytes).hexdigest()

    # セッションで「処理済みハッシュ」を管理して、未処理の時だけ解析を実行
    if "processed_hash" not in st.session_state or st.session_state["processed_hash"] != file_hash:
        temp_pdf_path = get_writable_path("temp_uploaded.pdf")
        with open(temp_pdf_path, "wb") as f:
            f.write(file_bytes)

        with st.spinner("PDFを解析中...少々お待ちください"):
            process_pdf(temp_pdf_path)

        # 解析が完了したらハッシュを記録
        st.session_state["processed_hash"] = file_hash

    # データベースから日付一覧とアレルゲン一覧を取得
    conn = sqlite3.connect(DB_PATH)
    df_dates = pd.read_sql("SELECT DISTINCT date FROM menu_allergens ORDER BY date", conn)
    df_allergen = pd.read_sql("SELECT DISTINCT name FROM allergen WHERE lang='ja'", conn)
    conn.close()

    # タブで「日付から探す」と「アレルゲンから探す」を切り替え
    tab1, tab2 = st.tabs(["📅 日付から探す", "🍔 アレルゲンから探す"])

    # --------------------------------------------------
    # タブ1: 日付を選択してアレルゲンを表示
    # --------------------------------------------------
    with tab1:
        with st.form("date_select_form"):
            st.write("アレルゲンを確認したい日付を選んで「決定」を押してね")
            
            selected_date = st.selectbox("日付を選択", df_dates["date"])
            submitted_date = st.form_submit_button("決定")

            if submitted_date:
                if selected_date:
                    conn = sqlite3.connect(DB_PATH)
                    df_day_menu = pd.read_sql(
                        "SELECT dish AS 料理名, allergen AS アレルゲン FROM menu_allergens WHERE date=?",
                        conn,
                        params=[selected_date]
                    )
                    conn.close()

                    if not df_day_menu.empty:
                        st.subheader(f"【{selected_date}】のアレルゲン別一覧")
                        grouped = df_day_menu.groupby("アレルゲン")["料理名"].unique()

                        for allergen_name, dishes in grouped.items():
                            with st.expander(f"📌 {allergen_name}（{len(dishes)}品）"):
                                for dish in dishes:
                                    st.write(f"└─ {dish}")
                    else:
                        st.info(f"「{selected_date}」のデータは見つかりませんでした。")

    # --------------------------------------------------
    # タブ2: アレルゲンから探す機能
    # --------------------------------------------------
    with tab2:
        with st.form("allergen_select_form"):
            st.write("表示したいアレルゲンを選んでから「決定」を押してね")

            selected_allergens = st.multiselect(
                "表示したいアレルゲンを選択(複数選択可)",
                df_allergen["name"]
            )
            submitted_allergen = st.form_submit_button("決定")

        if submitted_allergen:
            if selected_allergens:
                conn = sqlite3.connect(DB_PATH)

                for allergen in selected_allergens:
                    st.subheader(f"【{allergen}】を含む料理一覧")
                    df = pd.read_sql(
                        "SELECT DISTINCT date AS 日付, dish AS 料理名 FROM menu_allergens WHERE allergen=?",
                        conn,
                        params=[allergen]
                    )

                    if not df.empty:
                        st.dataframe(df, use_container_width=True)
                    else:
                        st.info(f"「{allergen}」が含まれる料理は見つかりませんでした。")

                conn.close()
            else:
                st.warning("アレルゲンを1つ以上選択してから「決定」を押してね！")

st.write("---")
st.write("製作者：木村 陸")
st.write(
    '何か不具合がありましたら、こちらのGmailアドレスにお申し付けください   address:"rikukimura0603@gmail.com"'
)
st.write("質問には答えます（15日以内には完了させます）")
