import streamlit as st
import json
import base64
import pandas as pd
import io
from openai import OpenAI
from datetime import datetime
import difflib 

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
            db[cat] = {
                "default": float(row.get('default', 0)), 
                "optimized": float(row.get('optimized', 0)), 
                "hs_code": str(row.get('hs_code', '000000')).split('.')[0], 
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

# ------------------------------------------------
# 🕵️‍♂️ [업그레이드] 강제 매칭 함수 (Keyword Matching)
# ------------------------------------------------
def force_match_material(ai_item_name, ai_material, db_keys):
    # 1. AI가 찾아온 품목명(Item Name)을 소문자로 변환
    name_lower = str(ai_item_name).lower()
    mat_lower = str(ai_material).lower()
    
    # 2. 키워드 검사 (여기가 핵심!)
    # 나사, 볼트, 스크류가 들어있으면 무조건 Steel (Bolts/Screws)로 연결
    if "bolt" in name_lower or "screw" in name_lower:
        # DB 키 중에 'Bolt'가 포함된 놈을 찾음
        found = [k for k in db_keys if "Bolt" in k or "Screw" in k]
        if found: return found[0]
        
    # 알루미늄이 들어있으면 DB의 Aluminum (Bars...) 등으로 연결
    if "aluminum" in name_lower or "aluminium" in name_lower:
        found = [k for k in db_keys if "Aluminum" in k]
        if found: return found[0]
        
    # 시트(Sheet), 플레이트(Plate) 확인
    if "sheet" in name_lower or "plate" in name_lower:
        found = [k for k in db_keys if "Sheet" in k or "Plate" in k]
        if found: return found[0]

    # 3. 키워드로 못 찾으면 difflib(유사도) 사용
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
            if mat in CBAM_DB: db_info = CBAM_DB[mat]
            elif CBAM_DB: db_info = CBAM_DB[list(CBAM_DB.keys())[0]]
            else: db_info = {'default':0, 'exchange_rate':1450}
            factor = db_info.get('default', 0)
            rate = db_info.get('exchange_rate', 1450)
            
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
        response = client.chat.completions.create(
            model="gpt-4o", 
            temperature=0.0, 
            messages=[
                {
                    "role": "system", 
                    "content": f"You are a CBAM expert. Identify ALL distinct items. For each item, select the Material Category STRICTLY from this list: [{cats_str}]. If unsure, use 'Other'. Extract 'Net Weight' in kg. Return JSON: {{'items': [{{'item': 'Item Name', 'material': 'Selected Category', 'weight': 1000, 'hs_code': '000000'}}, ...]}}."
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
            
            # 🚨 [여기 수정됨] 강제 매칭 로직 적용
            # AI가 'Steel Bolt'라고 가져오면 -> 'Steel (Bolts/Screws)'로 바꿈
            raw_item_name = item.get('item', '')
            raw_material = item.get('material', 'Other')
            
            corrected_mat = force_match_material(raw_item_name, raw_material, list(CBAM_DB.keys()))
            
            calc = calculate_tax_logic(corrected_mat, w)
            
            processed_items.append({
                "File Name": filename,
                "Date": datetime.now().strftime('%Y-%m-%d'),
                "Company": username.upper(),
                "Item Name": raw_item_name,
                "Material": corrected_mat, # 보정된 재질 이름
                "Weight (kg)": w,
                "HS Code": item.get('hs_code', calc['hs_code']),
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
# 🖥️ 화면 구성
# ==========================================

if 'logged_in' not in st.session_state: st.session_state['logged_in'] = False
if 'batch_results' not in st.session_state: st.session_state['batch_results'] = None

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
        if st.button("로그아웃"):
            st.session_state['logged_in'] = False
            st.rerun()

    st.markdown("## 🏭 대시보드 (Dashboard)")
    if CBAM_DB: krw_rate = CBAM_DB[list(CBAM_DB.keys())[0]].get('exchange_rate', 1450)
    else: krw_rate = 1450
    st.info(f"💶 **실시간 환율 적용 중:** 1 EUR = **{krw_rate:,.2f} KRW** (Google Finance 연동)")

    with st.container(border=True):
        st.subheader("📂 인보이스 업로드 (다중 품목 인식 지원)")
        uploaded_files = st.file_uploader("파일 추가", type=["jpg", "png", "jpeg"], accept_multiple_files=True)
        
        if uploaded_files:
            current_credits = st.session_state.get('credits', 0)
            required_credits = len(uploaded_files)
            is_unlimited = current_credits >= 999999
            can_run = is_unlimited or (current_credits >= required_credits)
            
            if can_run:
                if st.button(f"🚀 AI 분석 시작", type="primary"):
                    progress_text = "AI가 문서 내 모든 품목을 스캔 중입니다..."
                    my_bar = st.progress(0, text=progress_text)
                    all_results = []
                    
                    for i, file in enumerate(uploaded_files):
                        items = analyze_image(file.read(), file.name, st.session_state['username'])
                        if isinstance(items, list): all_results.extend(items)
                        else: all_results.append(items)
                        my_bar.progress((i + 1) / len(uploaded_files))
                    
                    st.session_state['batch_results'] = all_results
                    if not is_unlimited:
                        st.session_state['credits'] -= required_credits
                        st.toast(f"💳 {required_credits} 크레딧 차감 완료")
                    st.rerun()
            else:
                st.error(f"🚫 **크레딧 부족!**")

    if st.session_state['batch_results']:
        st.divider()
        st.subheader("📊 분석 결과 (Review)")
        results = st.session_state['batch_results']
        
        total_tax_krw = sum([r.get('Default Tax (KRW)', 0) for r in results])
        total_weight = sum([safe_float(r.get('Weight (kg)', 0)) for r in results])
        
        m1, m2, m3 = st.columns(3)
        m1.metric("총 항목 수", f"{len(results)} 개")
        m2.metric("총 중량", f"{total_weight:,.0f} kg")
        m3.metric("총 예상 세금", f"₩ {total_tax_krw:,.0f}")

        st.markdown("<br>", unsafe_allow_html=True)
        mat_options = list(CBAM_DB.keys())
        if "Other" not in mat_options: mat_options.append("Other")

        updated_final_results = []
        for idx, row in enumerate(results):
            with st.expander(f"📄 {row.get('File Name','')} - {row.get('Item Name','Unknown')} ({row.get('Weight (kg)',0)}kg)", expanded=False):
                c1, c2, c3 = st.columns([2, 1, 1])
                
                curr_mat = row.get('Material', 'Other')
                if curr_mat not in mat_options: curr_mat = "Other"
                new_mat = c1.selectbox("재질", mat_options, index=mat_options.index(curr_mat), key=f"m_{idx}")
                
                curr_hs = str(row.get('HS Code', '000000'))
                new_hs = c2.text_input("HS Code", value=curr_hs, key=f"h_{idx}")
                
                curr_w = safe_float(row.get('Weight (kg)', 0))
                new_weight = c3.number_input("중량 (kg)", value=curr_w, key=f"w_{idx}")
                
                recalc = calculate_tax_logic(new_mat, new_weight)
                row.update({
                    'Material': new_mat, 'HS Code': new_hs, 'Weight (kg)': new_weight, 
                    'Default Tax (KRW)': recalc['bad_tax'], 'exchange_rate': recalc['exchange_rate']
                })
                updated_final_results.append(row)

        st.divider()
        excel_data = generate_official_excel(updated_final_results)
        if excel_data:
            st.download_button("📥 엑셀 리포트 다운로드", data=excel_data, file_name=f"CBAM_Report_{datetime.now().strftime('%Y%m%d')}.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", type="primary", use_container_width=True)
