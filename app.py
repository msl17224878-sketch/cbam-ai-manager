import streamlit as st
import json
import os
import base64
import pandas as pd
import io
from openai import OpenAI
from datetime import datetime
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4

# ==========================================
# ⚠️ [설정] API 키 (여기에 입력!)
# ==========================================
API_KEY = "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" 
client = OpenAI(api_key=API_KEY)

# 💰 고객 장부
CLIENT_DB = {
    "admin": "1234",
    "samsung": "galaxy",
    "posco": "steel"
}

# 📊 CBAM 데이터베이스 (기타=0원)
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
    # 재질이 명확하지 않으면 Other로 처리
    if material not in CBAM_DB: 
        if material == "Other":
             db = CBAM_DB["Other"]
        else:
             # 사용자가 강제로 철강 등을 선택했을 때
             db = CBAM_DB.get(material, CBAM_DB["Iron/Steel"])
    else:
        db = CBAM_DB[material]
    
    if weight <= 0: weight = 1
    
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
# 📊 엑셀 생성
# ------------------------------------------------
def generate_bulk_excel(data_list, username):
    df = pd.DataFrame(data_list)
    # 엑셀에 '비고'란 추가 (대상 여부 표시)
    df['Status'] = df['Material'].apply(lambda x: "Exempt (면제)" if x == 'Other' else "Target (대상)")
    
    columns_order = [
        "Date", "Company", "File Name", "Item Name", "Material", "Status",
        "Weight (kg)", "Default Tax (KRW)", "Optimized Tax (KRW)", "Savings (KRW)"
    ]
    existing_cols = [col for col in columns_order if col in df.columns]
    df = df[existing_cols]

    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        df.to_excel(writer, index=False, sheet_name='CBAM_Analysis')
        worksheet = writer.sheets['CBAM_Analysis']
        worksheet.set_column('A:J', 18)
    return output.getvalue()

# ------------------------------------------------
# 🧠 AI 분석 함수 (안심 필터 적용)
# ------------------------------------------------
def analyze_image(image_bytes, filename):
    base64_image = base64.b64encode(image_bytes).decode('utf-8')
    try:
        response = client.chat.completions.create(
            model="gpt-4o",
            temperature=0.0,
            seed=42,
            messages=[
                {
                    "role": "system", 
                    "content": """
                    You are a CBAM Classifier AI.
                    
                    Step 1: Identify Product Item.
                    Step 2: Classify Material into: ['Iron/Steel', 'Aluminum', 'Electronics', 'Cement', 'Other'].
                    
                    🚨 CRITICAL RULE:
                    - Only classify as Iron/Steel/Aluminum/Cement if you are SURE.
                    - For Fish, Food, Wood, Plastic, Textile -> YOU MUST CLASSIFY AS "Other".
                    
                    Step 3: Extract Weight (Convert to KG).
                    - Lbs -> * 0.4536
                    - Tons/MT -> * 1000
                    
                    Output JSON:
                    {"item": "...", "material": "...", "weight": ...}
                    """
                },
                {"role": "user", "content": [{"type": "text", "text": "Analyze."}, {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}}]}
            ],
            max_tokens=300, response_format={"type": "json_object"}
        )
        data = json.loads(response.choices[0].message.content)
        
        calc = calculate_tax_logic(data.get('material', 'Other'), data.get('weight', 0))
        data.update(calc)
        
        if not data.get('item'): data['item'] = "Unidentified"
        
        data["File Name"] = filename
        data["Date"] = datetime.now().strftime('%Y-%m-%d')
        data["Company"] = st.session_state.get('username', 'Guest').upper()
        
        # 엑셀용 데이터
        mapped_data = {
            "Date": data["Date"],
            "Company": data["Company"],
            "File Name": data["File Name"],
            "Item Name": data["item"],
            "Material": data["material_display"],
            "Weight (kg)": data["weight"],
            "Default Tax (KRW)": data["bad_tax"],
            "Optimized Tax (KRW)": data["good_tax"],
            "Savings (KRW)": data["savings"]
        }
        mapped_data.update(data)
        return mapped_data

    except Exception as e:
        return {"File Name": filename, "Item Name": "Error", "Savings (KRW)": 0}

# ==========================================
# 🖥️ 메인 앱
# ==========================================
st.set_page_config(page_title="AI CBAM Master", page_icon="🌍", layout="wide")

if 'logged_in' not in st.session_state:
    st.session_state['logged_in'] = False
