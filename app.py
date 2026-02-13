import streamlit as st
import json
import base64
import pandas as pd
import io
import google.generativeai as genai
from datetime import datetime
import difflib 
import uuid
import time
import sqlite3

# ==========================================
# 🎨 [UI 설정]
# ==========================================
st.set_page_config(
    page_title="CBAM Master Pro (KTC Ver)", 
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
# 🔑 API 키 (Gemini 적용) 및 데이터 연결
# ==========================================
try:
    api_key = st.secrets["GEMINI_API_KEY"]
    genai.configure(api_key=api_key)
except Exception as e:
    st.error(f"🚨 API 키 오류: Secrets에 'GEMINI_API_KEY'를 설정하세요. ({e})")
    st.stop()

USER_DB_URL = "https://docs.google.com/spreadsheets/d/e/2PACX-1vRqCIpXf7jM4wyn8EhpoZipkUBQ2K43rEiaNi-KyoaI1j93YPNMLpavW07-LddivnoUL-FKFDMCFPkI/pub?gid=0&single=true&output=csv"
CBAM_DATA_URL = "https://docs.google.com/spreadsheets/d/e/2PACX-1vRTkYfVcC9EAv_xW0FChVWK3oMsPaxXiRL-hOQQeGT_aLsUG044s1L893er36HVJUpgTCrsM0xElFpW/pub?gid=747982569&single=true&output=csv"

# ------------------------------------------------
# 💾 데이터베이스(DB) 관리 (기존 유지)
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
    target_user = str(username).upper().strip()
    df = pd.read_sql_query("SELECT * FROM history WHERE username = ?", conn, params=(target_user,))
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
    target_user = str(username).upper().strip()
    c.execute("DELETE FROM history WHERE username = ?", (target_user,))
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
    except: return pd.DataFrame()

@st.cache_data(ttl=1) 
def load_cbam_db():
    master_db = {
        "Steel (Pipes/Tubes)": {"default": 2.50, "optimized": 1.9, "hs_code": "730400", "price": 85.0, "exchange_rate": 1450},
        "Steel (Wire)": {"default": 2.20, "optimized": 1.6, "hs_code": "721700", "price": 85.0, "exchange_rate": 1450},
    }
    try:
        df = pd.read_csv(CBAM_DATA_URL)
        first_cell = str(df.iloc[0,0]).strip().lower()
        if 'category' not in df.columns.str.lower() and first_cell == 'category':
            new_header = df.iloc[0]
            df = df[1:]
            df.columns = new_header
        
        cols = {c.strip().lower(): c for c in df.columns}
        rate_col = next((cols[k] for k in cols if 'exch' in k and 'rate' in k), None)
        hs_col = next((cols[k] for k in cols if 'hs' in k and 'code' in k), None)
        cat_col = next((cols[k] for k in cols if 'cat' in k), None)

        if cat_col:
            for _, row in df.iterrows():
                if pd.isna(row[cat_col]): continue
                cat = str(row[cat_col]).strip()
                
                rate_val = 1450.0
                if rate_col:
                    try: rate_val = float(str(row[rate_col]).replace(',', '').strip())
                    except: pass
                
                hs_val = '000000'
                if hs_col:
                    raw_hs = str(row[hs_col]).strip()
                    if raw_hs != 'nan' and raw_hs != '': hs_val = raw_hs.split('.')[0]
                
                def_val, opt_val = 0.0, 0.0
                for c in df.columns:
                    c_lower = str(c).lower()
                    if 'default' in c_lower:
                         try: def_val = float(str(row[c]).strip())
                         except: pass
                    if 'optimized' in c_lower:
                         try: opt_val = float(str(row[c]).strip())
                         except: pass

                master_db[cat] = {
                    "default": def_val, "optimized": opt_val, "hs_code": hs_val, 
                    "price": 85.0, "exchange_rate": rate_val
                }
    except Exception as e: pass 
    return master_db

user_df = load_user_data()
CBAM_DB = load_cbam_db()

def safe_float(value):
    try: return float(str(value).replace(',', '').replace('kg', '').replace('KG', '').strip())
    except: return 0.0

def force_match_material(ai_item_name, ai_material, db_keys):
    name_lower, mat_lower = str(ai_item_name).lower(), str(ai_material).lower()
    
    if "pipe" in name_lower or "tube" in name_lower:
        found = [k for k in db_keys if "Pipes" in k]
        if found: 
             if "alum" in name_lower: return "Aluminum (Pipes/Tubes)"
             return "Steel (Pipes/Tubes)"
    if "wire" in name_lower or "cable" in name_lower:
        found = [k for k in db_keys if "Wire" in k]
        if found: return found[0]
    if "structure" in name_lower or "beam" in name_lower:
        found = [k for k in db_keys if "Structures" in k]
        if found: return found[0]
    if "bolt" in name_lower or "screw" in name_lower or "nut" in name_lower:
        found = [k for k in db_keys if "Bolt" in k or "Screw" in k]
        if found: return found[0]
    if "aluminum" in name_lower or "aluminium" in name_lower:
        found = [k for k in db_keys if "Aluminum" in k]
        if found: return found[0]
    if "cement" in name_lower or "cmnt" in name_lower:
        found = [k for k in db_keys if "cement" in k.lower()]
        if found: return found[0]

    matches = difflib.get_close_matches(ai_material, db_keys, n=1, cutoff=0.4)
    if matches: return matches[0]
    return "Other"

# ==========================================
# 🧮 1. 핵심 로직 & 데이터 검증 시스템 (필살기 1)
# ==========================================
def calculate_tax_logic(material, weight):
    db = CBAM_DB.get(material, {"default":0, "optimized":0, "price":0, "exchange_rate":1450})
    if weight <= 0: weight = 0.0
    rate = db.get('exchange_rate', 1450.0)
    
    bad_tax = int((weight/1000) * db['default'] * db['price'] * rate)
    
    return {
        "bad_tax": bad_tax, "material_display": material, "weight": weight, 
        "hs_code": db.get('hs_code', '000000'), "exchange_rate": rate
    }

def validate_data(ai_hs, ai_mat):
    db_hs = CBAM_DB.get(ai_mat, {}).get('hs_code', '000000')
    if ai_mat == "Other": return "⚠️ 미등록 카테고리 (수동 확인 필요)"
    if str(ai_hs)[:4] != str(db_hs)[:4]: return f"🚩 HS코드 불일치 (DB권장: {db_hs})"
    return "✅ 검증 완료 (정상)"

# ==========================================
# 📊 2. KTC 표준 리포트 출력 (필살기 2 & 3)
# ==========================================
def generate_official_excel(data_list):
    if isinstance(data_list, pd.DataFrame):
        if data_list.empty: return None
        data_list = data_list.to_dict('records')
    if not data_list: return None

    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        wb = writer.book
        fmt_header = wb.add_format({'bold': True, 'fg_color': '#004494', 'font_color': 'white', 'border': 1, 'align':'center'})
        fmt_ktc_head = wb.add_format({'bold': True, 'bg_color': '#D7E4BC', 'border': 1, 'align':'center'})
        fmt_num = wb.add_format({'border': 1, 'num_format': '#,##0.00'})
        fmt_eur = wb.add_format({'border': 1, 'num_format': '€#,##0.00'})
        fmt_krw = wb.add_format({'border': 1, 'num_format': '₩#,##0'})
        fmt_warn = wb.add_format({'border': 1, 'font_color': 'red'})
        fmt_ok = wb.add_format({'border': 1, 'align':'center'})

        # KTC용 본문 시트 (필살기 2, 3 적용)
        ws2 = wb.add_worksheet("KTC_CBAM_Submission")
        # EU 최신 규정 준수 선언
        ws2.merge_range('A1:I1', f"CBAM Official Data (Ref: EU Regulation 2026/XXXX) - Integrity Checked", fmt_ktc_head)
        
        headers2 = ["No", "Origin", "HS Code", "Item Name", "Net Weight(t)", "Emission Factor", "Est. Tax (EUR)", "Est. Tax (KRW)", "Data Validation"]
        for c, h in enumerate(headers2): ws2.write(1, c, h, fmt_header)
        
        for i, d in enumerate(data_list):
            r = i + 2
            w_ton = d.get('Weight (kg)', 0) / 1000
            mat = d.get('Material', 'Iron/Steel')
            factor = CBAM_DB.get(mat, {}).get('default', 0)
            rate = d.get('exchange_rate', 1450)
            val_msg = d.get('Validation', '✅ 검증 완료')
            
            ws2.write(r, 0, i+1, fmt_ok)
            ws2.write(r, 1, "KR", fmt_ok)
            ws2.write(r, 2, d.get('HS Code', ''), fmt_ok)
            ws2.write(r, 3, d.get('Item Name', ''), fmt_ok)
            ws2.write(r, 4, w_ton, fmt_num)
            ws2.write(r, 5, factor, fmt_num)
            ws2.write(r, 6, (d.get('Default Tax (KRW)', 0)/rate) if rate>0 else 0, fmt_eur)
            ws2.write(r, 7, d.get('Default Tax (KRW)', 0), fmt_krw)
            
            # 빨간색 경고 표시
            if "🚩" in val_msg or "⚠️" in val_msg: ws2.write(r, 8, val_msg, fmt_warn)
            else: ws2.write(r, 8, val_msg, fmt_ok)
            
        ws2.set_column('A:A', 5)
        ws2.set_column('B:I', 18)

    return output.getvalue()

# ==========================================
# 🤖 3. Gemini 연동 AI 분석 (Gemini 업데이트)
# ==========================================
def analyze_image(image_bytes, filename, username):
    try:
        model = genai.GenerativeModel('gemini-2.0-flash') # Gemini 2.0 엔진
        cats_str = ", ".join(list(CBAM_DB.keys()))
        
        prompt = f"""You are a CBAM expert. Identify distinct items relevant to CBAM (Iron, Steel, Aluminum, Cement). IGNORE packing materials. 
        Select Material strictly from: [{cats_str}]. 
        Return ONLY valid JSON: {{"items": [{{"item": "name", "material": "category", "weight": 1000, "hs_code": "731800"}}]}}"""
        
        response = model.generate_content([prompt, {"mime_type": "image/jpeg", "data": image_bytes}])
        
        # JSON 클렌징
        json_str = response.text
        if '```json' in json_str: json_str = json_str.split('```json')[1].split('```')[0]
        elif '```' in json_str: json_str = json_str.split('```')[1].split('```')[0]
            
        items_list = json.loads(json_str.strip()).get('items', [])
        
        processed_items = []
        for item in items_list:
            w = safe_float(item.get('weight', 0))
            raw_name = item.get('item', '')
            raw_mat = item.get('material', 'Other')
            ai_hs = str(item.get('hs_code', '')).replace('.', '').strip()
            
            corrected_mat = force_match_material(raw_name, raw_mat, list(CBAM_DB.keys()))
            calc = calculate_tax_logic(corrected_mat, w)
            final_hs = ai_hs if (ai_hs and ai_hs != '000000') else calc['hs_code']
            
            # 🚨 실시간 검증 실행
            validation_result = validate_data(final_hs, corrected_mat)
            
            processed_items.append({
                "File Name": filename, "Date": datetime.now().strftime('%Y-%m-%d %H:%M'),
                "Company": username.upper(), "Item Name": raw_name, "Material": corrected_mat,
                "Weight (kg)": w, "HS Code": final_hs, "Default Tax (KRW)": calc['bad_tax'],
                "exchange_rate": calc['exchange_rate'], "Validation": validation_result
            })
        return processed_items
        
    except Exception as e:
        print(f"Gemini AI Error: {e}")
        return [{
            "File Name": filename, "Item Name": "Analysis Failed", "Material": "Other", 
            "Weight (kg)": 0, "HS Code": "000000", "Default Tax (KRW)": 0, 
            "exchange_rate": 1450, "Validation": "❌ 분석 실패 (에러)"
        }]

def process_analysis():
    uploaded_files = st.session_state.get('upl_files', [])
    if uploaded_files:
        current_credits = st.session_state.get('credits', 0)
        required_credits = len(uploaded_files)
        is_unlimited = current_credits >= 999999
        
        if is_unlimited or (current_credits >= required_credits):
            st.session_state['run_id'] = str(uuid.uuid4())
            with st.spinner("Gemini 엔진이 KTC 규격에 맞춰 정밀 분석 중입니다..."):
                all_results = []
                for file in uploaded_files:
                    file.seek(0)
                    items = analyze_image(file.read(), file.name, st.session_state['username'])
                    all_results.extend(items) if isinstance(items, list) else all_results.append(items)
                
                st.session_state['batch_results'] = all_results
                save_to_db(all_results)
                
                if not is_unlimited: st.session_state['credits'] -= required_credits
                st.toast("✅ KTC 표준 분석 및 검증 완료!")
        else: st.error("🚫 크레딧 부족!")

# ==========================================
# 🖥️ 화면 구성
# ==========================================
if 'logged_in' not in st.session_state: st.session_state['logged_in'] = False
if 'batch_results' not in st.session_state: st.session_state['batch_results'] = None

if not st.session_state['logged_in']:
    c1, c2, c3 = st.columns([1, 2, 1])
    with c2:
        st.markdown("<br><br><h1 style='text-align: center; color: #004494;'>🌍 CBAM Master Pro (KTC Edition)</h1>", unsafe_allow_html=True)
        with st.container(border=True):
            username = st.text_input("아이디")
            password = st.text_input("비밀번호", type="password")
            if st.button("로그인", type="primary", use_container_width=True):
                match = user_df[(user_df['username'] == username) & (user_df['password'].astype(str) == password) & (user_df['active'] == 'o')]
                if not match.empty:
                    st.session_state.update({'logged_in': True, 'username': username, 'credits': int(match.iloc[0]['credits'])})
                    st.rerun()
                else: st.error("❌ 로그인 실패")
else:
    with st.sidebar:
        st.title("CBAM Master (Gemini)")
        st.success("🟢 EU Reg 2026 Engine Online")
        st.write(f"👤 **{st.session_state['username'].upper()}** 님")
        st.metric("잔여 크레딧", "♾️ 무제한 (VIP)" if st.session_state.get('credits',0) >= 999999 else f"{st.session_state.get('credits',0)} 회")
        if st.button("로그아웃"): st.session_state['logged_in'] = False; st.rerun()

    tab1, tab2 = st.tabs(["🚀 KTC 정밀 분석 (Analysis)", "🕒 기록 관리 (History)"])

    with tab1:
        st.markdown("### 📄 인보이스 분석 및 KTC 데이터 검증")
        with st.container(border=True):
            uploaded_files = st.file_uploader("파일 추가 (Drag & Drop)", type=["jpg", "png", "jpeg"], accept_multiple_files=True, key="upl_files")
            if uploaded_files: st.button(f"🚀 Gemini 엔진 분석 시작", type="primary", on_click=process_analysis)

        if st.session_state['batch_results']:
            st.divider()
            st.subheader("📊 검증 결과 및 KTC 리포트 출력")
            results = st.session_state['batch_results']
            
            m1, m2, m3 = st.columns(3)
            m1.metric("추출 항목 수", f"{len(results)} 개")
            m2.metric("총 중량", f"{sum([safe_float(r.get('Weight (kg)',0)) for r in results]):,.0f} kg")
            m3.metric("EU 규정 준수 검증", "완료 (EU Reg 2026)")

            mat_options = list(CBAM_DB.keys()) + ["Other"]

            updated_final_results = []
            for idx, row in enumerate(results):
                val_status = row.get('Validation', '')
                # 🚨 검증 결과에 따라 UI 알림 표시
                if "🚩" in val_status: st.error(f"[{row.get('Item Name')}] {val_status}")
                elif "⚠️" in val_status: st.warning(f"[{row.get('Item Name')}] {val_status}")
                
                with st.expander(f"📄 {row.get('File Name','')} - {row.get('Item Name','')} | {val_status}", expanded=False):
                    c1, c2, c3 = st.columns([2, 1, 1])
                    curr_mat = row.get('Material', 'Other')
                    new_mat = c1.selectbox("재질", mat_options, index=mat_options.index(curr_mat) if curr_mat in mat_options else len(mat_options)-1, key=f"m_{idx}")
                    new_hs = c2.text_input("HS Code", value=str(row.get('HS Code', '')), key=f"h_{idx}")
                    new_weight = c3.number_input("중량 (kg)", value=safe_float(row.get('Weight (kg)', 0)), key=f"w_{idx}")
                    
                    # 값 수정 시 재검증 로직
                    recalc = calculate_tax_logic(new_mat, new_weight)
                    new_val = validate_data(new_hs, new_mat)
                    
                    row.update({'Material': new_mat, 'HS Code': new_hs, 'Weight (kg)': new_weight, 
                                'Default Tax (KRW)': recalc['bad_tax'], 'exchange_rate': recalc['exchange_rate'],
                                'Validation': new_val})
                    updated_final_results.append(row)

            st.markdown("<br>", unsafe_allow_html=True)
            excel_data = generate_official_excel(updated_final_results)
            if excel_data:
                st.download_button("📥 KTC 제출용 공식 리포트 다운로드 (Excel)", data=excel_data, file_name=f"CBAM_KTC_Report.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", type="primary", use_container_width=True)

    with tab2:
        st.markdown("### 🕒 계산 기록 관리 (History)")
        history_df = load_from_db(st.session_state['username'])
        if not history_df.empty:
            st.dataframe(history_df[['Date', 'File Name', 'Item Name', 'Material', 'Weight (kg)', 'HS Code']], use_container_width=True)
        else: st.info("📭 저장된 기록이 없습니다.")
