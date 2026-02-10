import streamlit as st
import json
import base64
import pandas as pd
import io
from openai import OpenAI
from datetime import datetime
import difflib 
import uuid
import time
import sqlite3

# ==========================================
# 🎨 [UI 설정]
# ==========================================
st.set_page_config(
    page_title="CBAM Master Pro", 
    page_icon="🌍", 
    layout="wide",
    initial_sidebar_state="expanded"
)

st.markdown("""
    <style>
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
    .stDeployButton {display:none;}
    div[data-testid="stMetricValue"] {
        font-size: 24px;
        color: #004494;
        font-weight: bold;
    }
    .stTabs [data-baseweb="tab-list"] {
        gap: 10px;
    }
    .stTabs [data-baseweb="tab"] {
        height: 50px;
        white-space: pre-wrap;
        background-color: #f0f2f6;
        border-radius: 4px 4px 0px 0px;
        font-weight: bold;
    }
    .stTabs [aria-selected="true"] {
        background-color: #004494;
        color: white;
    }
    </style>
    """, unsafe_allow_html=True)

# ==========================================
# 🔑 API 키 및 데이터 연결
# ==========================================
try:
    api_key = st.secrets["OPENAI_API_KEY"]
    client = OpenAI(api_key=api_key)
except Exception as e:
    st.error(f"🚨 API 키 오류: .streamlit/secrets.toml 파일을 확인하세요. ({e})")
    st.stop()

USER_DB_URL = "https://docs.google.com/spreadsheets/d/e/2PACX-1vRqCIpXf7jM4wyn8EhpoZipkUBQ2K43rEiaNi-KyoaI1j93YPNMLpavW07-LddivnoUL-FKFDMCFPkI/pub?gid=0&single=true&output=csv"
CBAM_DATA_URL = "https://docs.google.com/spreadsheets/d/e/2PACX-1vRTkYfVcC9EAv_xW0FChVWK3oMsPaxXiRL-hOQQeGT_aLsUG044s1L893er36HVJUpgTCrsM0xElFpW/pub?gid=747982569&single=true&output=csv"

# ------------------------------------------------
# 💾 데이터베이스(DB) 관리
# ------------------------------------------------
def init_db():
    conn = sqlite3.connect('cbam_database.db', check_same_thread=False)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT,
            date TEXT,
            filename TEXT,
            item_name TEXT,
            material TEXT,
            weight REAL,
            hs_code TEXT,
            tax_krw INTEGER,
            exchange_rate REAL
        )
    ''')
    conn.commit()
    conn.close()

def save_to_db(data_list):
    conn = sqlite3.connect('cbam_database.db', check_same_thread=False)
    c = conn.cursor()
    for item in data_list:
        c.execute('''
            INSERT INTO history (username, date, filename, item_name, material, weight, hs_code, tax_krw, exchange_rate)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            item['Company'], item['Date'], item['File Name'], item['Item Name'], 
            item['Material'], item['Weight (kg)'], item['HS Code'], 
            item['Default Tax (KRW)'], item['exchange_rate']
        ))
    conn.commit()
    conn.close()

def load_from_db(username):
    conn = sqlite3.connect('cbam_database.db', check_same_thread=False)
    df = pd.read_sql_query("SELECT * FROM history WHERE username = ?", conn, params=(username,))
    conn.close()
    if not df.empty:
        df = df.rename(columns={
            'date': 'Date', 'filename': 'File Name', 'item_name': 'Item Name',
            'material': 'Material', 'weight': 'Weight (kg)', 'hs_code': 'HS Code',
            'tax_krw': 'Default Tax (KRW)', 'exchange_rate': 'exchange_rate', 'username': 'Company'
        })
        df = df.sort_values(by='id', ascending=False)
    return df

def clear_my_history(username):
    conn = sqlite3.connect('cbam_database.db', check_same_thread=False)
    c = conn.cursor()
    c.execute("DELETE FROM history WHERE username = ?", (username,))
    conn.commit()
    conn.close()

init_db()

