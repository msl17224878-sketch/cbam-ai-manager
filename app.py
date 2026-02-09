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

# 1. 고객 장부 (아이디/비번/크레딧) - 기존 링크
USER_DB_URL = "https://docs.google.com/spreadsheets/d/e/2PACX-1vRqCIpXf7jM4wyn8EhpoZipkUBQ2K43rEiaNi-KyoaI1j93YPNMLpavW07-LddivnoUL-FKFDMCFPkI/pub?gid=0&single=true&output=csv"

# 2. 규정 장부 (배출계수/HS코드) - 방금 주신 새 링크!
CBAM_DATA_URL = "https://docs.google.com/spreadsheets/d/e/2PACX-1vRTkYfVcC9EAv_xW0FChVWK3oMsPaxXiRL-hOQQeGT_aLsUG044s1L893er36HVJUpgTCrsM0xElFpW/pub?gid=747982569&single=true&output=csv"

# ------------------------------------
# 1️⃣ 고객 데이터 로드 함수
# ------------------------------------
@st.cache_data(ttl=60) # 60초마다 갱신 (돈 관련이라 중요!)
def load_user_data():
    try:
        df = pd.read_csv(USER_DB_URL)
        df.columns = df.columns.str.strip().str.lower()
        df['username'] = df['username'].astype(str).str.strip()
        df['password'] = df['password'].astype(str).str.strip()
        df['active'] = df['active'].astype(str).str.strip().str.lower()
        
        # 크레딧 처리
        if 'credits' not in df.columns: df['credits'] = 0
        df['credits'] = pd.to_numeric(df['credits'], errors='coerce').fillna(0).astype(int)
        
        return df
    except Exception:
        return pd.DataFrame()

# ------------------------------------
# 2️⃣ CBAM 규정 데이터 로드 함수 (자동 업데이트 핵심)
# ------------------------------------
@st.cache_data(ttl=300) # 규정은 자주 안 바뀌니 5분마다 갱신
def load_cbam_db():
    try:
        df = pd.read_csv(CBAM_DATA_URL)
        df.columns = df.columns.str.strip().str.lower()
        
        db = {}
        for _, row in df.iterrows():
            # 엑셀의 category 컬럼을 키값으로 사용
            cat = str(row['category']).strip()
            db[cat] = {
                "default": float(row.get('default', 0)),
                "optimized": float(row.get('optimized', 0)),
                # HS코드는 소수점 없이 문자열로 변환
                "hs_code": str(row.get('hs_code', '000000')).split('.')[0], 
                "price": 85.0 # 탄소 가격은 일단 고정 (나중에 시트에 추가 가능)
            }
        return db
    except Exception as e:
        # 엑셀 못 읽으면 비상용 기본값
        st.toast(f"⚠️ 규정 데이터 로드 실패: {e}")
        return {
            "Iron/Steel": {"default": 2.5, "optimized": 0.5, "hs_code": "731800", "price": 85.0},
            "Aluminum": {"default": 8.0, "optimized": 1.5, "hs_code": "760400", "price": 85.0},
            "Other": {"default": 0.0, "optimized": 0.0, "hs_code": "000000", "price": 0.0}
        }

# 데이터 불러오기
user_df = load_user_data()
CBAM_DB = load_cbam_db()

# ------------------------------------------------
# 🧮 세금 계산 로직 (실시간 DB 적용)
# ------------------------------------------------
def calculate_tax_logic(material, weight):
    # 구글 시트에 있는 재질이면 그 값을 씀
    if material in CBAM_DB:
        db = CBAM_DB[material]
    else:
        # 없으면 Iron/Steel을 기본으로 하거나 Other 처리
        if "Iron/Steel" in CBAM_DB:
            db = CBAM_DB["Iron/Steel"]
        else:
            db = {"default": 0, "optimized": 0, "price": 0} # 비상용
    
    # Other(면제)인 경우
    if material == "Other":
        if "Other" in CBAM_DB:
            db = CBAM_DB["Other"]
        else:
            db = {"default": 0, "optimized": 0, "price": 0}

    if weight <= 0: weight = 1
    
    # 환율 1450원 기준
    bad_tax = int((weight/1000) * db['default'] * db['price'] * 1450)
    good_tax = int((weight/1000) * db['optimized'] * db['price'] * 1450)
    
    return {
        "bad_tax": bad_tax,
        "good_tax": good_tax,
        "savings": bad_tax - good_tax,
        "material_display": material,
        "weight": weight,
        "hs_code": db.get('hs_code', '000000') # DB에서 HS코드 가져옴
    }