if 'batch_results' not in st.session_state:
    st.session_state['batch_results'] = None

if not st.session_state['logged_in']:
    st.title("🔒 기업 회원 로그인")
    with st.form("login_form"):
        username = st.text_input("아이디")
        password = st.text_input("비밀번호", type="password")
        if st.form_submit_button("로그인"):
            if username in CLIENT_DB and CLIENT_DB[username] == password:
                st.session_state['logged_in'] = True
                st.session_state['username'] = username
                st.rerun()
            else:
                st.error("로그인 실패")
else:
    with st.sidebar:
        st.write(f"👤 **{st.session_state['username'].upper()}** 님")
        st.success("Global Enterprise Plan")
        if st.button("로그아웃"):
            st.session_state['logged_in'] = False
            st.session_state['batch_results'] = None
            st.rerun()

    st.title("🏭 CBAM 규제 판독 및 신고 시스템")
    st.info("💡 모든 무역 서류를 넣으세요. 규제 대상(철강 등)은 세금을 계산하고, 비규제 품목(식품 등)은 '면제'를 확인해 드립니다.")

    uploaded_files = st.file_uploader("파일 일괄 업로드", type=["jpg", "png", "jpeg"], accept_multiple_files=True)

    if uploaded_files:
        if st.session_state['batch_results'] and len(uploaded_files) != len(st.session_state['batch_results']):
             st.session_state['batch_results'] = None

        if len(uploaded_files) > 0:
            if st.button(f"🚀 {len(uploaded_files)}건 판독 시작") or st.session_state['batch_results']:
                if st.session_state['batch_results'] is None:
                    progress_bar = st.progress(0)
                    all_results = []
                    for i, file in enumerate(uploaded_files):
                        file.seek(0)
                        res = analyze_image(file.read(), file.name)
                        all_results.append(res)
                        progress_bar.progress((i + 1) / len(uploaded_files))
                    st.session_state['batch_results'] = all_results
                
                results = st.session_state['batch_results']
                st.subheader("📝 판독 결과 리포트")

                updated_results = []
                for idx, row in enumerate(results):
                    # 비규제 품목(Other)일 때 디자인을 다르게 보여줌 (안심 배지)
                    is_exempt = (row.get('Material', 'Other') == 'Other')
                    
                    with st.expander(f"[{idx+1}] {row['File Name']} - {row['Item Name']}"):
                        c1, c2, c3 = st.columns([2, 1, 1])
                        
                        # 1. 재질 선택
                        new_mat = c1.selectbox(
                            "재질 (Material)", 
                            ["Iron/Steel", "Aluminum", "Electronics", "Cement", "Other"],
                            index=["Iron/Steel", "Aluminum", "Electronics", "Cement", "Other"].index(row.get('Material', 'Other')),
                            key=f"mat_{idx}"
                        )
                        # 2. 무게
                        new_weight = c2.number_input("중량 (kg)", value=int(row.get('Weight (kg)', 0)), key=f"w_{idx}")
                        
                        # 재계산
                        recalc = calculate_tax_logic(new_mat, new_weight)
                        
                        # 3. 결과 표시 (핵심!)
                        if new_mat == 'Other':
                            # 생선 같은 경우 -> 녹색 안심 메시지
                            c3.success("✅ CBAM 대상 아님 (면제)")
                            st.caption("이 품목은 탄소세 신고 대상이 아닙니다.")
                        else:
                            # 철강 같은 경우 -> 세금 금액 표시
                            c3.metric("💰 예상 탄소세", f"{format(recalc['bad_tax'], ',')} 원")
                            st.write(f"절감 가능액: {format(recalc['savings'], ',')} 원")

                        row.update({
                            "Material": new_mat,
                            "Weight (kg)": new_weight,
                            "Default Tax (KRW)": recalc['bad_tax'],
                            "Savings (KRW)": recalc['savings']
                        })
                        updated_results.append(row)
                
                # 4. 엑셀 다운로드
                st.divider()
                st.success("판독이 완료되었습니다. 엑셀에서 'Target(대상)'과 'Exempt(면제)'를 구분해서 확인하세요.")
                bulk_excel = generate_bulk_excel(updated_results, st.session_state['username'])
                st.download_button("📥 전체 리포트 엑셀 다운로드", bulk_excel, "CBAM_Master_Report.xlsx")
                
                if st.button("🔄 초기화"):
                    st.session_state['batch_results'] = None
                    st.rerun()