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
# 🔑 API 키 설정 (보안 강화 버전)
# ==========================================
# secrets에서 키를 가져오거나, 없으면 에러 메시지 띄움
try:
    api_key = st.secrets["OPENAI_API_KEY"]
    client = OpenAI(api_key=api_key)
except FileNotFoundError:
    st.error("🚨 API 키가 설정되지 않았습니다! [Settings] > [Secrets]에 OPENAI_API_KEY를 추가하세요.")
    st.stop()
except Exception as e:
    st.error(f"🚨 설정 오류: {e}")
    st.stop()

# 💰 고객 장부 (아이디/비번)
CLIENT_DB = {
    "admin": "1234",
    "samsung": "galaxy",
    "posco": "steel"
}

# 📊 CBAM 데이터베이스
CBAM_DB = {
    "Iron/Steel": {"default": 2.5, "optimized": 0.5, "price": 85.0},
    "Aluminum": {"default": 8.0, "optimized": 1.5, "price": 85.0},
    "Electronics": {"default": 1.5, "optimized": 0.3, "price": 85.0},
    "Cement": {"default": 1.0, "optimized": 0.5, "price": 85.0},
    "Other": {"default": 0.0, "optimized": 0.0, "price": 0.0}
}

# ------------------------------------------------
# 🧮 세금 계산 로직
# ------------------------------------------------
def calculate_tax_logic(material, weight):
    if material not in CBAM_DB: 
        if material == "Other":
             db = CBAM_DB["Other"]
        else:
             db = CBAM_DB.get(material, CBAM_DB["Iron/Steel"])
    else:
        db = CBAM_DB[material]
    
    if weight <= 0: weight = 1
    
    # 환율 1450원 기준
    bad_tax = int((weight/1000) * db['default'] * db['price'] * 1450)
    good_tax = int((weight/1000) * db['optimized'] * db['price'] * 1450)
    
    return {
        "bad_tax": bad_tax,
        "good_tax": good_tax,
        "savings": bad_tax - good_tax,
        "material_display": material,
        "weight": weight
    }

# ------------------------------------------------
# 📊 엑셀 생성 함수
# ------------------------------------------------
def generate_bulk_excel(data_list):
    if not data_list:
        return None
        
    df = pd.DataFrame(data_list)
    
    # 필수 컬럼이 있는지 확인하고 없으면 기본값 채움
    required_cols = ["Date", "Company", "File Name", "Item Name", "Material", "Weight (kg)", "Default Tax (KRW)", "Optimized Tax (KRW)", "Savings (KRW)"]
    for col in required_cols:
        if col not in df.columns:
            df[col] = ""

    # 'Status' 컬럼 추가
    df['Status'] = df['Material'].apply(lambda x: "Exempt (면제)" if x == 'Other' else "Target (대상)")
    
    columns_order = ["Date", "Company", "File Name", "Item Name", "Material", "Status", 
                     "Weight (kg)", "Default Tax (KRW)", "Optimized Tax (KRW)", "Savings (KRW)"]
    
    # 존재하는 컬럼만 선택
    final_cols = [col for col in columns_order if col in df.columns]
    df = df[final_cols]

    output = io.BytesIO()
    # xlsxwriter 엔진 사용
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        df.to_excel(writer, index=False, sheet_name='CBAM_Analysis')
        worksheet = writer.sheets['CBAM_Analysis']
        worksheet.set_column('A:J', 18) # 컬럼 넓이 조정
    return output.getvalue()

# ------------------------------------------------
# 🧠 AI 분석 함수
# ------------------------------------------------
def analyze_image(image_bytes, filename, username):
    base64_image = base64.b64encode(image_bytes).decode('utf-8')
    try:
        response = client.chat.completions.create(
            model="gpt-4o",
            temperature=0.0,
            messages=[
                {
                    "role": "system", 
                    "content": """
                    You are a CBAM Classifier AI.
                    Step 1: Identify Product Item.
                    Step 2: Classify Material into: ['Iron/Steel', 'Aluminum', 'Electronics', 'Cement', 'Other'].
                    🚨 For Fish, Food, Wood, Plastic, Textile -> YOU MUST CLASSIFY AS "Other".
                    Step 3: Extract Weight (Convert to KG).
                    Output JSON: {"item": "...", "material": "...", "weight": ...}
                    """
                },
                {"role": "user", "content": [{"type": "text", "text": "Analyze this image."}, {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}}]}
            ],
            response_format={"type": "json_object"}
        )
        data = json.loads(response.choices[0].message.content)
        
        # 기본 계산
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

# 세션 상태 초기화
if 'logged_in' not in st.session_state:
    st.session_state['logged_in'] = False
if 'batch_results' not in st.session_state:
    st.session_state['batch_results'] = None

