import io
import re
import pandas as pd
import streamlit as st
from supabase import Client, create_client

# Page Configuration
st.set_page_config(
    page_title="Income Tax & Wealth Management Suite",
    page_icon="💼",
    layout="wide",
)


# Supabase Initialization
@st.cache_resource
def init_supabase() -> Client:
  url = st.secrets["SUPABASE_URL"]
  key = st.secrets["SUPABASE_KEY"]
  return create_client(url, key)


try:
  supabase = init_supabase()
except Exception:
  st.error("Please configure Supabase credentials in .streamlit/secrets.toml")


# --- MODULE 2 EVALUATION LOGIC ---
def determine_itr_form(responses: dict) -> str:
  res_status = responses.get("residential_status")
  has_dir = responses.get("has_directorship_indian_co") or responses.get(
      "has_directorship_foreign_co"
  )
  has_unlisted = responses.get("holds_unlisted_shares")
  has_fa = responses.get("has_foreign_assets_schedule_fa") or responses.get(
      "is_signing_authority_foreign_account"
  )
  has_business = responses.get("has_business_profession_income")
  has_cg = responses.get("has_capital_gains")
  has_speculative = responses.get("has_speculative_income")

  if has_business or has_speculative:
    return "ITR-3"
  if (
      has_cg
      or has_fa
      or has_dir
      or has_unlisted
      or res_status
      in [
          "Resident but Not Ordinarily Resident (RNOR)",
          "Non-Resident (NR)",
      ]
  ):
    return "ITR-2"
  return "ITR-1"