@st.cache_data(ttl=60)
def load_user_data():
    try:
        df = pd.read_csv(USER_DB_URL)
        df.columns = df.columns.str.strip().str.lower()
        df['username'] = df['username'].astype(str).str.strip()
        df['password'] = df['password'].astype(str).str.strip()
        df['active'] = df['active'].astype(str).str.strip().str.lower()
        if 'credits' not in df.columns: df['credits'] = 0
        df['credits'] = pd.to_numeric(df['credits'], errors='coerce').fillna(0).astype(int)
        return df
    except:
        return pd.DataFrame()

@st.cache_data(ttl=300) 
def load_cbam_db():
    try:
        df = pd.read_csv(CBAM_DATA_URL)
        first_cell = str(df.iloc[0,0]).strip().lower()
        if 'category' not in df.columns.str.lower() and first_cell == 'category':
            new_header = df.iloc[0]
            df = df[1:]
            df.columns = new_header
        df.columns = df.columns.astype(str).str.strip().str.lower()
        
        db = {}
        for _, row in df.iterrows():
            if pd.isna(row.get('category')): continue
            cat = str(row['category']).strip()
            try: rate = float(row.get('exchange_rate', 1450.0))
            except: rate = 1450.0
            
            raw_hs = str(row.get('hs_code', '000000')).strip()
            if raw_hs == 'nan' or raw_hs == '': raw_hs = '000000'
            
            db[cat] = {
                "default": float(row.get('default', 0)), 
                "optimized": float(row.get('optimized', 0)), 
                "hs_code": raw_hs.split('.')[0], 
                "price": 85.0, 
                "exchange_rate": rate
            }
        return db
    except:
        return {}

user_df = load_user_data()
CBAM_DB = load_cbam_db()

def safe_float(value):
    try: return float(str(value).replace(',', '').replace('kg', '').replace('KG', '').strip())
    except: return 0.0

def force_match_material(ai_item_name, ai_material, db_keys):
    name_lower = str(ai_item_name).lower()
    mat_lower = str(ai_material).lower()
    
    if "bolt" in name_lower or "screw" in name_lower:
        found = [k for k in db_keys if "Bolt" in k or "Screw" in k]
        if found: return found[0]
    if "aluminum" in name_lower or "aluminium" in name_lower:
        found = [k for k in db_keys if "Aluminum" in k]
        if "ingot" in name_lower:
            ingot_found = [k for k in db_keys if "Ingot" in k]
            if ingot_found: return ingot_found[0]
        if found: return found[0]
    if "sheet" in name_lower or "plate" in name_lower:
        found = [k for k in db_keys if "Sheet" in k or "Plate" in k]
        if found: return found[0]
    if "cement" in name_lower or "cmnt" in name_lower:
        found = [k for k in db_keys if "cement" in k.lower()]
        if found: return found[0]

    matches = difflib.get_close_matches(ai_material, db_keys, n=1, cutoff=0.4)
    if matches: return matches[0]
    return "Other"

# ==========================================
# 🧮 핵심 로직
# ==========================================
def calculate_tax_logic(material, weight):
    if material in CBAM_DB: db = CBAM_DB[material]
    elif CBAM_DB: db = CBAM_DB[list(CBAM_DB.keys())[0]]
    else: db = {"default":0, "optimized":0, "price":0, "exchange_rate":1450}

    if weight <= 0: weight = 0.0
    rate = db.get('exchange_rate', 1450.0)
    
    bad_tax = int((weight/1000) * db['default'] * db['price'] * rate)
    good_tax = int((weight/1000) * db['optimized'] * db['price'] * rate)
    
    return {
        "bad_tax": bad_tax, 
        "good_tax": good_tax, 
        "savings": bad_tax - good_tax, 
        "material_display": material, 
        "weight": weight, 
        "hs_code": db.get('hs_code', '000000'), 
        "exchange_rate": rate
    }