# ------------------------------------------------
# 🇪🇺 EU 공식 양식 엑셀 생성 (관세사 제출용)
# ------------------------------------------------
def generate_official_excel(data_list):
    if not data_list:
        return None
        
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        workbook = writer.book
        
        # 스타일 정의
        header_format = workbook.add_format({
            'bold': True, 'text_wrap': True, 'valign': 'vcenter', 'fg_color': '#004494', 'font_color': 'white', 'border': 1})
        cell_format = workbook.add_format({'border': 1, 'valign': 'vcenter'})
        num_format = workbook.add_format({'border': 1, 'valign': 'vcenter', 'num_format': '#,##0.00'})
        
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
        ws_summary.write(1, 4, total_tax, num_format)
        ws_summary.set_column('A:E', 25)

        # 2. Data 시트 (EU 제출용)
        ws_data = workbook.add_worksheet("CBAM_Data_For_Submission")
        data_headers = [
            "Line No", "Origin Country", "CN Code (HS Code)", "Goods Name", 
            "Net Mass (Tonnes)", "Direct Emissions (tCO2e/t)", "Total Emissions (tCO2e)", "Est. Tax (KRW)"
        ]
        
        for col, h in enumerate(data_headers):
            ws_data.write(0, col, h, header_format)
            
        for row_idx, data in enumerate(data_list):
            row = row_idx + 1
            weight_ton = data.get('Weight (kg)', 0) / 1000
            
            # DB에서 값 가져오기
            mat = data.get('Material', 'Iron/Steel')
            factor = 0
            if mat in CBAM_DB:
                factor = CBAM_DB[mat]['default']
            
            total_emissions = weight_ton * factor
            
            ws_data.write(row, 0, row, cell_format)
            ws_data.write(row, 1, "KR (Korea)", cell_format)
            ws_data.write(row, 2, data.get('HS Code', '000000'), cell_format)
            ws_data.write(row, 3, data.get('Item Name', ''), cell_format)
            ws_data.write(row, 4, weight_ton, num_format)
            ws_data.write(row, 5, factor, num_format)
            ws_data.write(row, 6, total_emissions, num_format)
            ws_data.write(row, 7, data.get('Default Tax (KRW)', 0), num_format)
            
        ws_data.set_column('A:H', 20)
        
    return output.getvalue()

# ------------------------------------------------
# 🧠 AI 분석 함수
# ------------------------------------------------
def analyze_image(image_bytes, filename, username):
    base64_image = base64.b64encode(image_bytes).decode('utf-8')
    try:
        # 동적으로 변하는 카테고리 리스트 생성
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
        
        # 계산 로직 호출
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
                    st.error("로그인 실패: 아이디/비번 확인 또는 승인 대기중")
            else:
                st.error("시스템 점검 중 (DB 연결 실패)")

