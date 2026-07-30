import re
import pandas as pd
import streamlit as st
from supabase import create_client, Client

# --- Page Configuration ---
st.set_page_config(page_title="Module 1: Client Onboarding", layout="wide")

# --- Supabase Initialization ---
@st.cache_resource
def init_supabase() -> Client:
    url = st.secrets["SUPABASE_URL"]
    key = st.secrets["SUPABASE_KEY"]
    return create_client(url, key)

supabase = init_supabase()

# --- Helper Validation Functions ---
def validate_pan(pan: str) -> bool:
    return bool(re.match(r"^[A-Z]{5}[0-9]{4}[A-Z]{1}$", pan.upper()))

def validate_aadhaar(aadhaar: str) -> bool:
    if not aadhaar:
        return True  # Optional / empty check
    return bool(re.match(r"^[0-9]{12}$", aadhaar))

def validate_email(email: str) -> bool:
    return bool(re.match(r"^[\w\.-]+@[\w\.-]+\.\w+$", email))

# --- UI Header ---
st.title("Module 1: Basic Profile Details")
st.subheader("Client Onboarding & Identity Capture")

# --- Onboarding Form ---
with st.form("client_profile_form", clear_on_submit=False):
    col1, col2 = st.columns(2)

    with col1:
        full_name = st.text_input("Full Legal Name (as per PAN)*")
        pan = st.text_input("PAN (10-character alphanumeric)*", max_chars=10).upper()
        aadhaar = st.text_input("Aadhaar Number (12 digits)", max_chars=12)
        entity_type = st.selectbox(
            "Entity Type*",
            [
                "Individual",
                "HUF",
                "Company",
                "Firm",
                "AOP/BOI",
                "Local Authority",
                "Artificial Juridical Person",
            ],
        )

    with col2:
        mobile = st.text_input("Mobile Number*", max_chars=15)
        email = st.text_input("Email Address*")
        it_password = st.text_input("IT Portal Password", type="password")
        assessment_year = st.selectbox(
            "Assessment Year (AY)*",
            ["2026-27", "2025-26", "2024-25"],
        )

    submitted = st.form_submit_button("Save Client Profile")

# --- Submission Logic ---
if submitted:
    errors = []
    if not full_name.strip():
        errors.append("Full Legal Name is required.")
    if not validate_pan(pan):
        errors.append("Invalid PAN format (e.g., ABCDE1234F).")
    if aadhaar and not validate_aadhaar(aadhaar):
        errors.append("Invalid Aadhaar format (must be 12 digits).")
    if not validate_email(email):
        errors.append("Invalid Email Address.")
    if not mobile.strip():
        errors.append("Mobile Number is required.")

    if errors:
        for err in errors:
            st.error(err)
    else:
        payload = {
            "full_name_pan": full_name.strip(),
            "pan": pan,
            "aadhaar_number": aadhaar if aadhaar else None,
            "mobile_number": mobile.strip(),
            "email": email.strip(),
            "it_portal_password": it_password,
            "entity_type": entity_type,
            "assessment_year": assessment_year,
        }
        try:
            response = supabase.table("client_profiles").upsert(payload, on_conflict="pan").execute()
            st.success("Client profile successfully saved/updated!")
        except Exception as e:
            st.error(f"Database error: {str(e)}")

# --- Client Directory & Export ---
st.divider()
st.subheader("Saved Client Profiles")

try:
    data = supabase.table("client_profiles").select("*").execute()
    records = data.data
    if records:
        df = pd.DataFrame(records)
        display_cols = [
            "full_name_pan",
            "pan",
            "aadhaar_number",
            "entity_type",
            "mobile_number",
            "email",
            "assessment_year",
            "created_at",
        ]
        st.dataframe(df[display_cols], use_container_width=True)

        # Export to CSV
        excel_data = df.to_csv(index=False)
        st.download_button(
            label="Download Profiles Data (CSV)",
            data=excel_data,
            file_name="client_profiles.csv",
            mime="text/csv",
        )
    else:
        st.info("No client profiles found.")
except Exception as e:
    st.error(f"Error fetching profiles: {str(e)}")