# 1️⃣ 로그인 화면
if not st.session_state['logged_in']:
    st.title("🔒 기업 회원 로그인")
    with st.form("login_form"):
        username = st.text_input("아이디")
        password = st.text_input("비밀번호", type="password")
        submit = st.form_submit_button("로그인") # 폼 안에는 반드시 버튼이 있어야 함
        
        if submit:
            if username in CLIENT_DB and CLIENT_DB[username] == password:
                st.session_state['logged_in'] = True
                st.session_state['username'] = username
                st.rerun()
            else:
                st.error("아이디 또는 비밀번호가 틀렸습니다.")

# 2️⃣ 메인 대시보드
else:
    # 사이드바
    with st.sidebar:
        st.write(f"👤 **{st.session_state['username'].upper()}** 님")
        st.success("Global Enterprise Plan")
        if st.button("로그아웃"):
            st.session_state['logged_in'] = False
            st.session_state['batch_results'] = None
            st.rerun()

    st.title("🏭 CBAM 규제 판독 및 신고 시스템")
    st.info("💡 파일을 업로드하면 AI가 자동으로 판독합니다.")

    # 파일 업로드 (폼 밖에서 실행)
    uploaded_files = st.file_uploader("파일 일괄 업로드", type=["jpg", "png", "jpeg"], accept_multiple_files=True)

    # 업로드 된 파일이 바뀌면 결과 초기화
    if uploaded_files:
        if st.session_state['batch_results'] and len(uploaded_files) != len(st.session_state['batch_results']):
             st.session_state['batch_results'] = None

    # 판독 버튼
    if uploaded_files and len(uploaded_files) > 0:
        if st.button(f"🚀 {len(uploaded_files)}건 판독 시작"):
            progress_bar = st.progress(0)
            all_results = []
            
            for i, file in enumerate(uploaded_files):
                file.seek(0)
                # 스피너 추가 (로딩 중 표시)
                with st.spinner(f"{file.name} 분석 중..."):
                    res = analyze_image(file.read(), file.name, st.session_state['username'])
                    # 엑셀용 데이터 매핑
                    mapped = {
                        "Date": res.get("Date"),
                        "Company": res.get("Company"),
                        "File Name": res.get("File Name"),
                        "Item Name": res.get("item"),
                        "Material": res.get("material_display"),
                        "Weight (kg)": res.get("weight"),
                        "Default Tax (KRW)": res.get("bad_tax"),
                        "Optimized Tax (KRW)": res.get("good_tax"),
                        "Savings (KRW)": res.get("savings")
                    }
                    all_results.append(mapped)
                progress_bar.progress((i + 1) / len(uploaded_files))
            
            st.session_state['batch_results'] = all_results
            st.rerun() # 결과 갱신을 위해 리런

    # 결과 리포트 및 수정 화면
    if st.session_state['batch_results']:
        st.divider()
        st.subheader("📝 판독 결과 (수정 가능)")
        
        results = st.session_state['batch_results']
        updated_final_results = []

        # 각 결과를 카드 형태로 출력
        for idx, row in enumerate(results):
            with st.expander(f"[{idx+1}] {row['File Name']} - {row['Item Name']}", expanded=True):
                c1, c2, c3 = st.columns([2, 1, 1])
                
                # 재질 수정
                current_mat = row.get('Material', 'Other')
                mat_options = ["Iron/Steel", "Aluminum", "Electronics", "Cement", "Other"]
                # 인덱스 에러 방지
                mat_index = mat_options.index(current_mat) if current_mat in mat_options else 4
                
                new_mat = c1.selectbox("재질", mat_options, index=mat_index, key=f"mat_{idx}")
                
                # 무게 수정
                new_weight = c2.number_input("중량 (kg)", value=float(row.get('Weight (kg)', 0)), key=f"w_{idx}")
                
                # 실시간 재계산
                recalc = calculate_tax_logic(new_mat, new_weight)
                
                if new_mat == 'Other':
                    c3.success("✅ 면제 대상")
                else:
                    c3.metric("💰 예상 세금", f"{format(recalc['bad_tax'], ',')}원")
                
                # 데이터 업데이트 (엑셀용)
                row['Material'] = new_mat
                row['Weight (kg)'] = new_weight
                row['Default Tax (KRW)'] = recalc['bad_tax']
                row['Optimized Tax (KRW)'] = recalc['good_tax']
                row['Savings (KRW)'] = recalc['savings']
                updated_final_results.append(row)

        # 엑셀 다운로드 버튼
        st.divider()
        excel_data = generate_bulk_excel(updated_final_results)
        if excel_data:
            st.download_button(
                label="📥 엑셀 리포트 다운로드",
                data=excel_data,
                file_name=f"CBAM_Report_{datetime.now().strftime('%Y%m%d')}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )
        
        if st.button("🔄 초기화 (처음으로)"):
            st.session_state['batch_results'] = None
            st.rerun()
