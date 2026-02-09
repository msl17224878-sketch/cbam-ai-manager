import streamlit as st
import json
import base64
import pandas as pd
import io
from openai import OpenAI
from datetime import datetime

# ==========================================
# 🎨 [UI 설정] 페이지 디자인 및 스타일링
# ==========================================
st.set_page_config(
    page_title="CBAM Master Pro", 
    page_icon="🌍", 
    layout="wide",
    initial_sidebar_state="expanded"
)

# 🖌️ 커스텀 CSS (불필요한 메뉴 숨김 & 디자인 강화)
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
    .big-font {
        font-size:18px !important;
        color: #333333;
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
        # 헤더 보정 로직 (category 오류 방지)
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

# ==========================================
# 🧮 핵심 로직 (계산 & 엑셀)
# ==========================================
def calculate_tax_logic(material, weight):
    db = CBAM_DB.get(material, CBAM_DB.get("Iron/Steel", {"default":0, "optimized":0, "price":0, "exchange_rate":1450}))
    if material == "Other": db = CBAM_DB.get("Other", {"default":0, "optimized":0, "price":0, "exchange_rate":1450})
    
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
        # 스타일 정의
        fmt_header = wb.add_format({'bold': True, 'text_wrap': True, 'valign': 'vcenter', 'fg_color': '#004494', 'font_color': 'white', 'border': 1})
        fmt_cell = wb.add_format({'border': 1, 'valign': 'vcenter'})
        fmt_num = wb.add_format({'border': 1, 'valign': 'vcenter', 'num_format': '#,##0.00'})
        fmt_eur = wb.add_format({'border': 1, 'valign': 'vcenter', 'num_format': '€#,##0.00'})
        fmt_krw = wb.add_format({'border': 1, 'valign': 'vcenter', 'num_format': '₩#,##0'})
        
        # Summary Sheet
        ws1 = wb.add_worksheet("Report_Summary")
        headers1 = ["Report Date", "Company", "Total Items", "Total Weight (Ton)", "Total Tax (EUR)", "Total Tax (KRW)"]
        
        t_tax_krw = sum([d.get('Default Tax (KRW)', 0) for d in data_list])
        t_tax_eur = sum([d.get('Default Tax (KRW)', 0) / d.get('exchange_rate', 1450) for d in data_list if d.get('exchange_rate', 0) > 0])
        
        for c, h in enumerate(headers1): ws1.write(0, c, h, fmt_header)
        ws1.write(1, 0, datetime.now().strftime('%Y-%m-%d'), fmt_cell)
        ws1.write(1, 1, data_list[0].get('Company', ''), fmt_cell)
        ws1.write(1, 2, len(data_list), fmt_cell)
        ws1.write(1, 3, sum([d.get('Weight (kg)', 0) for d in data_list])/1000, fmt_num)
        ws1.write(1, 4, t_tax_eur, fmt_eur)
        ws1.write(1, 5, t_tax_krw, fmt_krw)
        ws1.set_column('A:F', 22)

        # Data Sheet
        ws2 = wb.add_worksheet("CBAM_Data")
        headers2 = ["No", "Origin", "HS Code", "Item", "Weight (Ton)", "Emission Factor", "Total Emissions", "Est. Tax (EUR)", "Exch. Rate", "Est. Tax (KRW)"]
        for c, h in enumerate(headers2): ws2.write(0, c, h, fmt_header)
        
        for i, d in enumerate(data_list):
            r = i + 1
            w_ton = d.get('Weight (kg)', 0) / 1000
            mat = d.get('Material', 'Iron/Steel')
            db_info = CBAM_DB.get(mat, {})
            factor = db_info.get('default', 0)
            rate = db_info.get('exchange_rate', 1450)
            
            ws2.write(r, 0, r, fmt_cell)
            ws2.write(r, 1, "KR", fmt_cell)
            ws2.write(r, 2, d.get('HS Code', ''), fmt_cell)
            ws2.write(r, 3, d.get('Item Name', ''), fmt_cell)
            ws2.write(r, 4, w_ton, fmt_num)
            ws2.write(r, 5, factor, fmt_num)
            ws2.write(r, 6, w_ton * factor, fmt_num)
            ws2.write(r, 7, (d.get('Default Tax (KRW)', 0)/rate) if rate>0 else 0, fmt_eur)
            ws2.write(r, 8, rate, fmt_num)
            ws2.write(r, 9, d.get('Default Tax (KRW)', 0), fmt_krw)
        ws2.set_column('A:J', 18)
        
    return output.getvalue()

def analyze_image(image_bytes, filename, username):
    base64_image = base64.b64encode(image_bytes).decode('utf-8')
    try:
        cats = list(CBAM_DB.keys())
        response = client.chat.completions.create(
            model="gpt-4o", temperature=0.0,
            messages=[
                {"role": "system", "content": f"Classify into: {cats}. For others, use 'Other'. Return JSON: {{'item': '...', 'material': '...', 'weight': ...}} (weight in kg, number only)."},
                {"role": "user", "content": [{"type": "text", "text": "Analyze invoice."}, {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}}]}
            ],
            response_format={"type": "json_object"}
        )
        data = json.loads(response.choices[0].message.content)
        calc = calculate_tax_logic(data.get('material', 'Other'), data.get('weight', 0))
        data.update(calc)
        data.update({"File Name": filename, "Date": datetime.now().strftime('%Y-%m-%d'), "Company": username.upper()})
        return data
    except:
        return {"File Name": filename, "Item Name": "Error", "Material": "Other", "Weight (kg)": 0, "bad_tax": 0, "good_tax": 0, "savings": 0}