def generate_official_excel(data_list):
    if not data_list: return None
    if isinstance(data_list, pd.DataFrame): data_list = data_list.to_dict('records')

    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        wb = writer.book
        fmt_header = wb.add_format({'bold': True, 'fg_color': '#004494', 'font_color': 'white', 'border': 1})
        fmt_num = wb.add_format({'border': 1, 'num_format': '#,##0.00'})
        fmt_eur = wb.add_format({'border': 1, 'num_format': '€#,##0.00'})
        fmt_krw = wb.add_format({'border': 1, 'num_format': '₩#,##0'})
        
        ws1 = wb.add_worksheet("Report_Summary")
        headers1 = ["Report Date", "Company", "Total Items", "Total Weight (Ton)", "Total Tax (EUR)", "Total Tax (KRW)"]
        t_tax_krw = sum([d.get('Default Tax (KRW)', 0) for d in data_list])
        t_tax_eur = sum([d.get('Default Tax (KRW)', 0) / d.get('exchange_rate', 1450) for d in data_list if d.get('exchange_rate', 0) > 0])
        
        for c, h in enumerate(headers1): ws1.write(0, c, h, fmt_header)
        ws1.write(1, 0, datetime.now().strftime('%Y-%m-%d'))
        ws1.write(1, 1, data_list[0].get('Company', ''))
        ws1.write(1, 2, len(data_list))
        ws1.write(1, 3, sum([d.get('Weight (kg)', 0) for d in data_list])/1000, fmt_num)
        ws1.write(1, 4, t_tax_eur, fmt_eur)
        ws1.write(1, 5, t_tax_krw, fmt_krw)
        ws1.set_column('A:F', 20)

        ws2 = wb.add_worksheet("CBAM_Data")
        headers2 = ["No", "Origin", "HS Code", "Item", "Weight (Ton)", "Emission Factor", "Est. Tax (EUR)", "Exch. Rate", "Est. Tax (KRW)"]
        for c, h in enumerate(headers2): ws2.write(0, c, h, fmt_header)
        
        for i, d in enumerate(data_list):
            r = i + 1
            w_ton = d.get('Weight (kg)', 0) / 1000
            mat = d.get('Material', 'Iron/Steel')
            factor = 0
            if mat in CBAM_DB: factor = CBAM_DB[mat].get('default', 0)
            rate = d.get('exchange_rate', 1450)
            ws2.write(r, 0, r)
            ws2.write(r, 1, "KR")
            ws2.write(r, 2, d.get('HS Code', ''))
            ws2.write(r, 3, d.get('Item Name', ''))
            ws2.write(r, 4, w_ton, fmt_num)
            ws2.write(r, 5, factor, fmt_num)
            ws2.write(r, 6, (d.get('Default Tax (KRW)', 0)/rate) if rate>0 else 0, fmt_eur)
            ws2.write(r, 7, rate, fmt_num)
            ws2.write(r, 8, d.get('Default Tax (KRW)', 0), fmt_krw)
        ws2.set_column('A:I', 18)
    return output.getvalue()

def analyze_image(image_bytes, filename, username):
    base64_image = base64.b64encode(image_bytes).decode('utf-8')
    try:
        cats_str = ", ".join(list(CBAM_DB.keys()))
        # 🚨 [V10.6] 포장재 제거(IGNORE) 명령 추가
        response = client.chat.completions.create(
            model="gpt-4o", 
            temperature=0.0, 
            messages=[
                {
                    "role": "system", 
                    "content": f"You are a CBAM expert. Identify distinct items relevant to CBAM (Iron, Steel, Aluminum, Cement, Hydrogen, Fertilizers). IGNORE non-CBAM items such as packing materials (plastic wrapping, wood pallets, boxes). For each item, select the Material Category STRICTLY from this list: [{cats_str}]. If unsure, use 'Other'. Extract 'Net Weight' in kg. Extract 'HS Code' (numbers only). Return JSON: {{'items': [{{'item': 'Item Name', 'material': 'Selected Category', 'weight': 1000, 'hs_code': '731800'}}, ...]}}."
                },
                {
                    "role": "user", 
                    "content": [
                        {"type": "text", "text": "Extract all CBAM items."}, 
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}}
                    ]
                }
            ],
            response_format={"type": "json_object"}
        )
        
        result_json = json.loads(response.choices[0].message.content)
        items_list = result_json.get('items', [])
        
        processed_items = []
        for item in items_list:
            w = safe_float(item.get('weight', 0))
            raw_item_name = item.get('item', '')
            raw_material = item.get('material', 'Other')
            ai_hs_code = str(item.get('hs_code', '')).replace('.', '').strip()
            
            corrected_mat = force_match_material(raw_item_name, raw_material, list(CBAM_DB.keys()))
            calc = calculate_tax_logic(corrected_mat, w)
            final_hs_code = ai_hs_code if (ai_hs_code and ai_hs_code != '000000') else calc['hs_code']
            
            processed_items.append({
                "File Name": filename,
                "Date": datetime.now().strftime('%Y-%m-%d %H:%M'),
                "Company": username.upper(),
                "Item Name": raw_item_name,
                "Material": corrected_mat,
                "Weight (kg)": w,
                "HS Code": final_hs_code, 
                "Default Tax (KRW)": calc['bad_tax'],
                "exchange_rate": calc['exchange_rate']
            })
            
        return processed_items
        
    except Exception as e:
        print(f"AI Error: {e}")
        return [{
            "File Name": filename, "Item Name": "Analysis Failed", 
            "Material": "Other", "Weight (kg)": 0, "HS Code": "000000",
            "Default Tax (KRW)": 0, "exchange_rate": 1450
        }]

