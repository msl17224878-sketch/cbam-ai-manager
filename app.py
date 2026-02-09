import streamlit as st
import json
import base64
import pandas as pd
import io
from openai import OpenAI
from datetime import datetime

# ==========================================
# ⚙️ [설정] 페이지 기본 설정
# ==========================================
st.set_page_config(page_title="AI CBAM Master", page_icon="🌍", layout="wide")

# ==========================================
# 🔑 API 키 설정
# ==========================================
try:
    api_key = st.secrets["OPENAI_API_KEY"]
    client = OpenAI(api_key=api_key)
except FileNotFoundError:
    st.error("🚨 API 키가 설정되지 않았습니다! [Settings] > [Secrets]에 OPENAI_API_KEY를 추가하세요.")
    st.stop()
except Exception as e:
    st.error(f"🚨 설정 오류: {e}")
    st.stop()

# ==========================================
# 📡 [핵심] 구글 시트 연동 (2개 채널)
# ==========================================

# 1. 고객 장부 (아이디/비번/크레딧)
USER_DB_URL = "https://docs.google.com/spreadsheets/d/e/2PACX-1vRqCIpXf7jM4wyn8EhpoZipkUBQ2K43rEiaNi-KyoaI1j93YPNMLpavW07-LddivnoUL-FKFDMCFPkI/pub?gid=0&single=true&output=csv"

# 2. 규정 장부 (배출계수/HS코드/환율) - 사장님이 방금 세팅한 그 시트!
# 🚨 주의: 만약 'items' 탭을 새로 만들어서 주소가 바뀌었다면, 아래 주소를 새 CSV 링크로 꼭 바꿔주세요!
# (기존 주소 그대로라면 두셔도 됩니다)
CBAM_DATA_URL = "https://docs.google.com/spreadsheets/d/e/2PACX-1vRTkYfVcC9EAv_xW0FChVWK3oMsPaxXiRL-hOQQeGT_aLsUG044s1L893er36HVJUpgTCrsM0xElFpW/pub?gid=747982569&single=true&output=csv"

# ------------------------------------
# 1️⃣ 고객 데이터 로드 함수
# ------------------------------------
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
    except Exception:
        return pd.DataFrame()

# ------------------------------------
# 2️⃣ CBAM 규정 데이터 로드 함수 (환율 적용됨!)
# ------------------------------------
@st.cache_data(ttl=300) # 5분마다 갱신
def load_cbam_db():
    try:
        df = pd.read_csv(CBAM_DATA_URL)
        df.columns = df.columns.str.strip().str.lower()
        
        db = {}
        for _, row in df.iterrows():
            cat = str(row['category']).strip()
            # 💰 여기서 구글 시트의 실시간 환율(exchange_rate)을 가져옵니다!
            # 만약 시트에 값이 없으면 기본값 1450원 사용
            rate = float(row.get('exchange_rate', 1450.0))

            db[cat] = {
                "default": float(row.get('default', 0)),
                "optimized": float(row.get('optimized', 0)),
                "hs_code": str(row.get('hs_code', '000000')).split('.')[0], 
                "price": 85.0,
                "exchange_rate": rate # 저장
            }
        return db
    except Exception as e:
        print(f"⚠️ 규정 데이터 로드 실패: {e}")
        # 비상용 기본값
        return {
            "Iron/Steel": {"default": 2.5, "optimized": 0.5, "hs_code": "731800", "price": 85.0, "exchange_rate": 1450.0},
            "Aluminum": {"default": 8.0, "optimized": 1.5, "hs_code": "760400", "price": 85.0, "exchange_rate": 1450.0},
            "Other": {"default": 0.0, "optimized": 0.0, "hs_code": "000000", "price": 0.0, "exchange_rate": 1450.0}
        }

# 데이터 불러오기
user_df = load_user_data()
CBAM_DB = load_cbam_db()