# --- MODULE 1 RENDER ---
def render_module_1():
  st.header("Module 1: Basic Profile Details")

  with st.form("client_profile_form", clear_on_submit=False):
    col1, col2 = st.columns(2)

    with col1:
      full_legal_name = st.text_input("Full Legal Name (as per PAN)*")
      pan = st.text_input("PAN (10-digit Alphanumeric)*", max_chars=10).upper()
      aadhaar_number = st.text_input("Aadhaar Number (12 digits)", max_chars=12)
      mobile_number = st.text_input("Mobile Number*", max_chars=15)

    with col2:
      email_address = st.text_input("Email Address*")
      it_portal_password = st.text_input(
          "Income Tax Portal Password", type="password"
      )
      entity_type = st.selectbox(
          "Entity Type*",
          [
              "Individual",
              "Hindu Undivided Family (HUF)",
              "Company",
              "Firm",
              "Association of Persons (AOP) / Body of Individuals (BOI)",
              "Local Authority",
              "Artificial Juridical Person",
          ],
      )
      assessment_year = st.selectbox(
          "Assessment Year (AY)*", ["2025-26", "2024-25", "2026-27"]
      )

    submitted = st.form_submit_button("Save Profile Details")

    if submitted:
      pan_regex = r"^[A-Z]{5}[0-9]{4}[A-Z]{1}$"
      email_regex = r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$"

      if not full_legal_name or not pan or not mobile_number or not email_address:
        st.error("Please fill in all mandatory fields (*).")
      elif not re.match(pan_regex, pan):
        st.error("Invalid PAN format. Standard format: ABCDE1234F")
      elif aadhaar_number and (
          not aadhaar_number.isdigit() or len(aadhaar_number) != 12
      ):
        st.error("Aadhaar Number must be exactly 12 numeric digits.")
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
            "assessment_year": assessment_year,
        }

        try:
          supabase.table("client_profiles").upsert(
              payload, on_conflict="pan"
          ).execute()
          st.success("Client profile successfully saved!")
        except Exception as e:
          st.error(f"Database error: {str(e)}")

  st.markdown("---")
  st.subheader("Export Profile Data")

  if st.button("Fetch & Prepare Profile Excel Export"):
    try:
      response = supabase.table("client_profiles").select("*").execute()
      data = response.data
      if data:
        df = pd.DataFrame(data)
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine="openpyxl") as writer:
          df.to_excel(writer, index=False, sheet_name="Client_Profiles")
        excel_data = output.getvalue()

        st.download_button(
            label="📥 Download Module 1 Profile Data (Excel)",
            data=excel_data,
            file_name="Module_1_Client_Profiles.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
      else:
        st.info("No profile records found in database to export.")
    except Exception as e:
      st.error(f"Failed to generate Excel file: {str(e)}")


# --- MODULE 2 RENDER ---
def render_module_2():
  st.header("Module 2: Income Tax Statutory Questionnaire")

  try:
    clients_res = (
        supabase.table("client_profiles")
        .select("id, full_legal_name, pan")
        .execute()
    )
    clients = clients_res.data
  except Exception as e:
    st.error(f"Failed to fetch client list: {str(e)}")
    clients = []

  if not clients:
    st.warning("Please add at least one client profile in Module 1 first.")
    return

  client_options = {
      f"{c['full_legal_name']} ({c['pan']})": c["id"] for c in clients
  }
  selected_client_label = st.selectbox(
      "Select Target Client*", list(client_options.keys())
  )
  selected_client_id = client_options[selected_client_label]

  with st.form("statutory_questionnaire_form"):
    residential_status = st.selectbox(
        "Residential Status (u/s 6)*",
        [
            "Resident and Ordinarily Resident (ROR)",
            "Resident but Not Ordinarily Resident (RNOR)",
            "Non-Resident (NR)",
        ],
    )

    col1, col2 = st.columns(2)
    with col1:
      has_dir_ind = st.checkbox("Directorship in Indian Company")
      has_dir_for = st.checkbox("Directorship in Foreign Company")
      holds_unlisted = st.checkbox("Holds Unlisted Equity Shares")
      has_fa = st.checkbox("Holds Foreign Assets (Schedule FA)")

    with col2:
      is_signing_auth = st.checkbox(
          "Signing Authority in Foreign Bank Account"
      )
      has_business = st.checkbox("Income from Business or Profession (PGBP)")
      has_cg = st.checkbox("Income from Capital Gains")
      has_speculative = st.checkbox("Speculative Income")

    submitted = st.form_submit_button("Evaluate & Save Assessment")

    if submitted:
      responses = {
          "residential_status": residential_status,
          "has_directorship_indian_co": has_dir_ind,
          "has_directorship_foreign_co": has_dir_for,
          "holds_unlisted_shares": holds_unlisted,
          "has_foreign_assets_schedule_fa": has_fa,
          "is_signing_authority_foreign_account": is_signing_auth,
          "has_business_profession_income": has_business,
          "has_capital_gains": has_cg,
          "has_speculative_income": has_speculative,
      }

      rec_itr = determine_itr_form(responses)

      payload = {
          "client_id": selected_client_id,
          **responses,
          "recommended_itr_form": rec_itr,
      }

      try:
        supabase.table("statutory_questionnaires").upsert(
            payload, on_conflict="client_id"
        ).execute()
        st.success(f"Assessment saved! Recommended ITR Form: {rec_itr}")
      except Exception as e:
        st.error(f"Database error: {str(e)}")

  st.markdown("---")
  st.subheader("Export Questionnaire Data")

  if st.button("Fetch & Prepare Questionnaire Excel Export"):
    try:
      response = (
          supabase.table("statutory_questionnaires").select("*").execute()
      )
      data = response.data
      if data:
        df = pd.DataFrame(data)
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine="openpyxl") as writer:
          df.to_excel(writer, index=False, sheet_name="Questionnaires")
        excel_data = output.getvalue()

        st.download_button(
            label="📥 Download Module 2 Data (Excel)",
            data=excel_data,
            file_name="Module_2_Statutory_Questionnaires.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
      else:
        st.info("No questionnaire records found in database to export.")
    except Exception as e:
      st.error(f"Failed to generate Excel file: {str(e)}")


# --- MAIN NAVIGATION ---
def main():
  st.title("💼 Income Tax & Wealth Management Suite")
  tab1, tab2 = st.tabs(
      ["Module 1: Client Profile", "Module 2: Statutory Questionnaire"]
  )

  with tab1:
    render_module_1()
  with tab2:
    render_module_2()


if __name__ == "__main__":
  main()