# ==========================================
# 🚀 분석 처리 콜백
# ==========================================
def process_analysis():
    uploaded_files = st.session_state.get('upl_files', [])
    if uploaded_files:
        current_credits = st.session_state.get('credits', 0)
        required_credits = len(uploaded_files)
        is_unlimited = current_credits >= 999999
        
        if is_unlimited or (current_credits >= required_credits):
            st.session_state['run_id'] = str(uuid.uuid4())
            with st.spinner("AI가 정밀 분석 중입니다... 잠시만 기다려주세요."):
                all_results = []
                for i, file in enumerate(uploaded_files):
                    file.seek(0)
                    items = analyze_image(file.read(), file.name, st.session_state['username'])
                    if isinstance(items, list): all_results.extend(items)
                    else: all_results.append(items)
                
                st.session_state['batch_results'] = all_results
                save_to_db(all_results)
                
                if not is_unlimited:
                    st.session_state['credits'] -= required_credits
                    st.toast(f"💳 {required_credits} 크레딧 차감 완료")
                else:
                    st.toast("✅ 분석 및 저장 완료!")
        else:
            st.error(f"🚫 크레딧 부족!")

# ==========================================
# 🖥️ 화면 구성
# ==========================================

if 'logged_in' not in st.session_state: st.session_state['logged_in'] = False
if 'batch_results' not in st.session_state: st.session_state['batch_results'] = None
if 'run_id' not in st.session_state: st.session_state['run_id'] = str(uuid.uuid4())

if not st.session_state['logged_in']:
    c1, c2, c3 = st.columns([1, 2, 1])
    with c2:
        st.markdown("<br><br><h1 style='text-align: center; color: #004494;'>🌍 CBAM Master Pro</h1>", unsafe_allow_html=True)
        with st.container(border=True):
            username = st.text_input("아이디")
            password = st.text_input("비밀번호", type="password")
            if st.button("로그인", type="primary", use_container_width=True):
                if not user_df.empty:
                    match = user_df[(user_df['username'] == username) & (user_df['password'].astype(str) == password) & (user_df['active'] == 'o')]
                    if not match.empty:
                        st.session_state['logged_in'] = True
                        st.session_state['username'] = username
                        user_credits = int(match.iloc[0]['credits'])
                        st.session_state['credits'] = user_credits
                        st.rerun()
                    else:
                        st.error("❌ 로그인 실패")
                else:
                    st.error("⚠️ DB 연결 실패")
