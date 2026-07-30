import re
import pandas as pd
import streamlit as st
from supabase import create_client, Client

# --- Page Configuration ---
st.set_page_config(page_title="Tax & Wealth OS", layout="wide")

# --- Custom Styling (Executive Dark & Royal Blue Theme) ---
st.markdown(
    """
    <style>
    /* Main App Background */
    .stApp {
        background-color: #0b132b;
        color: #ffffff;
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    }
    
    /* Sidebar Styling */
    section[data-testid="stSidebar"] {
        background-color: #1c2541;
        border-right: 1px solid #3a506b;
    }
    
    /* Headers & Navigation Cards */
    .sidebar-header {
        background: linear-gradient(135deg, #1d4ed8 0%, #1e40af 100%);
        color: #ffffff;
        padding: 14px 18px;
        border-radius: 10px;
        font-weight: 700;
        font-size: 18px;
        margin-bottom: 14px;
        box-shadow: 0 4px 12px rgba(0, 0, 0, 0.25);
    }
    
    .sidebar-subheader {
        background-color: #1c2541;
        color: #93c5fd;
        padding: 8px 12px;
        border-radius: 6px;
        font-weight: 600;
        font-size: 12px;
        text-transform: uppercase;
        letter-spacing: 0.8px;
        margin-bottom: 12px;
        border: 1px solid #3a506b;
    }
    
    /* Module Header Block */
    .module-header-container {
        background: linear-gradient(135deg, #1e3a8a 0%, #1d4ed8 100%);
        border: 1px solid #3b82f6;
        border-radius: 12px;
        padding: 24px;
        margin-bottom: 24px;
        box-shadow: 0 6px 16px rgba(0, 0, 0, 0.3);
    }
    
    .module-title {
        color: #ffffff;
        font-size: 26px;
        font-weight: 700;
        margin: 0;
    }
    
    .module-subtitle {
        color: #bfdbfe;
        font-size: 15px;
        font-weight: 400;
        margin-top: 6px;
    }
    
    /* Input Form Containers */
    div[data-testid="stForm"] {
        background-color: #1c2541;
        border: 1px solid #3a506b;
        border-radius: 12px;
        padding: 24px;
        box-shadow: 0 4px 14px rgba(0, 0, 0, 0.2);
    }
    </style>
    """,
    unsafe_allow_html=True,
)

# --- Supabase Initialization ---
@st.cache_resource
def init_supabase() -> Client:
    url = st.secrets["SUPABASE_URL"]
    key = st.secrets["SUPABASE_KEY"]
    return create_client(url, key)

supabase = init_supabase()

# --- Navigation Sidebar ---
st.sidebar.markdown('<div class="sidebar-header">Tax & Wealth OS</div>', unsafe_allow_html=True)
st.sidebar.markdown('<div class="sidebar-subheader">Modules</div>', unsafe_allow_html=True)

selected_module = st.sidebar.radio(
    "Select Module",
    ["Module 1: Basic Profile Details"],
    label_visibility="collapsed"
)

# --- Helper Validation Functions ---
def validate_pan(pan: str) -> bool:
    return bool(re.match(r"^[A-Z]{5}[0-9]{4}[A-Z]{1}$", pan.upper()))

def validate_aadhaar(aadhaar: str) -> bool:
    if not aadhaar:
        return True
    return bool(re.match(r"^[0-9]{12}$", aadhaar))

def validate_email(email: str) -> bool:
    return bool(re.match(r"^[\w\.-]+@[\w\.-]+\.\w+$", email))

# --- Module 1 Render ---
if selected_module == "Module 1: Basic Profile Details":
    st.markdown(
        """
        <div class="module-header-container">
            <div class="module-title">Module 1: Basic Profile Details</div>
            <div class="module-subtitle">Client Onboarding & Identity Capture</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # --- Form Container ---
    with st.container():
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

    # --- Form Processing ---
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
                supabase.table("client_profiles").upsert(payload, on_conflict="pan").execute()
                st.success("Client profile successfully saved/updated!")
            except Exception as e:
                st.error(f"Database error: {str(e)}")

    # --- Client Directory Preview & Export ---
    st.divider()
    st.subheader("Client Directory")

    try:
        data = supabase.table("client_profiles").select("*").execute()
        records = data.data
        if records:
            df = pd.DataFrame(records)
            
            # Display View (Simplified for cleaner UI)
            display_df = df[["full_name_pan", "pan"]].rename(
                columns={"full_name_pan": "Client Name", "pan": "PAN"}
            )
            st.dataframe(display_df, use_container_width=True, hide_index=True)

            # Full Export Option (Includes all profile attributes)
            full_csv = df.to_csv(index=False)
            st.download_button(
                label="Download Full Client Profiles (CSV)",
                data=full_csv,
                file_name="client_profiles_full.csv",
                mime="text/csv",
            )
        else:
            st.info("No client profiles found.")
    except Exception as e:
        st.error(f"Error fetching profiles: {str(e)}")