# ==========================================
# 🖥️ 화면 구성 (여기서부터 디자인 대개조)
# ==========================================

if 'logged_in' not in st.session_state: st.session_state['logged_in'] = False
if 'batch_results' not in st.session_state: st.session_state['batch_results'] = None

# --- [화면 1] 로그인 페이지 ---
if not st.session_state['logged_in']:
    c1, c2, c3 = st.columns([1, 2, 1])
    with c2:
        st.markdown("<br><br><h1 style='text-align: center; color: #004494;'>🌍 CBAM Master Pro</h1>", unsafe_allow_html=True)
        st.markdown("<p style='text-align: center; color: grey;'>EU 탄소국경조정제도 대응을 위한 AI 자동화 솔루션</p>", unsafe_allow_html=True)
        
        with st.container(border=True):
            username = st.text_input("아이디", placeholder="기업 아이디를 입력하세요")
            password = st.text_input("비밀번호", type="password", placeholder="비밀번호를 입력하세요")
            
            if st.button("로그인", type="primary", use_container_width=True):
                if not user_df.empty:
                    match = user_df[(user_df['username'] == username) & (user_df['password'].astype(str) == password) & (user_df['active'] == 'o')]
                    if not match.empty:
                        st.session_state['logged_in'] = True
                        st.session_state['username'] = username
                        st.rerun()
                    else:
                        st.error("❌ 로그인 정보가 올바르지 않습니다.")
                else:
                    st.error("⚠️ 시스템 점검 중")