# ------------------------------------------------
# 🧮 세금 계산 로직 (실시간 환율 반영)
# ------------------------------------------------
def calculate_tax_logic(material, weight):
    if material in CBAM_DB:
        db = CBAM_DB[material]
    else:
        # DB에 없는 재질이면 Iron/Steel 또는 기본값 사용
        if "Iron/Steel" in CBAM_DB:
            db = CBAM_DB["Iron/Steel"]
        else:
            db = {"default": 0, "optimized": 0, "price": 0, "exchange_rate": 1450}
    
    # 면제(Other) 처리
    if material == "Other":
        if "Other" in CBAM_DB:
            db = CBAM_DB["Other"]
        else:
            db = {"default": 0, "optimized": 0, "price": 0, "exchange_rate": 1450}

    if weight <= 0: weight = 1
    
    # 💰 실시간 환율 적용
    exchange_rate = db.get('exchange_rate', 1450.0)
    
    # 계산식: (무게/1000) * 배출계수 * 탄소가격(85유로) * 환율
    bad_tax = int((weight/1000) * db['default'] * db['price'] * exchange_rate)
    good_tax = int((weight/1000) * db['optimized'] * db['price'] * exchange_rate)
    
    return {
        "bad_tax": bad_tax,
        "good_tax": good_tax,
        "savings": bad_tax - good_tax,
        "material_display": material,
        "weight": weight,
        "hs_code": db.get('hs_code', '000000'),
        "exchange_rate": exchange_rate # 화면 표시용
    }

# ------------------------------------------------
# 🇪🇺 EU 공식 양식 엑셀 생성
# ------------------------------------------------
def generate_official_excel(data_list):
    if not data_list:
        return None
        
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        workbook = writer.book
        
        # 스타일
        header_format = workbook.add_format({
            'bold': True, 'text_wrap': True, 'valign': 'vcenter', 'fg_color': '#004494', 'font_color': 'white', 'border': 1})
        cell_format = workbook.add_format({'border': 1, 'valign': 'vcenter'})
        num_format = workbook.add_format({'border': 1, 'valign': 'vcenter', 'num_format': '#,##0.00'})
        krw_format = workbook.add_format({'border': 1, 'valign': 'vcenter', 'num_format': '#,##0'})
        
        # 1. Summary 시트
        ws_summary = workbook.add_worksheet("Report_Summary")
        summary_headers = ["Report Date", "Company", "Total Items", "Total Weight (Ton)", "Total Est. Tax (KRW)"]
        
        total_items = len(data_list)
        total_weight_ton = sum([d.get('Weight (kg)', 0) for d in data_list]) / 1000
        total_tax = sum([d.get('Default Tax (KRW)', 0) for d in data_list])
        company_name = data_list[0].get('Company', 'Unknown') if data_list else ""
        
        for col, h in enumerate(summary_headers):
            ws_summary.write(0, col, h, header_format)
        
        ws_summary.write(1, 0, datetime.now().strftime('%Y-%m-%d'), cell_format)
        ws_summary.write(1, 1, company_name, cell_format)
        ws_summary.write(1, 2, total_items, cell_format)
        ws_summary.write(1, 3, total_weight_ton, num_format)
        ws_summary.write(1, 4, total_tax, krw_format)
        ws_summary.set_column('A:E', 25)

        # 2. Data 시트
        ws_data = workbook.add_worksheet("CBAM_Data_For_Submission")
        data_headers = [
            "Line No", "Origin Country", "CN Code (HS Code)", "Goods Name", 
            "Net Mass (Tonnes)", "Direct Emissions (tCO2e/t)", "Total Emissions (tCO2e)", 
            "Applied Exch. Rate", "Est. Tax (KRW)"
        ]
        
        for col, h in enumerate(data_headers):
            ws_data.write(0, col, h, header_format)
            
        for row_idx, data in enumerate(data_list):
            row = row_idx + 1
            weight_ton = data.get('Weight (kg)', 0) / 1000
            
            mat = data.get('Material', 'Iron/Steel')
            factor = 0
            rate = 1450.0
            
            # DB에서 값 조회 (환율 포함)
            if mat in CBAM_DB:
                factor = CBAM_DB[mat]['default']
                rate = CBAM_DB[mat]['exchange_rate']
            
            total_emissions = weight_ton * factor
            
            ws_data.write(row, 0, row, cell_format)
            ws_data.write(row, 1, "KR (Korea)", cell_format)
            ws_data.write(row, 2, data.get('HS Code', '000000'), cell_format)
            ws_data.write(row, 3, data.get('Item Name', ''), cell_format)
            ws_data.write(row, 4, weight_ton, num_format)
            ws_data.write(row, 5, factor, num_format)
            ws_data.write(row, 6, total_emissions, num_format)
            ws_data.write(row, 7, rate, num_format) # 환율 정보 추가!
            ws_data.write(row, 8, data.get('Default Tax (KRW)', 0), krw_format)
            
        ws_data.set_column('A:I', 20)
        
    return output.getvalue()