# 2️⃣ 메인 대시보드
else:
    # 크레딧 정보 실시간 확인
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
    st.info("💡 EU 공식 배출계수 DB가 실시간으로 적용됩니다.")

    uploaded_files = st.file_uploader("수출 서류 업로드", type=["jpg", "png", "jpeg"], accept_multiple_files=True)

    if uploaded_files:
        if st.session_state['batch_results'] and len(uploaded_files) != len(st.session_state['batch_results']):
             st.session_state['batch_results'] = None

    if uploaded_files and len(uploaded_files) > 0:
        file_count = len(uploaded_files)
        
        if user_credits < file_count:
            st.warning(f"⚠️ 크레딧이 부족합니다. (보유: {user_credits} / 필요: {file_count})")
        else:
            if st.button(f"🚀 {file_count}건 판독 시작 (크레딧 차감)"):
                progress_bar = st.progress(0)
                all_results = []
                
                for i, file in enumerate(uploaded_files):
                    file.seek(0)
                    with st.spinner(f"{file.name} 분석 중..."):
                        res = analyze_image(file.read(), file.name, st.session_state['username'])
                        # 엑셀용 데이터 매핑
                        mapped = res.copy()
                        mapped["Default Tax (KRW)"] = res.get("bad_tax")
                        mapped["Optimized Tax (KRW)"] = res.get("good_tax")
                        mapped["Savings (KRW)"] = res.get("savings")
                        mapped["Item Name"] = res.get("item")
                        mapped["Material"] = res.get("material_display")
                        mapped["Weight (kg)"] = res.get("weight")
                        mapped["HS Code"] = res.get("hs_code") # HS코드 추가
                        
                        all_results.append(mapped)
                    progress_bar.progress((i + 1) / len(uploaded_files))
                
                st.session_state['batch_results'] = all_results
                st.toast("판독 완료! 결과를 확인하고 수정하세요.")
                st.rerun()

    # 결과 리포트 및 수정
    if st.session_state['batch_results']:
        st.divider()
        st.subheader("📝 데이터 검증 (EU 제출용)")
        st.caption("AI가 인식한 HS Code와 무게를 확인 후 수정하세요. 이 데이터가 엑셀에 반영됩니다.")
        
        results = st.session_state['batch_results']
        updated_final_results = []

        # 구글 시트에서 가져온 카테고리 목록
        mat_options = list(CBAM_DB.keys())
        if "Other" not in mat_options: mat_options.append("Other")

        for idx, row in enumerate(results):
            with st.expander(f"[{idx+1}] {row['File Name']} - {row['Item Name']}", expanded=True):
                c1, c2, c3, c4 = st.columns([1.5, 1.5, 1, 1.5])
                
                # 1. 재질 선택
                current_mat = row.get('Material', 'Other')
                # DB에 없는 재질이 들어오면 기본값 처리
                mat_index = mat_options.index(current_mat) if current_mat in mat_options else mat_options.index("Other")
                new_mat = c1.selectbox("재질", mat_options, index=mat_index, key=f"mat_{idx}")
                
                # 2. HS Code 수정 (DB에서 가져온 값 or 사용자 입력)
                # 재질을 바꾸면 HS코드도 DB에 있는 걸로 추천해줌
                suggested_hs = CBAM_DB.get(new_mat, {}).get('hs_code', '000000')
                current_hs = row.get('HS Code', suggested_hs)
                new_hs = c2.text_input("CN Code (HS 6단위)", value=str(current_hs), key=f"hs_{idx}")
                
                # 3. 무게 수정
                new_weight = c3.number_input("중량 (kg)", value=float(row.get('Weight (kg)', 0)), key=f"w_{idx}")
                
                # 재계산 (라이브)
                # 1) 재질이 바뀌었으니 다시 DB 참조
                recalc = calculate_tax_logic(new_mat, new_weight)
                
                # 4. 결과 표시
                if new_mat == 'Other':
                    c4.success("✅ 보고 면제")
                else:
                    c4.metric("📊 예상 배출량", f"{recalc['bad_tax']/1450/85:.2f} tCO2")

                # 데이터 업데이트
                row['Material'] = new_mat
                row['HS Code'] = new_hs
                row['Weight (kg)'] = new_weight
                row['Default Tax (KRW)'] = recalc['bad_tax']
                updated_final_results.append(row)

        st.divider()
        
        # 엑셀 다운로드
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
        
        # ⚠️ 법적 면책 조항 (필수)
        st.markdown("---")
        st.warning("""
        **⚖️ [법적 고지 및 면책 조항]**
        1. 본 리포트는 AI 기반의 예상 시뮬레이션 결과이며, EU CBAM 공식 제출을 위한 기초 자료로만 활용해야 합니다.
        2. 최종 신고 전, 반드시 관세사 또는 전문 검증기관의 검토를 거쳐야 합니다.
        3. 서비스 제공자는 본 데이터 활용으로 인한 세무/법적 책임에 대해 보증하지 않습니다.
        """)
        
        if st.button("🔄 초기화"):
            st.session_state['batch_results'] = None
            st.rerun()
