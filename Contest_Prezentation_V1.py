import hashlib
import os
import re
import sqlite3
import sys
import pandas as pd
import pdfplumber
import streamlit as st
from tabula.io import read_pdf



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

allergen_headers = [
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


# ===== table_13 を作る処理 =====
def create_table13(pdf_path):
    try:
        dfs = read_pdf(
            pdf_path, pages="all", multiple_tables=True, lattice=True
        )

        dfs_filtered = [
            d for d in dfs if len(d.columns) == len(correct_columns)
        ]
        if not dfs_filtered:
            print("❌ 列数が一致するテーブルがありません")
            return False

        df = pd.concat(dfs_filtered, ignore_index=True)
        df.columns = correct_columns

        conn = sqlite3.connect(DB_PATH)
        df.to_sql("table_13", conn, if_exists="replace", index=False)
        conn.close()

        print("✅ table_13 作成完了")
        return True

    except Exception as e:
        print(f"❌ table_13 作成エラー: {e}")
        return False


# ===== アレルゲン一覧登録 =====
def insert_allergens_from_table1():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    for allergen in allergen_headers:
        try:
            cur.execute(
                f"SELECT COUNT(*) FROM table_13 "
                f"WHERE `{allergen}` IN ('○','▲')"
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


# ===== PDF解析ロジック =====
def build_date_dish_map(pdf_path):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("DELETE FROM menu_allergens")
    conn.commit()

    try:
        with pdfplumber.open(pdf_path) as pdf:
            all_dfs = read_pdf(
                pdf_path, pages="all", multiple_tables=True, lattice=True
            )

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

                if df_page.shape[0] < 3:
                    continue

                header_rows = df_page.iloc[0:3]
                new_columns = [
                    "".join([
                        (
                            str(header_rows.iloc[i, col])
                            if not pd.isna(header_rows.iloc[i, col])
                            else ""
                        )
                        for i in range(3)
                    ])
                    for col in range(header_rows.shape[1])
                ]

                df_page.columns = [
                    re.sub(r"\s+", "", col) for col in new_columns
                ]
                df_page = df_page.iloc[3:]

                if len(df_page.columns) != len(correct_columns):
                    continue

                df_page.columns = correct_columns

                for _, row in df_page.iterrows():
                    dish = row["料理名/食品名"]
                    if pd.isna(dish):
                        continue

                    dish_str = str(dish).strip()

                    # 1. 【特例: ...】の表記を消去
                    dish_str = re.sub(
                        r"【\s*特例\s*[:：][^】]*】", "", dish_str
                    )

                    # 2. カッコ（全角・半角）で囲まれた食材名を消去
                    dish_str = re.sub(r"[（\(][^）\)]*[）\)]", "", dish_str)

                    # 3. 「／」や「/」区切りの後半（食材名）を削る
                    dish_str = re.split(r"[／/]", dish_str)[0]

                    dish_str = dish_str.strip()

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
                        cell = row.get(allergen)
                        if str(cell).strip() in ["○", "▲", "☒"]:
                            cur.execute(
                                "INSERT INTO menu_allergens (date, dish,"
                                " allergen) VALUES (?, ?, ?)",
                                (date_str, dish_str, allergen),
                            )

    except Exception as e:
        print(f"❌ build_date_dish_map エラー: {e}")

    finally:
        conn.commit()
        conn.close()


# ===== Streamlit から呼ぶメイン処理 =====
def process_pdf(pdf_path):
    init_db()
    clear_db()

    if not create_table13(pdf_path):
        print("❌ table_13 の作成に失敗しました")
        return

    insert_allergens_from_table1()
    build_date_dish_map(pdf_path)
    print("🎉 PDF 処理完了！")


# ===== Streamlit 画面設定（※必ずタイトルの前に記述） =====
st.set_page_config(
    page_title="給食アレルゲン調査機", page_icon="🍔", layout="centered"
)

st.title("給食アレルゲン調査機（Streamlit版）")
uploaded = st.file_uploader("PDFを選んでください", type=["pdf"])
st.write("食物アレルギー原因食品一覧表のPDFを入手して、アレルゲンを選択してください")

if uploaded:
    pdf_bytes = uploaded.getvalue()
    pdf_hash = hashlib.md5(pdf_bytes).hexdigest()

    # 未解析または新しいPDFがアップロードされた場合のみ解析処理を実行
    if st.session_state.get("processed_pdf_hash") != pdf_hash:
        with open("temp.pdf", "wb") as f:
            f.write(pdf_bytes)
        with st.spinner("PDF解析中...."):
            process_pdf("temp.pdf")
        st.session_state["processed_pdf_hash"] = pdf_hash

    st.success("PDF解析完了！")

    # データベースから登録されているアレルゲン一覧を取得
    conn = sqlite3.connect(DB_PATH)
    df_allergen = pd.read_sql(
        "SELECT DISTINCT name FROM allergen WHERE lang='ja'", conn
    )
    conn.close()

    # フォーム（決定ボタンを押すまで再読み込みをブロック）
    with st.form("allergen_select_form"):
        st.write("表示したいアレルゲンを選んでから「決定」を押してね")

        selected_allergens = st.multiselect(
            "表示したいアレルゲンを選択(複数選択可)", df_allergen["name"]
        )

        submitted = st.form_submit_button("決定")

    if submitted:
        if selected_allergens:
            conn = sqlite3.connect(DB_PATH)

            for allergen in selected_allergens:
                st.subheader(f"【{allergen}】を含む料理一覧")
                df = pd.read_sql(
                    "SELECT DISTINCT date AS 日付, dish AS 料理名 FROM"
                    " menu_allergens WHERE allergen=?",
                    conn,
                    params=[allergen],
                )

                if not df.empty:
                    for date,group in df.groupby("日付", sort=False):
                        st.markdown(f"### {date}")
                        dishes=group["料理名"].drop_duplicates()
                        for dish in dishes:
                            st.markdown(f"-{dish}")
                        st.divider()

                    
                else:
                    st.info(
                        f"「{allergen}」が含まれる料理は見つかりませんでした。"
                    )

            conn.close()
        else:
            st.warning(
                "アレルゲンを1つ以上選択してから「決定」を押してね！"
            )