else:
    with st.sidebar:
        st.title("CBAM Master")
        st.success("🟢 System Online")
        st.divider()
        st.write(f"👤 **{st.session_state['username'].upper()}** 님")
        current_credits = st.session_state.get('credits', 0)
        if current_credits >= 999999: st.metric("잔여 크레딧", "♾️ 무제한 (VIP)")
        else: st.metric("잔여 크레딧", f"{current_credits} 회")
        
        my_history_df = load_from_db(st.session_state['username'])
        st.caption(f"📝 저장된 기록: {len(my_history_df)}건")
        
        if st.button("로그아웃"):
            st.session_state['logged_in'] = False
            st.rerun()

    tab1, tab2 = st.tabs(["🚀 분석 (Analysis)", "🕒 기록 관리 (History)"])

    with tab1:
        st.markdown("### 📄 인보이스 분석")
        if CBAM_DB: krw_rate = CBAM_DB[list(CBAM_DB.keys())[0]].get('exchange_rate', 1450)
        else: krw_rate = 1450
        st.info(f"💶 **실시간 환율 적용 중:** 1 EUR = **{krw_rate:,.2f} KRW** (Google Finance 연동)")

        with st.container(border=True):
            uploaded_files = st.file_uploader("파일 추가 (Drag & Drop)", type=["jpg", "png", "jpeg"], accept_multiple_files=True, key="upl_files")
            if uploaded_files:
                st.button(f"🚀 AI 분석 시작", type="primary", on_click=process_analysis)

        if st.session_state['batch_results']:
            st.divider()
            st.subheader("📊 금회 분석 결과")
            results = st.session_state['batch_results']
            
            total_tax_krw = sum([r.get('Default Tax (KRW)', 0) for r in results])
            total_weight = sum([safe_float(r.get('Weight (kg)', 0)) for r in results])
            
            m1, m2, m3 = st.columns(3)
            m1.metric("항목 수", f"{len(results)} 개")
            m2.metric("총 중량", f"{total_weight:,.0f} kg")
            m3.metric("총 세금", f"₩ {total_tax_krw:,.0f}")

            mat_options = list(CBAM_DB.keys())
            if "Other" not in mat_options: mat_options.append("Other")

            updated_final_results = []
            run_id = st.session_state['run_id']

            for idx, row in enumerate(results):
                with st.expander(f"📄 {row.get('File Name','')} - {row.get('Item Name','Unknown')} ({row.get('Weight (kg)',0)}kg)", expanded=True):
                    c1, c2, c3 = st.columns([2, 1, 1])
                    unique_key = f"{idx}_{run_id}"
                    
                    curr_mat = row.get('Material', 'Other')
                    if curr_mat not in mat_options: curr_mat = "Other"
                    new_mat = c1.selectbox("재질", mat_options, index=mat_options.index(curr_mat), key=f"m_{unique_key}")
                    
                    curr_hs = str(row.get('HS Code', '000000'))
                    new_hs = c2.text_input("HS Code", value=curr_hs, key=f"h_{unique_key}")
                    
                    curr_w = safe_float(row.get('Weight (kg)', 0))
                    new_weight = c3.number_input("중량 (kg)", value=curr_w, key=f"w_{unique_key}")
                    
                    recalc = calculate_tax_logic(new_mat, new_weight)
                    row.update({
                        'Material': new_mat, 'HS Code': new_hs, 'Weight (kg)': new_weight, 
                        'Default Tax (KRW)': recalc['bad_tax'], 'exchange_rate': recalc['exchange_rate']
                    })
                    updated_final_results.append(row)

            st.markdown("<br>", unsafe_allow_html=True)
            excel_data = generate_official_excel(updated_final_results)
            if excel_data:
                st.download_button("📥 엑셀 다운로드", data=excel_data, file_name=f"CBAM_Report_NOW.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", type="primary", use_container_width=True)

    with tab2:
        st.markdown("### 🕒 계산 기록 관리 (History)")
        st.caption("서버 데이터베이스에 영구 저장된 기록입니다. (로그아웃 해도 유지됨)")
        
        history_df = load_from_db(st.session_state['username'])
        
        if not history_df.empty:
            cols_to_show = ['Date', 'File Name', 'Item Name', 'Material', 'Weight (kg)', 'Default Tax (KRW)', 'HS Code']
            st.dataframe(history_df[cols_to_show], use_container_width=True)
            
            st.divider()
            c1, c2 = st.columns([1, 1])
            with c1:
                full_excel = generate_official_excel(history_df)
                st.download_button("📥 전체 기록 엑셀 다운로드", data=full_excel, file_name=f"CBAM_History_Full.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", type="primary", use_container_width=True)
            with c2:
                if st.button("🗑️ 기록 초기화 (주의)"):
                    clear_my_history(st.session_state['username'])
                    st.rerun()
        else:
            st.info("📭 저장된 기록이 없습니다.")
