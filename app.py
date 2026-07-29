import streamlit as st
import re
from supabase import create_client, Client

# Supabase Initialization (Requires st.secrets for production)
@st.cache_resource
def init_supabase() -> Client:
    url = st.secrets["SUPABASE_URL"]
    key = st.secrets["SUPABASE_KEY"]
    return create_client(url, key)

supabase = init_supabase()

def render_module_1():
    st.header("Module 1: Basic Profile Details")
    
    with st.form("client_profile_form", clear_on_submit=False):
        col1, col2 = st.columns(2)
        
        with col1:
            full_legal_name = st.text_input("Full Legal Name (as per PAN)*")
            pan = st.text_input("PAN (10 alphanumeric)*", max_chars=10).upper()
            aadhaar_number = st.text_input("Aadhaar Number (12 digits)", max_chars=12)
            mobile_number = st.text_input("Mobile Number*", max_chars=15)
            
        with col2:
            email_address = st.text_input("Email Address*")
            it_portal_password = st.text_input("Income Tax Portal Password", type="password")
            entity_type = st.selectbox(
                "Entity Type*",
                [
                    "Individual",
                    "Hindu Undivided Family (HUF)",
                    "Company",
                    "Firm",
                    "Association of Persons (AOP) / Body of Individuals (BOI)",
                    "Local Authority",
                    "Artificial Juridical Person"
                ]
            )
            assessment_year = st.selectbox(
                "Assessment Year (AY)*",
                ["2025-26", "2024-25", "2026-27"]
            )
            
        submitted = st.form_submit_button("Save Profile Details")
        
        if submitted:
            # Validations
            pan_regex = r'^[A-Z]{5}[0-9]{4}[A-Z]{1}$'
            email_regex = r'^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$'
            
            if not full_legal_name or not pan or not mobile_number or not email_address:
                st.error("Please fill in all mandatory fields (*).")
            elif not re.match(pan_regex, pan):
                st.error("Invalid PAN format. Example: ABCDE1234F")
            elif aadhaar_number and (not aadhaar_number.isdigit() or len(aadhaar_number) != 12):
                st.error("Aadhaar Number must be exactly 12 digits.")
            elif not re.match(email_regex, email_address):
                st.error("Invalid Email Address format.")
            else:
                payload = {
                    "full_legal_name": full_legal_name,
                    "pan": pan,
                    "aadhaar_number": aadhaar_number if aadhaar_number else None,
                    "mobile_number": mobile_number,
                    "email_address": email_address,
                    "it_portal_password": it_portal_password,
                    "entity_type": entity_type,
                    "assessment_year": assessment_year
                }
                
                try:
                    response = supabase.table("client_profiles").upsert(payload, on_conflict="pan").execute()
                    st.success("Client profile successfully saved!")
                except Exception as e:
                    st.error(f"Error saving profile: {str(e)}")

if __name__ == "__main__":
    render_module_1()