# ------------------------------------------------
# 🧠 AI 분석 함수
# ------------------------------------------------
def analyze_image(image_bytes, filename, username):
    base64_image = base64.b64encode(image_bytes).decode('utf-8')
    try:
        categories = list(CBAM_DB.keys())
        
        response = client.chat.completions.create(
            model="gpt-4o",
            temperature=0.0,
            messages=[
                {
                    "role": "system", 
                    "content": f"""
                    You are a CBAM Classifier AI.
                    Step 1: Identify Product Item.
                    Step 2: Classify Material into: {categories}.
                    🚨 For Fish, Food, Wood, Plastic, Textile -> YOU MUST CLASSIFY AS "Other".
                    Step 3: Extract Weight (Convert to KG).
                    Output JSON: {{"item": "...", "material": "...", "weight": ...}}
                    """
                },
                {"role": "user", "content": [{"type": "text", "text": "Analyze this image."}, {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}}]}
            ],
            response_format={"type": "json_object"}
        )
        data = json.loads(response.choices[0].message.content)
        
        calc = calculate_tax_logic(data.get('material', 'Other'), data.get('weight', 0))
        data.update(calc)
        
        if not data.get('item'): data['item'] = "Unidentified"
        data["File Name"] = filename
        data["Date"] = datetime.now().strftime('%Y-%m-%d')
        data["Company"] = username.upper()
        return data
    except Exception as e:
        return {"File Name": filename, "Item Name": "Error", "Material": "Other", "Weight (kg)": 0, "bad_tax": 0, "good_tax": 0, "savings": 0}

# ==========================================
# 🖥️ 메인 화면 로직
# ==========================================

if 'logged_in' not in st.session_state:
    st.session_state['logged_in'] = False
if 'batch_results' not in st.session_state:
    st.session_state['batch_results'] = None

# 1️⃣ 로그인 화면
if not st.session_state['logged_in']:
    st.title("🔒 기업 회원 로그인")
    st.caption("구글 시트에 등록된 계정으로 로그인하세요.")
    
    with st.form("login_form"):
        username = st.text_input("아이디").strip()
        password = st.text_input("비밀번호", type="password").strip()
        submit = st.form_submit_button("로그인")
        
        if submit:
            if not user_df.empty:
                match = user_df[(user_df['username'] == username) & 
                                (user_df['password'].astype(str) == password) & 
                                (user_df['active'] == 'o')]
                if not match.empty:
                    st.session_state['logged_in'] = True
                    st.session_state['username'] = username
                    st.rerun()
                else:
                    st.error("로그인 실패: 계정 정보를 확인하세요.")
            else:
                st.error("시스템 DB 연결 실패")