# --- [화면 2] 메인 대시보드 ---
else:
    # 1. 사이드바 (사용자 정보)
    with st.sidebar:
        st.title("CBAM Master")
        st.success("🟢 System Online")
        st.divider()
        st.write(f"👤 **{st.session_state['username'].upper()}** 님")
        
        try:
            creds = int(user_df[user_df['username'] == st.session_state['username']].iloc[0]['credits'])
            st.metric("잔여 크레딧", f"{creds} 회")
        except:
            st.metric("잔여 크레딧", "0 회")
            
        st.markdown("---")
        if st.button("로그아웃"):
            st.session_state['logged_in'] = False
            st.rerun()

    # 2. 메인 헤더 & 상태창
    st.markdown("## 🏭 대시보드 (Dashboard)")
    
    # 🚨 [수정된 부분] 특정 이름(Iron/Steel) 대신, DB에 있는 첫 번째 품목의 환율을 가져오게 변경
    if CBAM_DB:
        first_item = list(CBAM_DB.keys())[0] # 목록의 첫 번째 놈을 잡음 (예: Steel (Bolts/Screws))
        krw_rate = CBAM_DB[first_item].get('exchange_rate', 1450)
    else:
        krw_rate = 1450

    st.info(f"💶 **실시간 환율 적용 중:** 1 EUR = **{krw_rate:,.2f} KRW** (Google Finance 연동됨)")

    # 3. 파일 업로드 섹션
    with st.container(border=True):
        st.subheader("📂 인보이스 업로드")
        uploaded_files = st.file_uploader("드래그 앤 드롭으로 파일을 추가하세요", type=["jpg", "png", "jpeg"], accept_multiple_files=True)
        
        if uploaded_files:
            st.write(f"총 {len(uploaded_files)}개의 파일이 선택되었습니다.")
            if st.button(f"🚀 AI 분석 시작 ({len(uploaded_files)} Credit 차감)", type="primary"):
                progress_text = "AI가 문서를 분석하고 배출량을 계산 중입니다..."
                my_bar = st.progress(0, text=progress_text)
                
                all_results = []
                for i, file in enumerate(uploaded_files):
                    res = analyze_image(file.read(), file.name, st.session_state['username'])
                    # 매핑
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
                st.toast("✅ 분석이 완료되었습니다!")
                st.rerun()

    # 4. 결과 리포트 및 수정 섹션
    if st.session_state['batch_results']:
        st.divider()
        st.subheader("📊 분석 결과 및 리포트 (Review)")
        
        results = st.session_state['batch_results']
        updated_final_results = []
        
        # 상단 요약 지표 (Metrics)
        total_tax_krw = sum([r.get('Default Tax (KRW)', 0) for r in results])
        total_weight = sum([float(r.get('Weight (kg)', 0)) for r in results])
        
        m1, m2, m3 = st.columns(3)
        m1.metric("총 항목 수", f"{len(results)} 개")
        m2.metric("총 중량", f"{total_weight:,.0f} kg")
        m3.metric("총 예상 세금 (KRW)", f"₩ {total_tax_krw:,.0f}")

        st.markdown("<br>", unsafe_allow_html=True)

        # 개별 항목 수정 카드
        mat_options = list(CBAM_DB.keys())
        if "Other" not in mat_options: mat_options.append("Other")

        for idx, row in enumerate(results):
            with st.expander(f"📄 {row['File Name']} : {row['Item Name']}", expanded=False):
                col_a, col_b, col_c = st.columns([2, 1, 1])
                
                # 재질 및 HS코드
                curr_mat = row.get('Material', 'Other')
                mat_idx = mat_options.index(curr_mat) if curr_mat in mat_options else mat_options.index("Other")
                new_mat = col_a.selectbox("품목 분류 (재질)", mat_options, index=mat_idx, key=f"m_{idx}")
                
                sugg_hs = CBAM_DB.get(new_mat, {}).get('hs_code', '000000')
                new_hs = col_b.text_input("HS Code", value=str(row.get('HS Code', sugg_hs)), key=f"h_{idx}")
                
                # 무게 안전 변환
                try:
                    w_val = float(str(row.get('Weight (kg)', 0)).replace(',','').replace('kg','').strip())
                except: w_val = 0.0
                new_weight = col_c.number_input("중량 (kg)", value=w_val, key=f"w_{idx}")
                
                # 재계산
                recalc = calculate_tax_logic(new_mat, new_weight)
                row.update({
                    'Material': new_mat, 'HS Code': new_hs, 'Weight (kg)': new_weight,
                    'Default Tax (KRW)': recalc['bad_tax'], 'exchange_rate': recalc['exchange_rate']
                })
                updated_final_results.append(row)
                
                st.caption(f"✔ 적용 환율: {recalc['exchange_rate']:,.2f} 원 | 배출계수: {CBAM_DB.get(new_mat, {}).get('default', 0)}")

        # 5. 다운로드 존
        st.divider()
        excel_data = generate_official_excel(updated_final_results)
        
        d1, d2 = st.columns([3, 1])
        with d1:
            st.info("💡 **Tip:** 최종 리포트는 EU CBAM 공식 제출 양식에 맞춰져 있습니다.")
        with d2:
            if excel_data:
                st.download_button(
                    label="📥 엑셀 리포트 다운로드",
                    data=excel_data,
                    file_name=f"CBAM_Report_{datetime.now().strftime('%Y%m%d')}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    type="primary",
                    use_container_width=True
                )

