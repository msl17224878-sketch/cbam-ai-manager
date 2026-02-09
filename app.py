import streamlit as st
import json
import base64
import pandas as pd
import io
from openai import OpenAI
from datetime import datetime

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
# 🔑 API 키 설정
# ==========================================
try:
    api_key = st.secrets["OPENAI_API_KEY"]
    client = OpenAI(api_key=api_key)
except Exception as e:
    st.error(f"🚨 시스템 설정 오류: API 키를 확인하세요. ({e})")
    st.stop()

# ==========================================
# 📡 데이터 연결 (구글 시트)
# ==========================================
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
        # 헤더 보정 로직
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
    except Exception as e:
        return {}

user_df = load_user_data()
CBAM_DB = load_cbam_db()

# ------------------------------------------------
# 🛠️ [핵심 수정] 숫자 안전 변환 함수 (에러 방지용)
# ------------------------------------------------
def safe_float(value):
    try:
        # 쉼표, 문자(kg) 제거 후 실수 변환
        clean_val = str(value).replace(',', '').replace('kg', '').replace('KG', '').strip()
        return float(clean_val)
    except:
        return 0.0

# ==========================================
# 🧮 핵심 로직
# ==========================================
def calculate_tax_logic(material, weight):
    # DB에 없는 재질이면 첫 번째 품목이나 기본값 사용
    if material in CBAM_DB:
        db = CBAM_DB[material]
    elif CBAM_DB:
        first_key = list(CBAM_DB.keys())[0]
        db = CBAM_DB[first_key]
    else:
        db = {"default":0, "optimized":0, "price":0, "exchange_rate":1450}

    if material == "Other": 
        db = CBAM_DB.get("Other", {"default":0, "optimized":0, "price":0, "exchange_rate":1450})
    
    if weight <= 0: weight = 1
    rate = db.get('exchange_rate', 1450.0)
    
    bad_tax = int((weight/1000) * db['default'] * db['price'] * rate)
    good_tax = int((weight/1000) * db['optimized'] * db['price'] * rate)
    
    return {
        "bad_tax": bad_tax, "good_tax": good_tax, "savings": bad_tax - good_tax,
        "material_display": material, "weight": weight, "hs_code": db.get('hs_code', '000000'), "exchange_rate": rate
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
        
        # Summary
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

        # Data
        ws2 = wb.add_worksheet("CBAM_Data")
        headers2 = ["No", "Origin", "HS Code", "Item", "Weight (Ton)", "Emission Factor", "Est. Tax (EUR)", "Exch. Rate", "Est. Tax (KRW)"]
        for c, h in enumerate(headers2): ws2.write(0, c, h, fmt_header)
        
        for i, d in enumerate(data_list):
            r = i + 1
            w_ton = d.get('Weight (kg)', 0) / 1000
            mat = d.get('Material', 'Iron/Steel')
            
            # DB 조회 안전장치
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

# ------------------------------------------------
# 🧪 [테스트 모드] AI 없이 가짜 데이터로 테스트하기
# ------------------------------------------------
def analyze_image(image_bytes, filename, username):
    # 원래는 여기서 이미지를 AI에게 보내지만, 지금은 토큰이 없으니 생략합니다.
    # base64_image = base64.b64encode(image_bytes).decode('utf-8')
    
    import random
    import time
    
    # 1. AI가 생각하는 척 (1초 딜레이)
    time.sleep(1.0) 
    
    # 2. 가짜 결과 생성 (사장님 구글 시트에 있는 품목명 중 하나를 넣으세요!)
    # 랜덤으로 무게와 품목을 바꿔가며 테스트해볼 수 있습니다.
    
    mock_data = {
        "item": "TEST_Bolt_Sample",  # 가짜 품목명
        "material": "Steel (Bolts/Screws)", # 🚨 중요: 구글 시트에 있는 정확한 이름이어야 함!
        "weight": 1500, # 가짜 무게 (1500kg)
        "hs_code": "731800" # 가짜 HS코드
    }
    
    # 3. 계산 로직 태우기 (이건 진짜로 돌아갑니다)
    try:
        # 안전 변환 (위에서 만든 safe_float 함수가 있다고 가정)
        weight_val = float(mock_data['weight'])
        
        calc = calculate_tax_logic(mock_data['material'], weight_val)
        mock_data.update(calc)
        
        # 메타데이터 추가
        mock_data.update({
            "File Name": filename, 
            "Date": datetime.now().strftime('%Y-%m-%d'), 
            "Company": username.upper()
        })
        
        return mock_data
        
    except Exception as e:
        print(f"테스트 모드 에러: {e}")
        return {"File Name": filename, "Item Name": "Error", "Material": "Other", "Weight (kg)": 0, "bad_tax": 0, "good_tax": 0}
# ==========================================
# 🖥️ 화면 구성
# ==========================================

if 'logged_in' not in st.session_state: st.session_state['logged_in'] = False
if 'batch_results' not in st.session_state: st.session_state['batch_results'] = None

# --- [화면 1] 로그인 ---
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
                        st.rerun()
                    else:
                        st.error("❌ 로그인 실패")
                else:
                    st.error("⚠️ DB 연결 실패")

# --- [화면 2] 대시보드 ---
else:
    with st.sidebar:
        st.title("CBAM Master")
        st.success("🟢 System Online")
        st.divider()
        st.write(f"👤 **{st.session_state['username'].upper()}** 님")
        try: creds = int(user_df[user_df['username'] == st.session_state['username']].iloc[0]['credits'])
        except: creds = 0
        st.metric("잔여 크레딧", f"{creds} 회")
        st.markdown("---")
        if st.button("로그아웃"):
            st.session_state['logged_in'] = False
            st.rerun()

    st.markdown("## 🏭 대시보드 (Dashboard)")
    
    # 💰 [수정 완료] 실시간 환율 표시 (DB 첫번째 항목 기준)
    if CBAM_DB:
        first_item = list(CBAM_DB.keys())[0]
        krw_rate = CBAM_DB[first_item].get('exchange_rate', 1450)
    else:
        krw_rate = 1450
    st.info(f"💶 **실시간 환율 적용 중:** 1 EUR = **{krw_rate:,.2f} KRW** (Google Finance 연동됨)")

    with st.container(border=True):
        st.subheader("📂 인보이스 업로드")
        uploaded_files = st.file_uploader("파일 추가", type=["jpg", "png", "jpeg"], accept_multiple_files=True)
        
        if uploaded_files:
            if st.button(f"🚀 AI 분석 시작", type="primary"):
                progress_text = "AI 분석 중..."
                my_bar = st.progress(0, text=progress_text)
                all_results = []
                for i, file in enumerate(uploaded_files):
                    res = analyze_image(file.read(), file.name, st.session_state['username'])
                    mapped = res.copy()
                    mapped["Default Tax (KRW)"] = res.get("bad_tax")
                    mapped["Item Name"] = res.get("item")
                    mapped["Material"] = res.get("material_display")
                    mapped["Weight (kg)"] = res.get("weight")
                    mapped["HS Code"] = res.get("hs_code")
                    mapped["exchange_rate"] = res.get("exchange_rate")
                    all_results.append(mapped)
                    my_bar.progress((i + 1) / len(uploaded_files))
                
                st.session_state['batch_results'] = all_results
                st.rerun()

    if st.session_state['batch_results']:
        st.divider()
        st.subheader("📊 분석 결과 (Review)")
        results = st.session_state['batch_results']
        updated_final_results = []
        
        # 🚨 [수정 완료] 합계 계산 시 안전 변환 적용 (에러 원인 해결)
        total_tax_krw = sum([r.get('Default Tax (KRW)', 0) for r in results])
        total_weight = sum([safe_float(r.get('Weight (kg)', 0)) for r in results])
        
        m1, m2, m3 = st.columns(3)
        m1.metric("총 항목 수", f"{len(results)} 개")
        m2.metric("총 중량", f"{total_weight:,.0f} kg")
        m3.metric("총 예상 세금", f"₩ {total_tax_krw:,.0f}")

        st.markdown("<br>", unsafe_allow_html=True)

        mat_options = list(CBAM_DB.keys())
        if "Other" not in mat_options: mat_options.append("Other")

        for idx, row in enumerate(results):
            with st.expander(f"📄 {row['File Name']} : {row['Item Name']}", expanded=False):
                c1, c2, c3 = st.columns([2, 1, 1])
                curr_mat = row.get('Material', 'Other')
                if curr_mat not in mat_options: curr_mat = "Other"
                
                new_mat = c1.selectbox("재질", mat_options, index=mat_options.index(curr_mat), key=f"m_{idx}")
                sugg_hs = CBAM_DB.get(new_mat, {}).get('hs_code', '000000')
                new_hs = c2.text_input("HS Code", value=str(row.get('HS Code', sugg_hs)), key=f"h_{idx}")
                
                # 🚨 [수정 완료] 개별 수정 시에도 안전 변환 적용
                w_val = safe_float(row.get('Weight (kg)', 0))
                new_weight = c3.number_input("중량 (kg)", value=w_val, key=f"w_{idx}")
                
                recalc = calculate_tax_logic(new_mat, new_weight)
                row.update({
                    'Material': new_mat, 'HS Code': new_hs, 'Weight (kg)': new_weight,
                    'Default Tax (KRW)': recalc['bad_tax'], 'exchange_rate': recalc['exchange_rate']
                })
                updated_final_results.append(row)

        st.divider()
        excel_data = generate_official_excel(updated_final_results)
        if excel_data:
            st.download_button("📥 엑셀 리포트 다운로드", data=excel_data, file_name="CBAM_Report.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", type="primary", use_container_width=True)