# 2️⃣ 메인 대시보드
else:
    try:
        current_user_info = user_df[user_df['username'] == st.session_state['username']].iloc[0]
        user_credits = int(current_user_info['credits'])
    except:
        user_credits = 0

    with st.sidebar:
        st.write(f"👤 **{st.session_state['username'].upper()}** 님")
        
        if user_credits > 0:
            st.success(f"🪙 잔여 크레딧: **{user_credits}**회")
        else:
            st.error("❌ 크레딧 부족")
            st.info("충전 문의: 010-0000-0000")

        if st.button("로그아웃"):
            st.session_state['logged_in'] = False
            st.session_state['batch_results'] = None
            st.rerun()

    st.title("🏭 CBAM 규제 판독 시스템 (Ver 1.0)")
    st.info(f"💡 실시간 환율 적용 중 (1 EUR = {CBAM_DB.get('Iron/Steel', {}).get('exchange_rate', 1450):,.2f} KRW)")

    uploaded_files = st.file_uploader("수출 서류 업로드", type=["jpg", "png", "jpeg"], accept_multiple_files=True)

    if uploaded_files:
        if st.session_state['batch_results'] and len(uploaded_files) != len(st.session_state['batch_results']):
             st.session_state['batch_results'] = None

    if uploaded_files and len(uploaded_files) > 0:
        file_count = len(uploaded_files)
        
        if user_credits < file_count:
            st.warning(f"⚠️ 크레딧이 부족합니다. (보유: {user_credits} / 필요: {file_count})")
        else:
            if st.button(f"🚀 {file_count}건 판독 시작"):
                progress_bar = st.progress(0)
                all_results = []
                
                for i, file in enumerate(uploaded_files):
                    file.seek(0)
                    with st.spinner(f"{file.name} 분석 중..."):
                        res = analyze_image(file.read(), file.name, st.session_state['username'])
                        mapped = res.copy()
                        mapped["Default Tax (KRW)"] = res.get("bad_tax")
                        mapped["Optimized Tax (KRW)"] = res.get("good_tax")
                        mapped["Savings (KRW)"] = res.get("savings")
                        mapped["Item Name"] = res.get("item")
                        mapped["Material"] = res.get("material_display")
                        mapped["Weight (kg)"] = res.get("weight")
                        mapped["HS Code"] = res.get("hs_code")
                        all_results.append(mapped)
                    progress_bar.progress((i + 1) / len(uploaded_files))
                
                st.session_state['batch_results'] = all_results
                st.toast("판독 완료! 결과를 확인하세요.")
                st.rerun()

    # 결과 리포트 및 수정
    if st.session_state['batch_results']:
        st.divider()
        st.subheader("📝 데이터 검증 (EU 제출용)")
        st.caption("AI가 인식한 데이터를 검토하고 수정하세요.")
        
        results = st.session_state['batch_results']
        updated_final_results = []

        mat_options = list(CBAM_DB.keys())
        if "Other" not in mat_options: mat_options.append("Other")

        for idx, row in enumerate(results):
            with st.expander(f"[{idx+1}] {row['File Name']} - {row['Item Name']}", expanded=True):
                c1, c2, c3, c4 = st.columns([1.5, 1.5, 1, 1.5])
                
                # 1. 재질 선택
                current_mat = row.get('Material', 'Other')
                mat_index = mat_options.index(current_mat) if current_mat in mat_options else mat_options.index("Other")
                new_mat = c1.selectbox("재질", mat_options, index=mat_index, key=f"mat_{idx}")
                
                # 2. HS Code 수정
                suggested_hs = CBAM_DB.get(new_mat, {}).get('hs_code', '000000')
                current_hs = row.get('HS Code', suggested_hs)
                new_hs = c2.text_input("CN Code (HS 6단위)", value=str(current_hs), key=f"hs_{idx}")
                
                # 3. 무게 안전 수정 (문자/쉼표 제거)
                raw_weight = row.get('Weight (kg)', 0)
                try:
                    if isinstance(raw_weight, str):
                        raw_weight = raw_weight.replace(',', '').replace('kg', '').strip()
                        if raw_weight == '': raw_weight = 0
                    safe_weight = float(raw_weight)
                except:
                    safe_weight = 0.0

                new_weight = c3.number_input("중량 (kg)", value=safe_weight, key=f"w_{idx}")
                
                # 재계산 (라이브 환율 적용)
                recalc = calculate_tax_logic(new_mat, new_weight)
                
                # 4. 결과 표시
                if new_mat == 'Other':
                    c4.success("✅ 보고 면제")
                else:
                    c4.metric("📊 예상 배출량", f"{recalc['bad_tax'] / recalc['exchange_rate'] / 85:.2f} tCO2")
                    st.caption(f"적용 환율: {recalc['exchange_rate']:,.0f} 원")

                row['Material'] = new_mat
                row['HS Code'] = new_hs
                row['Weight (kg)'] = new_weight
                row['Default Tax (KRW)'] = recalc['bad_tax']
                updated_final_results.append(row)

        st.divider()
        
        excel_data = generate_official_excel(updated_final_results)
        if excel_data:
            c_down1, c_down2 = st.columns([3, 1])
            c_down1.download_button(
                label="📥 [EU 제출용] 공식 양식 엑셀 다운로드",
                data=excel_data,
                file_name=f"CBAM_Report_{datetime.now().strftime('%Y%m%d')}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                type="primary",
                use_container_width=True
            )
        
        st.markdown("---")
        st.warning("""
        **⚖️ [법적 고지 및 면책 조항]**
        1. 본 리포트는 AI 기반 시뮬레이션 결과이며, 공식 제출 전 관세사의 검토가 필요합니다.
        2. 적용된 환율 및 배출계수는 구글 금융 및 EU 기본값을 따릅니다.
        3. 서비스 제공자는 본 데이터 활용으로 인한 법적 책임을 지지 않습니다.
        """)
        
        if st.button("🔄 초기화"):
            st.session_state['batch_results'] = None
            st.rerun()
