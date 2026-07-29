import io
import json
import re
from datetime import date
import pandas as pd
import pdfplumber
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


# --- MODULE 4 PARSING LOGIC ---
def parse_vault_document(
    file_bytes: bytes, file_name: str, category: str
) -> dict:
  extracted_records = []

  try:
    if file_name.endswith(".pdf"):
      with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
        for page_num, page in enumerate(pdf.pages, start=1):
          text = page.extract_text() or ""
          tables = page.extract_tables() or []

          page_data = {
              "page": page_num,
              "text_snippet": text[:300],
              "tables": tables,
          }
          extracted_records.append(page_data)

    elif file_name.endswith((".xlsx", ".xls", ".csv")):
      if file_name.endswith(".csv"):
        df = pd.read_csv(io.BytesIO(file_bytes))
      else:
        df = pd.read_excel(io.BytesIO(file_bytes))

      extracted_records = df.head(100).to_dict(orient="records")

    else:
      extracted_records = [{
          "info": "Structured parser not available for this file type.",
          "file_name": file_name,
      }]

    return {
        "status": "success",
        "category": category,
        "record_count": len(extracted_records),
        "data": extracted_records,
    }
  except Exception as e:
    return {"status": "error", "error": str(e)}


# --- MODULE 6 CATEGORIZATION ENGINE ---
def auto_categorize_transaction(description: str) -> tuple:
  desc_upper = description.upper()

  if any(
      k in desc_upper for k in ["TAX", "CHALLAN", "ADVANCE TAX", "INCOME TAX"]
  ):
    return "Tax Payment (Advance/Self-Assessment)", "Sec 211 / Sec 140A"

  if any(k in desc_upper for k in ["DIVIDEND", "DIV ", "INTEREST", "INT PAID"]):
    return "Dividend / Interest Income", "Sec 56 (IFOS)"

  if any(
      k in desc_upper
      for k in ["MUTUAL FUND", "ZERODHA", "GROWW", "SIP", "EQUITY", "PURCHASE"]
  ):
    return "Investment / Capital Outflow", "Capital Asset / Sec 45"

  if any(
      k in desc_upper
      for k in ["SELF", "TRANSFER TO", "TRANSFER FROM", "SWEEP", "CONTRA"]
  ):
    return "Contra / Internal Transfer", "N/A (Excluded)"

  return "Uncategorized", "Review Needed"


# --- MODULE 7 CAPITAL GAINS ENGINE ---
def compute_capital_gains(
    asset_type: str,
    acq_date,
    transfer_date,
    sale_consideration: float,
    cost_acq: float,
    cost_imp: float,
    transfer_exp: float,
    fmv_jan_31_2018: float,
    indexation_applicable: bool,
) -> dict:
  holding_days = (transfer_date - acq_date).days

  is_ltcg = False
  if asset_type in [
      "Listed Equity Shares",
      "Equity Oriented Mutual Funds",
  ]:
    is_ltcg = holding_days > 365
  elif asset_type in [
      "Unlisted Shares",
      "Real Estate / Immovable Property",
      "Sovereign Gold Bonds (SGB)",
  ]:
    is_ltcg = holding_days > 730
  else:
    is_ltcg = holding_days > 1095

  effective_cost = cost_acq
  if (
      is_ltcg
      and asset_type
      in [
          "Listed Equity Shares",
          "Equity Oriented Mutual Funds",
      ]
      and fmv_jan_31_2018 > 0
  ):
    effective_cost = max(cost_acq, min(sale_consideration, fmv_jan_31_2018))

  net_consideration = sale_consideration - transfer_exp
  total_cost = effective_cost + cost_imp
  gain_loss = net_consideration - total_cost

  stcg = 0.0
  ltcg = 0.0
  section = "N/A"
  tax_rate = "0%"

  if is_ltcg:
    ltcg = gain_loss
    if asset_type in [
        "Listed Equity Shares",
        "Equity Oriented Mutual Funds",
    ]:
      section = "Sec 112A"
      tax_rate = "12.5%"
    else:
      section = "Sec 112"
      tax_rate = "12.5%"
  else:
    stcg = gain_loss
    if asset_type in [
        "Listed Equity Shares",
        "Equity Oriented Mutual Funds",
    ]:
      section = "Sec 111A"
      tax_rate = "20%"
    else:
      section = "Sec 45 (Slab Rate)"
      tax_rate = "Slab Rate"

  return {
      "is_ltcg": is_ltcg,
      "stcg": stcg if not is_ltcg else 0.0,
      "ltcg": ltcg if is_ltcg else 0.0,
      "section": section,
      "tax_rate": tax_rate,
  }


# --- MODULE 8 COMPUTATION LOGIC ---
def compute_sgb_debt_taxation(
    instrument_type: str,
    units: float,
    acq_date,
    transfer_date,
    acq_cost: float,
    sale_consideration: float,
    is_rbi_maturity: bool,
) -> dict:
  holding_days = (transfer_date - acq_date).days if transfer_date else 0

  is_exempt = False
  taxable_stcg = 0.0
  taxable_ltcg = 0.0
  section = "N/A"
  tax_rate = "0%"

  if instrument_type == "Sovereign Gold Bond (SGB)":
    if is_rbi_maturity:
      is_exempt = True
      section = "Sec 47(viib)"
      tax_rate = "Exempt"
    else:
      if holding_days > 730:
        taxable_ltcg = sale_consideration - acq_cost
        section = "Sec 112"
        tax_rate = "12.5%"
      else:
        taxable_stcg = sale_consideration - acq_cost
        section = "Sec 45 (Slab Rate)"
        tax_rate = "Slab Rate"

  elif instrument_type == "Specified Debt Mutual Fund (Sec 50AA)":
    taxable_stcg = sale_consideration - acq_cost
    section = "Sec 50AA"
    tax_rate = "Slab Rate"

  elif instrument_type in [
      "Listed Debenture / Bond",
      "Unlisted Debenture / Bond",
      "Commercial Paper / T-Bill",
  ]:
    threshold = 365 if "Listed" in instrument_type else 730
    if holding_days > threshold:
      taxable_ltcg = sale_consideration - acq_cost
      section = "Sec 112"
      tax_rate = "12.5%" if "Listed" in instrument_type else "20%"
    else:
      taxable_stcg = sale_consideration - acq_cost
      section = "Sec 45 (Slab Rate)"
      tax_rate = "Slab Rate"

  return {
      "is_exempt": is_exempt,
      "stcg": max(0.0, taxable_stcg),
      "ltcg": max(0.0, taxable_ltcg),
      "section": section,
      "tax_rate": tax_rate,
  }


# --- MODULE 10 COMPUTATION ENGINE ---
def compute_total_tax_liability(
    salary: float,
    house_prop: float,
    pgbp: float,
    stcg: float,
    ltcg: float,
    ifos: float,
    chapter_via: float,
) -> dict:
  std_deduction_old = 50000.0 if salary > 0 else 0.0
  net_salary_old = max(0.0, salary - std_deduction_old)
  gti_old = net_salary_old + house_prop + pgbp + stcg + ltcg + ifos
  nti_old = max(0.0, gti_old - chapter_via)

  tax_old = 0.0
  taxable_slab_old = max(0.0, nti_old - (stcg + ltcg))

  if taxable_slab_old > 1000000:
    tax_old += (taxable_slab_old - 1000000) * 0.30 + 112500
  elif taxable_slab_old > 500000:
    tax_old += (taxable_slab_old - 500000) * 0.20 + 12500
  elif taxable_slab_old > 250000:
    tax_old += (taxable_slab_old - 250000) * 0.05

  if taxable_slab_old <= 500000:
    rebate_old = min(tax_old, 12500.0)
    tax_old -= rebate_old

  tax_old += (stcg * 0.20) + max(0.0, (ltcg - 125000) * 0.125)
  total_tax_old = tax_old * 1.04

  std_deduction_new = 75000.0 if salary > 0 else 0.0
  net_salary_new = max(0.0, salary - std_deduction_new)
  gti_new = net_salary_new + house_prop + pgbp + stcg + ltcg + ifos
  nti_new = gti_new

  tax_new = 0.0
  taxable_slab_new = max(0.0, nti_new - (stcg + ltcg))

  if taxable_slab_new > 1500000:
    tax_new += (taxable_slab_new - 1500000) * 0.30 + 140000
  elif taxable_slab_new > 1200000:
    tax_new += (taxable_slab_new - 1200000) * 0.20 + 80000
  elif taxable_slab_new > 1000000:
    tax_new += (taxable_slab_new - 1000000) * 0.15 + 50000
  elif taxable_slab_new > 700000:
    tax_new += (taxable_slab_new - 700000) * 0.10 + 20000
  elif taxable_slab_new > 300000:
    tax_new += (taxable_slab_new - 300000) * 0.05

  if taxable_slab_new <= 700000:
    rebate_new = min(tax_new, 25000.0)
    tax_new -= rebate_new

  tax_new += (stcg * 0.20) + max(0.0, (ltcg - 125000) * 0.125)
  total_tax_new = tax_new * 1.04

  recommended = (
      "New Regime" if total_tax_new <= total_tax_old else "Old Regime"
  )

  return {
      "gti": gti_new,
      "nti_old": nti_old,
      "nti_new": nti_new,
      "tax_old_regime": round(total_tax_old, 2),
      "tax_new_regime": round(total_tax_new, 2),
      "recommended_regime": recommended,
  }


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
          "Assessment Year (AY)*", ["2026-27", "2025-26", "2024-25"]
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
      "Select Target Client*", list(client_options.keys()), key="m2_client"
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


# --- MODULE 3 RENDER ---
def render_module_3():
  st.header("Module 3: Document Vault & Repository")

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
      "Active Client Selection*", list(client_options.keys()), key="m3_client"
  )
  selected_client_id = client_options[selected_client_label]

  st.subheader("Upload Compliance & Financial Documents")
  uploaded_file = st.file_uploader("Choose a file")
  category = st.selectbox(
      "Category Classification*",
      [
          "Bank Statements",
          "AIS/TIS Documents",
          "Previous Year ITRs",
          "Sovereign Gold Bond (SGB) Certificates",
          "Demat Holdings Reports",
          "Broker Capital Gains Statements",
          "Form 26AS",
          "Form 16/16A",
          "Miscellaneous Documents",
      ],
  )

  if st.button("Upload Document"):
    if uploaded_file is not None:
      file_path = f"{selected_client_id}/{uploaded_file.name}"
      try:
        file_bytes = uploaded_file.read()
        supabase.storage.from_("vault_documents").upload(
            file_path, file_bytes, {"upsert": "true"}
        )

        doc_payload = {
            "client_id": selected_client_id,
            "file_name": uploaded_file.name,
            "file_path": file_path,
            "category": category,
        }
        supabase.table("document_vault").insert(doc_payload).execute()
        st.success(f"Successfully uploaded {uploaded_file.name}")
        st.rerun()
      except Exception as e:
        st.error(f"Upload failed: {str(e)}")
    else:
      st.error("Please select a file to upload.")

  st.markdown("---")
  st.subheader("Client Vault Repository")

  try:
    vault_res = (
        supabase.table("document_vault")
        .select("*")
        .eq("client_id", selected_client_id)
        .execute()
    )
    vault_docs = vault_res.data

    if vault_docs:
      doc_df = pd.DataFrame(vault_docs)
      display_df = doc_df[["file_name", "category", "uploaded_at"]].copy()
      st.dataframe(display_df, use_container_width=True)

      st.markdown("#### Delete Document")
      doc_to_delete = st.selectbox(
          "Select Document to Delete",
          options=vault_docs,
          format_func=lambda x: f"{x['file_name']} ({x['category']})",
      )

      if st.button("Delete Selected Document"):
        try:
          supabase.storage.from_("vault_documents").remove(
              [doc_to_delete["file_path"]]
          )
          supabase.table("document_vault").delete().eq(
              "id", doc_to_delete["id"]
          ).execute()
          st.success(f"Deleted {doc_to_delete['file_name']}")
          st.rerun()
        except Exception as e:
          st.error(f"Delete failed: {str(e)}")
    else:
      st.info("No documents found in vault for this client.")
  except Exception as e:
    st.error(f"Error fetching vault documents: {str(e)}")

  st.markdown("---")
  st.subheader("Export Vault Registry")

  if st.button("Fetch & Prepare Vault Registry Excel Export"):
    try:
      response = (
          supabase.table("document_vault")
          .select("*, client_profiles(full_legal_name, pan)")
          .execute()
      )
      data = response.data
      if data:
        flattened = []
        for row in data:
          client_info = row.get("client_profiles", {}) or {}
          flattened.append({
              "Client Name": client_info.get("full_legal_name"),
              "PAN": client_info.get("pan"),
              "File Name": row.get("file_name"),
              "Category": row.get("category"),
              "Upload Date": row.get("uploaded_at"),
          })
        df_export = pd.DataFrame(flattened)
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine="openpyxl") as writer:
          df_export.to_excel(
              writer, index=False, sheet_name="Vault_Registry"
          )
        excel_data = output.getvalue()

        st.download_button(
            label="📥 Download Module 3 Vault Registry (Excel)",
            data=excel_data,
            file_name="Module_3_Vault_Registry.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
      else:
        st.info("No vault records found to export.")
    except Exception as e:
      st.error(f"Failed to generate Excel file: {str(e)}")


# --- MODULE 4 RENDER ---
def render_module_4():
  st.header("Module 4: Automated Document Parsing Engine")

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
      "Select Active Client*", list(client_options.keys()), key="m4_client"
  )
  selected_client_id = client_options[selected_client_label]

  try:
    vault_res = (
        supabase.table("document_vault")
        .select("*")
        .eq("client_id", selected_client_id)
        .execute()
    )
    vault_docs = vault_res.data
  except Exception as e:
    st.error(f"Failed to fetch client documents: {str(e)}")
    vault_docs = []

  if not vault_docs:
    st.info("No documents uploaded for this client in Module 3.")
    return

  st.subheader("Trigger Document Parser")
  selected_doc = st.selectbox(
      "Select Document to Parse*",
      options=vault_docs,
      format_func=lambda x: f"{x['file_name']} ({x['category']})",
  )

  if st.button("Run AI Automated Parser"):
    with st.spinner("Downloading and parsing document line-by-line..."):
      try:
        file_bytes = supabase.storage.from_("vault_documents").download(
            selected_doc["file_path"]
        )

        parsed_json = parse_vault_document(
            file_bytes=file_bytes,
            file_name=selected_doc["file_name"],
            category=selected_doc["category"],
        )

        if parsed_json.get("status") == "success":
          staging_payload = {
              "client_id": selected_client_id,
              "vault_file_id": selected_doc["id"],
              "category": selected_doc["category"],
              "extracted_json": parsed_json,
              "status": "Pending Review",
          }

          supabase.table("parsed_data_staging").insert(
              staging_payload
          ).execute()
          st.success("Document parsed and dispatched to Module 5 Staging Vault!")
          st.rerun()
        else:
          st.error(
              f"Parsing error: {parsed_json.get('error', 'Unknown error')}"
          )

      except Exception as e:
        st.error(f"Failed to parse document: {str(e)}")

  st.markdown("---")
  st.subheader("Parsed Documents Staging Log")

  try:
    staging_res = (
        supabase.table("parsed_data_staging")
        .select("*, document_vault(file_name)")
        .eq("client_id", selected_client_id)
        .execute()
    )
    staging_data = staging_res.data

    if staging_data:
      log_list = []
      for row in staging_data:
        doc_info = row.get("document_vault", {}) or {}
        log_list.append({
            "File Name": doc_info.get("file_name"),
            "Category": row.get("category"),
            "Status": row.get("status"),
            "Parsed Date": row.get("created_at"),
        })
      st.dataframe(pd.DataFrame(log_list), use_container_width=True)
    else:
      st.info("No parsed records in staging log for this client.")
  except Exception as e:
    st.error(f"Error loading staging log: {str(e)}")


# --- MODULE 5 RENDER ---
def render_module_5():
  st.header("Module 5: Parsed Data Validation & Template Mapping Vault")

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
      "Select Active Client*", list(client_options.keys()), key="m5_client"
  )
  selected_client_id = client_options[selected_client_label]

  try:
    staging_res = (
        supabase.table("parsed_data_staging")
        .select("*, document_vault(file_name)")
        .eq("client_id", selected_client_id)
        .execute()
    )
    staging_records = staging_res.data
  except Exception as e:
    st.error(f"Failed to fetch staging data: {str(e)}")
    staging_records = []

  if not staging_records:
    st.info("No parsed records awaiting validation for this client.")
    return

  selected_staging = st.selectbox(
      "Select Staged Document for Mapping & Review*",
      options=staging_records,
      format_func=lambda x: (
          f"{x.get('document_vault', {}).get('file_name', 'Unknown File')}"
          f" ({x['category']})"
      ),
  )

  st.subheader("Template Mapping & Validation Controls")
  with st.form("template_mapping_form"):
    template_type = st.selectbox(
        "Assign Ledger Template*",
        [
            "Bank Statement Ledger",
            "AIS/TIS Data Schedule",
            "Capital Gains Broker Ledger",
            "SGB Portfolio Ledger",
            "Form 16 Tax Schedule",
            "Form 26AS TDS Register",
            "Generic Document Ledger",
        ],
    )

    template_approved = st.checkbox("Confirm Template Mapping", value=False)
    data_accuracy_approved = st.checkbox(
        "Verify Extracted Data Accuracy", value=False
    )

    st.markdown("#### Parsed Data Preview")
    extracted_json = selected_staging.get("extracted_json", {})
    st.json(extracted_json)

    submitted = st.form_submit_button("Save Mapping & Validation Sign-Off")

    if submitted:
      payload = {
          "client_id": selected_client_id,
          "vault_file_id": selected_staging["vault_file_id"],
          "template_type": template_type,
          "template_approved": template_approved,
          "extracted_data": extracted_json,
          "data_accuracy_approved": data_accuracy_approved,
      }

      try:
        supabase.table("parsed_template_mappings").insert(payload).execute()

        new_status = (
            "Approved"
            if (template_approved and data_accuracy_approved)
            else "Pending Review"
        )
        supabase.table("parsed_data_staging").update(
            {"status": new_status}
        ).eq("id", selected_staging["id"]).execute()

        st.success("Validation and template mapping saved successfully!")
        st.rerun()
      except Exception as e:
        st.error(f"Failed to save mapping: {str(e)}")

  st.markdown("---")
  st.subheader("Export Validated Mappings")

  if st.button("Fetch & Prepare Mappings Excel Export"):
    try:
      response = (
          supabase.table("parsed_template_mappings")
          .select("*, client_profiles(full_legal_name, pan)")
          .eq("client_id", selected_client_id)
          .execute()
      )
      data = response.data
      if data:
        flattened = []
        for row in data:
          client_info = row.get("client_profiles", {}) or {}
          flattened.append({
              "Client Name": client_info.get("full_legal_name"),
              "PAN": client_info.get("pan"),
              "Template Type": row.get("template_type"),
              "Template Approved": row.get("template_approved"),
              "Data Accuracy Verified": row.get("data_accuracy_approved"),
              "Created At": row.get("created_at"),
          })
        df_export = pd.DataFrame(flattened)
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine="openpyxl") as writer:
          df_export.to_excel(
              writer, index=False, sheet_name="Validated_Mappings"
          )
        excel_data = output.getvalue()

        st.download_button(
            label="📥 Download Module 5 Validated Mappings (Excel)",
            data=excel_data,
            file_name="Module_5_Validated_Mappings.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
      else:
        st.info("No validated mapping records found to export.")
    except Exception as e:
      st.error(f"Failed to generate Excel file: {str(e)}")


# --- MODULE 6 RENDER ---
def render_module_6():
  st.header("Module 6: Standardized Bank Ledger & Categorization Engine")

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
      "Select Active Client*", list(client_options.keys()), key="m6_client"
  )
  selected_client_id = client_options[selected_client_label]

  st.subheader("Manual Transaction Entry & Categorization")
  with st.form("manual_txn_form"):
    col1, col2 = st.columns(2)
    with col1:
      txn_date = st.date_input("Transaction Date*")
      description = st.text_input("Transaction Narrative / Description*")
      debit_amount = st.number_input(
          "Debit Amount (₹)", min_value=0.0, step=100.0
      )

    with col2:
      credit_amount = st.number_input(
          "Credit Amount (₹)", min_value=0.0, step=100.0
      )
      balance = st.number_input("Running Balance (₹)", step=100.0)

      auto_cat, auto_sec = auto_categorize_transaction(description)
      category = st.selectbox(
          "Category*",
          [
              "Business Receipt",
              "Business Expense",
              "Personal Expense",
              "Tax Payment (Advance/Self-Assessment)",
              "Investment / Capital Outflow",
              "Dividend / Interest Income",
              "Contra / Internal Transfer",
              "Uncategorized",
          ],
          index=[
              "Business Receipt",
              "Business Expense",
              "Personal Expense",
              "Tax Payment (Advance/Self-Assessment)",
              "Investment / Capital Outflow",
              "Dividend / Interest Income",
              "Contra / Internal Transfer",
              "Uncategorized",
          ].index(auto_cat),
      )

    it_act_section = st.text_input(
        "Income Tax Act Provision / Section", value=auto_sec
    )
    is_verified = st.checkbox("Mark Entry as Verified", value=True)

    submitted = st.form_submit_button("Save Transaction to Ledger")

    if submitted:
      if not description:
        st.error("Transaction description is required.")
      else:
        payload = {
            "client_id": selected_client_id,
            "txn_date": str(txn_date),
            "description": description,
            "debit_amount": debit_amount,
            "credit_amount": credit_amount,
            "balance": balance,
            "category": category,
            "it_act_section": it_act_section,
            "is_verified": is_verified,
        }

        try:
          supabase.table("bank_ledger_transactions").insert(payload).execute()
          st.success("Transaction successfully added to bank ledger!")
          st.rerun()
        except Exception as e:
          st.error(f"Database error: {str(e)}")

  st.markdown("---")
  st.subheader("Bank Ledger Transactions Register")

  try:
    ledger_res = (
        supabase.table("bank_ledger_transactions")
        .select("*")
        .eq("client_id", selected_client_id)
        .order("txn_date", desc=True)
        .execute()
    )
    ledger_data = ledger_res.data

    if ledger_data:
      ledger_df = pd.DataFrame(ledger_data)
      display_cols = [
          "txn_date",
          "description",
          "debit_amount",
          "credit_amount",
          "balance",
          "category",
          "it_act_section",
          "is_verified",
      ]
      st.dataframe(ledger_df[display_cols], use_container_width=True)
    else:
      st.info("No transaction records found in ledger for this client.")
  except Exception as e:
    st.error(f"Error fetching ledger: {str(e)}")

  st.markdown("---")
  st.subheader("Export Bank Ledger")

  if st.button("Fetch & Prepare Bank Ledger Excel Export"):
    try:
      response = (
          supabase.table("bank_ledger_transactions")
          .select("*, client_profiles(full_legal_name, pan)")
          .eq("client_id", selected_client_id)
          .execute()
      )
      data = response.data
      if data:
        flattened = []
        for row in data:
          client_info = row.get("client_profiles", {}) or {}
          flattened.append({
              "Client Name": client_info.get("full_legal_name"),
              "PAN": client_info.get("pan"),
              "Date": row.get("txn_date"),
              "Description": row.get("description"),
              "Debit": row.get("debit_amount"),
              "Credit": row.get("credit_amount"),
              "Balance": row.get("balance"),
              "Category": row.get("category"),
              "IT Provision": row.get("it_act_section"),
              "Verified": row.get("is_verified"),
          })
        df_export = pd.DataFrame(flattened)
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine="openpyxl") as writer:
          df_export.to_excel(writer, index=False, sheet_name="Bank_Ledger")
        excel_data = output.getvalue()

        st.download_button(
            label="📥 Download Module 6 Bank Ledger (Excel)",
            data=excel_data,
            file_name="Module_6_Bank_Ledger.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
      else:
        st.info("No bank ledger records found to export.")
    except Exception as e:
      st.error(f"Failed to generate Excel file: {str(e)}")


# --- MODULE 7 RENDER ---
def render_module_7():
  st.header("Module 7: Capital Gains & Securities Portfolio Engine")

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
      "Select Active Client*", list(client_options.keys()), key="m7_client"
  )
  selected_client_id = client_options[selected_client_label]

  st.subheader("Record Asset Transfer & Compute Capital Gains")
  with st.form("capital_gains_form"):
    col1, col2 = st.columns(2)
    with col1:
      asset_type = st.selectbox(
          "Asset Class*",
          [
              "Listed Equity Shares",
              "Equity Oriented Mutual Funds",
              "Debt Mutual Funds",
              "Unlisted Shares",
              "Real Estate / Immovable Property",
              "Sovereign Gold Bonds (SGB)",
              "Bonds / Debentures",
              "Other Assets",
          ],
      )
      asset_name = st.text_input("Asset Name / Security Scrip*")
      isin_code = st.text_input("ISIN Code (Optional)", max_chars=12)
      quantity = st.number_input("Quantity / Units", min_value=0.0001, value=1.0)
      acq_date = st.date_input("Acquisition Date*")
      transfer_date = st.date_input("Transfer / Sale Date*")

    with col2:
      sale_consideration = st.number_input(
          "Full Value of Consideration (Sale Value) (₹)*",
          min_value=0.0,
          step=1000.0,
      )
      cost_acq = st.number_input(
          "Cost of Acquisition (₹)*", min_value=0.0, step=1000.0
      )
      cost_imp = st.number_input(
          "Cost of Improvement (₹)", min_value=0.0, step=1000.0
      )
      transfer_exp = st.number_input(
          "Transfer Expenses / Brokerage (₹)", min_value=0.0, step=100.0
      )
      fmv_jan_31_2018 = st.number_input(
          "FMV as on Jan 31, 2018 (Sec 112A Grandfathering) (₹)",
          min_value=0.0,
          step=1000.0,
      )
      indexation_applicable = st.checkbox(
          "Apply Indexation (If eligible u/s 48)"
      )

    submitted = st.form_submit_button("Compute & Save Transaction")

    if submitted:
      if not asset_name:
        st.error("Asset Name is required.")
      elif transfer_date < acq_date:
        st.error("Transfer date cannot be earlier than Acquisition date.")
      else:
        cg_res = compute_capital_gains(
            asset_type=asset_type,
            acq_date=acq_date,
            transfer_date=transfer_date,
            sale_consideration=sale_consideration,
            cost_acq=cost_acq,
            cost_imp=cost_imp,
            transfer_exp=transfer_exp,
            fmv_jan_31_2018=fmv_jan_31_2018,
            indexation_applicable=indexation_applicable,
        )

        payload = {
            "client_id": selected_client_id,
            "asset_type": asset_type,
            "asset_name": asset_name,
            "isin_code": isin_code if isin_code else None,
            "quantity": quantity,
            "acquisition_date": str(acq_date),
            "transfer_date": str(transfer_date),
            "sale_consideration": sale_consideration,
            "cost_of_acquisition": cost_acq,
            "cost_of_improvement": cost_imp,
            "transfer_expenses": transfer_exp,
            "fmv_as_on_jan_31_2018": fmv_jan_31_2018,
            "indexation_applicable": indexation_applicable,
            "computed_stcg": cg_res["stcg"],
            "computed_ltcg": cg_res["ltcg"],
            "applicable_tax_rate": float(
                cg_res["tax_rate"].replace("%", "")
                if "%" in cg_res["tax_rate"]
                else 0.0
            ),
            "it_act_section": cg_res["section"],
        }

        try:
          supabase.table("capital_gains_portfolio").insert(payload).execute()
          st.success("Capital Gains entry recorded successfully!")
          st.info(
              f"Gain Classification: {'LTCG' if cg_res['is_ltcg'] else 'STCG'}"
              f" | Section: {cg_res['section']} | Tax Rate:"
              f" {cg_res['tax_rate']} | Computed STCG: ₹{cg_res['stcg']:,.2f} |"
              f" Computed LTCG: ₹{cg_res['ltcg']:,.2f}"
          )
          st.rerun()
        except Exception as e:
          st.error(f"Database error: {str(e)}")

  st.markdown("---")
  st.subheader("Capital Gains Portfolio Register")

  try:
    portfolio_res = (
        supabase.table("capital_gains_portfolio")
        .select("*")
        .eq("client_id", selected_client_id)
        .execute()
    )
    portfolio_data = portfolio_res.data

    if portfolio_data:
      p_df = pd.DataFrame(portfolio_data)
      display_cols = [
          "asset_type",
          "asset_name",
          "acquisition_date",
          "transfer_date",
          "sale_consideration",
          "cost_of_acquisition",
          "computed_stcg",
          "computed_ltcg",
          "it_act_section",
      ]
      st.dataframe(p_df[display_cols], use_container_width=True)
    else:
      st.info("No capital gains transactions recorded for this client.")
  except Exception as e:
    st.error(f"Error fetching portfolio: {str(e)}")

  st.markdown("---")
  st.subheader("Export Capital Gains Portfolio")

  if st.button("Fetch & Prepare Capital Gains Excel Export"):
    try:
      response = (
          supabase.table("capital_gains_portfolio")
          .select("*, client_profiles(full_legal_name, pan)")
          .eq("client_id", selected_client_id)
          .execute()
      )
      data = response.data
      if data:
        flattened = []
        for row in data:
          client_info = row.get("client_profiles", {}) or {}
          flattened.append({
              "Client Name": client_info.get("full_legal_name"),
              "PAN": client_info.get("pan"),
              "Asset Class": row.get("asset_type"),
              "Asset Name": row.get("asset_name"),
              "ISIN": row.get("isin_code"),
              "Quantity": row.get("quantity"),
              "Acquisition Date": row.get("acquisition_date"),
              "Transfer Date": row.get("transfer_date"),
              "Sale Value": row.get("sale_consideration"),
              "Cost of Acquisition": row.get("cost_of_acquisition"),
              "STCG": row.get("computed_stcg"),
              "LTCG": row.get("computed_ltcg"),
              "IT Provision": row.get("it_act_section"),
          })
        df_export = pd.DataFrame(flattened)
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine="openpyxl") as writer:
          df_export.to_excel(
              writer, index=False, sheet_name="Capital_Gains_Register"
          )
        excel_data = output.getvalue()

        st.download_button(
            label="📥 Download Module 7 Capital Gains Portfolio (Excel)",
            data=excel_data,
            file_name="Module_7_Capital_Gains.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
      else:
        st.info("No capital gains records found to export.")
    except Exception as e:
      st.error(f"Failed to generate Excel file: {str(e)}")


# --- MODULE 8 RENDER ---
def render_module_8():
  st.header("Module 8: Sovereign Gold Bond (SGB) & Debt Portfolio Engine")

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
      "Select Active Client*", list(client_options.keys()), key="m8_client"
  )
  selected_client_id = client_options[selected_client_label]

  st.subheader("Record SGB / Debt Asset Holdings & Realization")
  with st.form("sgb_debt_form"):
    col1, col2 = st.columns(2)
    with col1:
      instrument_type = st.selectbox(
          "Instrument Type*",
          [
              "Sovereign Gold Bond (SGB)",
              "Listed Debenture / Bond",
              "Unlisted Debenture / Bond",
              "Specified Debt Mutual Fund (Sec 50AA)",
              "Commercial Paper / T-Bill",
          ],
      )
      instrument_name = st.text_input(
          "Instrument Name / Tranche (e.g., SGB 2021-22 Series V)*"
      )
      units_held = st.number_input("Units / Quantity Held*", min_value=0.001)
      issue_purchase_date = st.date_input("Purchase / Issue Date*")
      total_acq_cost = st.number_input(
          "Total Acquisition Cost (₹)*", min_value=0.0, step=1000.0
      )

    with col2:
      annual_interest = st.number_input(
          "Annual Interest Income Received (IFOS u/s 56) (₹)",
          min_value=0.0,
          step=500.0,
      )
      is_sold = st.checkbox(
          "Has this holding been redeemed / transferred during the AY?"
      )

      redemption_sale_date = None
      total_sale_consideration = 0.0
      is_rbi_maturity = False

      if is_sold:
        redemption_sale_date = st.date_input("Redemption / Transfer Date*")
        total_sale_consideration = st.number_input(
            "Total Sale Consideration (₹)*", min_value=0.0, step=1000.0
        )
        if instrument_type == "Sovereign Gold Bond (SGB)":
          is_rbi_maturity = st.checkbox(
              "Redemption directly by RBI upon Maturity (Sec 47(viib) Exempt)"
          )

    submitted = st.form_submit_button("Compute & Save Debt Holding")

    if submitted:
      if not instrument_name:
        st.error("Instrument Name is required.")
      else:
        tax_res = compute_sgb_debt_taxation(
            instrument_type=instrument_type,
            units=units_held,
            acq_date=issue_purchase_date,
            transfer_date=redemption_sale_date,
            acq_cost=total_acq_cost,
            sale_consideration=total_sale_consideration,
            is_rbi_maturity=is_rbi_maturity,
        )

        payload = {
            "client_id": selected_client_id,
            "instrument_type": instrument_type,
            "instrument_name": instrument_name,
            "units_held": units_held,
            "issue_purchase_date": str(issue_purchase_date),
            "total_acquisition_cost": total_acq_cost,
            "redemption_sale_date": (
                str(redemption_sale_date) if redemption_sale_date else None
            ),
            "total_sale_consideration": total_sale_consideration,
            "is_rbi_maturity_redemption": is_rbi_maturity,
            "annual_interest_received": annual_interest,
        }

        try:
          supabase.table("sgb_debt_portfolio").insert(payload).execute()
          st.success("SGB / Debt transaction recorded successfully!")
          if is_sold:
            if tax_res["is_exempt"]:
              st.info(
                  f"Capital Gain Status: EXEMPT under {tax_res['section']}"
              )
            else:
              st.info(
                  f"Tax Provision: {tax_res['section']} | Rate:"
                  f" {tax_res['tax_rate']} | STCG: ₹{tax_res['stcg']:,.2f} |"
                  f" LTCG: ₹{tax_res['ltcg']:,.2f}"
              )
          st.rerun()
        except Exception as e:
          st.error(f"Database error: {str(e)}")

  st.markdown("---")
  st.subheader("SGB & Debt Holdings Register")

  try:
    portfolio_res = (
        supabase.table("sgb_debt_portfolio")
        .select("*")
        .eq("client_id", selected_client_id)
        .execute()
    )
    portfolio_data = portfolio_res.data

    if portfolio_data:
      p_df = pd.DataFrame(portfolio_data)
      display_cols = [
          "instrument_type",
          "instrument_name",
          "units_held",
          "issue_purchase_date",
          "total_acquisition_cost",
          "annual_interest_received",
          "redemption_sale_date",
          "total_sale_consideration",
          "is_rbi_maturity_redemption",
      ]
      st.dataframe(p_df[display_cols], use_container_width=True)
    else:
      st.info("No SGB or Debt instruments recorded for this client.")
  except Exception as e:
    st.error(f"Error fetching portfolio: {str(e)}")


# --- MODULE 9 RENDER ---
def render_module_9():
  st.header("Module 9: AIS/TIS Cross-Reconciliation & Discrepancy Engine")

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
      "Select Active Client*", list(client_options.keys()), key="m9_client"
  )
  selected_client_id = client_options[selected_client_label]

  st.subheader("Record AIS / TIS Information & Book Reconciliation Entry")
  with st.form("ais_tis_reconcile_form"):
    col1, col2 = st.columns(2)
    with col1:
      info_code = st.selectbox(
          "Information Code / Category*",
          [
              "TDS-194J (Professional Fees)",
              "TDS-194A (Interest Income)",
              "TDS-194C (Contractor Payments)",
              "TDS-194IA (Transfer of Immovable Property)",
              "SFT-005 (Cash Deposit / Withdrawal)",
              "SFT-012 (Purchase of Shares/MFs)",
              "MUTUAL_FUND_SALE (MF Redemption)",
              "DIVIDEND (Dividend Received)",
              "OFFSHORE_REMITTANCE (LRS u/s 206C)",
              "OTHER_INFORMATION",
          ],
      )
      source_desc = st.text_input(
          "Source / Reporter Description*",
          placeholder="e.g., State Bank of India / Zerodha Broking",
      )
      reported_ais = st.number_input(
          "Reported Amount in AIS (₹)", min_value=0.0, step=1000.0
      )

    with col2:
      reported_tis = st.number_input(
          "Reported Amount in TIS (₹)", min_value=0.0, step=1000.0
      )
      books_amount = st.number_input(
          "Actual Amount as per Books / Bank Ledger (₹)",
          min_value=0.0,
          step=1000.0,
      )

      calc_variance = reported_tis - books_amount
      st.markdown(f"**Computed Variance (TIS - Books):** ₹{calc_variance:,.2f}")

      discrepancy_status = st.selectbox(
          "Discrepancy Status*",
          [
              "Reconciled - Matched",
              "Timing Difference",
              "Information Incorrect in AIS",
              "Under-Reported in Books",
              "Duplicate Entry in AIS",
              "Unreconciled",
          ],
      )

    client_remarks = st.text_area(
        "Client / Consultant Remarks for Portal Feedback"
    )

    submitted = st.form_submit_button("Save Reconciliation Entry")

    if submitted:
      if not source_desc:
        st.error("Source Description is required.")
      else:
        payload = {
            "client_id": selected_client_id,
            "information_code": info_code.split(" ")[0],
            "source_description": source_desc,
            "reported_amount_ais": reported_ais,
            "reported_amount_tis": reported_tis,
            "books_amount": books_amount,
            "discrepancy_status": discrepancy_status,
            "client_remarks": client_remarks if client_remarks else None,
        }

        try:
          supabase.table("ais_tis_reconciliation").insert(payload).execute()
          st.success("AIS/TIS reconciliation entry saved successfully!")
          st.rerun()
        except Exception as e:
          st.error(f"Database error: {str(e)}")

  st.markdown("---")
  st.subheader("AIS/TIS Reconciliation Register")

  try:
    recon_res = (
        supabase.table("ais_tis_reconciliation")
        .select("*")
        .eq("client_id", selected_client_id)
        .execute()
    )
    recon_data = recon_res.data

    if recon_data:
      r_df = pd.DataFrame(recon_data)
      display_cols = [
          "information_code",
          "source_description",
          "reported_amount_ais",
          "reported_amount_tis",
          "books_amount",
          "variance",
          "discrepancy_status",
          "client_remarks",
      ]
      st.dataframe(r_df[display_cols], use_container_width=True)
    else:
      st.info("No AIS/TIS reconciliation records found for this client.")
  except Exception as e:
    st.error(f"Error fetching reconciliation data: {str(e)}")

  st.markdown("---")
  st.subheader("Export AIS/TIS Reconciliation Register")

  if st.button("Fetch & Prepare AIS/TIS Excel Export"):
    try:
      response = (
          supabase.table("ais_tis_reconciliation")
          .select("*, client_profiles(full_legal_name, pan)")
          .eq("client_id", selected_client_id)
          .execute()
      )
      data = response.data
      if data:
        flattened = []
        for row in data:
          client_info = row.get("client_profiles", {}) or {}
          flattened.append({
              "Client Name": client_info.get("full_legal_name"),
              "PAN": client_info.get("pan"),
              "Information Code": row.get("information_code"),
              "Source Description": row.get("source_description"),
              "AIS Amount": row.get("reported_amount_ais"),
              "TIS Amount": row.get("reported_amount_tis"),
              "Books Amount": row.get("books_amount"),
              "Variance": row.get("variance"),
              "Status": row.get("discrepancy_status"),
              "Remarks": row.get("client_remarks"),
          })
        df_export = pd.DataFrame(flattened)
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine="openpyxl") as writer:
          df_export.to_excel(
              writer, index=False, sheet_name="AIS_TIS_Reconciliation"
          )
        excel_data = output.getvalue()

        st.download_button(
            label="📥 Download Module 9 AIS/TIS Register (Excel)",
            data=excel_data,
            file_name="Module_9_AIS_TIS_Reconciliation.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
      else:
        st.info("No AIS/TIS reconciliation records found to export.")
    except Exception as e:
      st.error(f"Failed to generate Excel file: {str(e)}")


# --- MODULE 10 RENDER ---
def render_module_10():
  st.header(
      "Module 10: Five Heads of Income & Final Tax Computation Dashboard"
  )

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
      "Select Active Client*", list(client_options.keys()), key="m10_client"
  )
  selected_client_id = client_options[selected_client_label]

  st.subheader("Compute Head-wise Income & Tax Liability")
  with st.form("tax_computation_form"):
    st.markdown("### 1. Five Heads of Income (₹)")
    col1, col2, col3 = st.columns(3)
    with col1:
      inc_salary = st.number_input(
          "Gross Salary Income (Sec 15-17)", min_value=0.0, step=5000.0
      )
      inc_house_prop = st.number_input(
          "Income / Loss from House Property (Sec 22-27)", step=5000.0
      )
    with col2:
      inc_pgbp = st.number_input(
          "Profits & Gains of Business / Profession (PGBP)", step=5000.0
      )
      inc_stcg = st.number_input("Short Term Capital Gains (STCG)", step=5000.0)
    with col3:
      inc_ltcg = st.number_input("Long Term Capital Gains (LTCG)", step=5000.0)
      inc_ifos = st.number_input(
          "Income from Other Sources (IFOS - Interest/Dividend)", step=5000.0
      )

    st.markdown("### 2. Deductions & Tax Credits (₹)")
    col4, col5 = st.columns(2)
    with col4:
      chapter_via_deductions = st.number_input(
          "Chapter VI-A Deductions (80C, 80D, 80CCD, etc. - Old Regime)",
          min_value=0.0,
          step=5000.0,
      )
      tds_tcs_credit = st.number_input(
          "TDS / TCS Prepaid Tax Credit", min_value=0.0, step=1000.0
      )
    with col5:
      advance_tax = st.number_input(
          "Advance Tax Paid u/s 211", min_value=0.0, step=1000.0
      )
      self_assessment_tax = st.number_input(
          "Self Assessment Tax Paid u/s 140A", min_value=0.0, step=1000.0
      )
      interest_234 = st.number_input(
          "Interest u/s 234A / 234B / 234C", min_value=0.0, step=500.0
      )

    submitted = st.form_submit_button("Run Tax Computation Engine")

    if submitted:
      tax_calc = compute_total_tax_liability(
          salary=inc_salary,
          house_prop=inc_house_prop,
          pgbp=inc_pgbp,
          stcg=inc_stcg,
          ltcg=inc_ltcg,
          ifos=inc_ifos,
          chapter_via=chapter_via_deductions,
      )

      chosen_tax = (
          tax_calc["tax_new_regime"]
          if tax_calc["recommended_regime"] == "New Regime"
          else tax_calc["tax_old_regime"]
      )
      total_prepaid = tds_tcs_credit + advance_tax + self_assessment_tax
      net_payable_refundable = (
          (chosen_tax + interest_234) - total_prepaid
      )

      payload = {
          "client_id": selected_client_id,
          "assessment_year": "2026-27",
          "income_salary": inc_salary,
          "income_house_property": inc_house_prop,
          "income_pgbp": inc_pgbp,
          "income_capital_gains_stcg": inc_stcg,
          "income_capital_gains_ltcg": inc_ltcg,
          "income_ifos": inc_ifos,
          "gross_total_income": tax_calc["gti"],
          "deductions_chapter_via": chapter_via_deductions,
          "net_taxable_income": (
              tax_calc["nti_new"]
              if tax_calc["recommended_regime"] == "New Regime"
              else tax_calc["nti_old"]
          ),
          "tax_liability_old_regime": tax_calc["tax_old_regime"],
          "tax_liability_new_regime": tax_calc["tax_new_regime"],
          "recommended_tax_regime": tax_calc["recommended_regime"],
          "tds_tcs_credit": tds_tcs_credit,
          "advance_tax_paid": advance_tax,
          "self_assessment_tax_paid": self_assessment_tax,
          "interest_u_s_234a_b_c": interest_234,
          "net_tax_payable_or_refundable": net_payable_refundable,
      }

      try:
        supabase.table("tax_computations").upsert(
            payload, on_conflict="client_id, assessment_year"
        ).execute()
        st.success("Tax computation generated & saved successfully!")

        st.markdown("---")
        st.markdown("### 📊 Tax Analysis Summary")

        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Gross Total Income", f"₹{tax_calc['gti']:,.2f}")
        m2.metric("Old Regime Tax", f"₹{tax_calc['tax_old_regime']:,.2f}")
        m3.metric("New Regime Tax", f"₹{tax_calc['tax_new_regime']:,.2f}")
        m4.metric("Optimal Regime", tax_calc["recommended_regime"])

        if net_payable_refundable > 0:
          st.warning(
              f"⚠️ Balance Net Tax Payable: ₹{net_payable_refundable:,.2f}"
          )
        elif net_payable_refundable < 0:
          st.success(
              f"🎉 Refund Due to Client: ₹{abs(net_payable_refundable):,.2f}"
          )
        else:
          st.info("✅ Net Tax Position: Fully Settled / Zero Balance")

        st.rerun()
      except Exception as e:
        st.error(f"Database error: {str(e)}")

  st.markdown("---")
  st.subheader("Saved Tax Computation Summary")

  try:
    tax_res = (
        supabase.table("tax_computations")
        .select("*")
        .eq("client_id", selected_client_id)
        .execute()
    )
    tax_data = tax_res.data

    if tax_data:
      t_df = pd.DataFrame(tax_data)
      display_cols = [
          "assessment_year",
          "gross_total_income",
          "tax_liability_old_regime",
          "tax_liability_new_regime",
          "recommended_tax_regime",
          "net_tax_payable_or_refundable",
      ]
      st.dataframe(t_df[display_cols], use_container_width=True)
    else:
      st.info("No tax computations found for this client.")
  except Exception as e:
    st.error(f"Error fetching computation records: {str(e)}")

  st.markdown("---")
  st.subheader("Export Final Tax Computation")

  if st.button("Fetch & Prepare Final Computation Excel Export"):
    try:
      response = (
          supabase.table("tax_computations")
          .select("*, client_profiles(full_legal_name, pan)")
          .eq("client_id", selected_client_id)
          .execute()
      )
      data = response.data
      if data:
        flattened = []
        for row in data:
          client_info = row.get("client_profiles", {}) or {}
          flattened.append({
              "Client Name": client_info.get("full_legal_name"),
              "PAN": client_info.get("pan"),
              "AY": row.get("assessment_year"),
              "Salary": row.get("income_salary"),
              "House Property": row.get("income_house_property"),
              "PGBP": row.get("income_pgbp"),
              "STCG": row.get("income_capital_gains_stcg"),
              "LTCG": row.get("income_capital_gains_ltcg"),
              "IFOS": row.get("income_ifos"),
              "GTI": row.get("gross_total_income"),
              "Chapter VI-A": row.get("deductions_chapter_via"),
              "Old Regime Tax": row.get("tax_liability_old_regime"),
              "New Regime Tax": row.get("tax_liability_new_regime"),
              "Optimal Regime": row.get("recommended_tax_regime"),
              "Net Tax Payable/Refundable": row.get(
                  "net_tax_payable_or_refundable"
              ),
          })
        df_export = pd.DataFrame(flattened)
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine="openpyxl") as writer:
          df_export.to_excel(
              writer, index=False, sheet_name="Tax_Computation"
          )
        excel_data = output.getvalue()

        st.download_button(
            label="📥 Download Module 10 Tax Computation (Excel)",
            data=excel_data,
            file_name="Module_10_Tax_Computation.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
      else:
        st.info("No computation records found to export.")
    except Exception as e:
      st.error(f"Failed to generate Excel file: {str(e)}")


# --- MODULE 11 RENDER ---
def render_module_11():
  st.header(
      "Module 11: Comprehensive Wealth & Net Worth Statement (Schedule AL)"
  )

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
      "Select Active Client*", list(client_options.keys()), key="m11_client"
  )
  selected_client_id = client_options[selected_client_label]

  financial_year = st.selectbox(
      "Financial Year (FY)*", ["2025-26", "2024-25", "2023-24"]
  )

  try:
    tax_comp_res = (
        supabase.table("tax_computations")
        .select("gross_total_income")
        .eq("client_id", selected_client_id)
        .execute()
    )
    if tax_comp_res.data:
      gti = tax_comp_res.data[0].get("gross_total_income", 0.0)
      if gti > 5000000:
        st.warning(
            f"⚠️ Schedule AL Mandated u/s 139: Total Income (₹{gti:,.2f}) exceeds"
            " ₹50 Lakhs threshold."
        )
      else:
        st.info(
            f"ℹ️ Total Income is ₹{gti:,.2f} (Below ₹50 Lakhs threshold)."
            " Schedule AL is Optional."
        )
  except Exception:
    pass

  tab_asset, tab_liab = st.tabs(
      ["1. Asset Register", "2. Liabilities Register"]
  )

  with tab_asset:
    st.subheader("Record Wealth Asset Holding")
    with st.form("wealth_asset_form"):
      col1, col2 = st.columns(2)
      with col1:
        asset_category = st.selectbox(
            "Asset Category*",
            [
                "Immovable Property (Land / Building)",
                "Movable - Financial (Shares / Debentures / MFs)",
                "Movable - SGB & Bullion / Jewelry",
                "Movable - Vehicles / Aircraft / Yachts",
                "Movable - Cash & Bank Balances",
                "Movable - Insurance Policies",
                "Other Assets",
            ],
        )
        asset_description = st.text_input("Asset Description / Address*")
      with col2:
        cost_acq = st.number_input(
            "Cost of Acquisition / Value as per Books (₹)*",
            min_value=0.0,
            step=50000.0,
        )
        est_market_val = st.number_input(
            "Estimated Market Value (₹)", min_value=0.0, step=50000.0
        )

      sub_asset = st.form_submit_button("Save Asset Record")

      if sub_asset:
        if not asset_description:
          st.error("Asset Description is required.")
        else:
          payload = {
              "client_id": selected_client_id,
              "financial_year": financial_year,
              "asset_category": asset_category,
              "asset_description": asset_description,
              "cost_of_acquisition": cost_acq,
              "estimated_market_value": est_market_val,
          }
          try:
            supabase.table("wealth_net_worth_assets").insert(payload).execute()
            st.success("Asset successfully recorded!")
            st.rerun()
          except Exception as e:
            st.error(f"Database error: {str(e)}")

  with tab_liab:
    st.subheader("Record Liability / Loan Balance")
    with st.form("wealth_liability_form"):
      col3, col4 = st.columns(2)
      with col3:
        liability_category = st.selectbox(
            "Liability Category*",
            [
                "Housing Loan / Mortgage",
                "Vehicle Loan",
                "Secured Business / Portfolio Loan",
                "Unsecured Loan / Personal Loan",
                "Bank Overdraft",
                "Other Liabilities",
            ],
        )
        lender_name = st.text_input("Lender Name / Financial Institution*")
      with col4:
        outstanding_amount = st.number_input(
            "Outstanding Amount as on March 31 (₹)*",
            min_value=0.0,
            step=50000.0,
        )

      sub_liab = st.form_submit_button("Save Liability Record")

      if sub_liab:
        if not lender_name:
          st.error("Lender Name is required.")
        else:
          payload = {
              "client_id": selected_client_id,
              "financial_year": financial_year,
              "liability_category": liability_category,
              "lender_name": lender_name,
              "outstanding_amount": outstanding_amount,
          }
          try:
            supabase.table("wealth_net_worth_liabilities").insert(
                payload
            ).execute()
            st.success("Liability successfully recorded!")
            st.rerun()
          except Exception as e:
            st.error(f"Database error: {str(e)}")

  st.markdown("---")
  st.subheader("📊 Net Worth Summary Dashboard")

  try:
    assets_res = (
        supabase.table("wealth_net_worth_assets")
        .select("*")
        .eq("client_id", selected_client_id)
        .eq("financial_year", financial_year)
        .execute()
    )
    liab_res = (
        supabase.table("wealth_net_worth_liabilities")
        .select("*")
        .eq("client_id", selected_client_id)
        .eq("financial_year", financial_year)
        .execute()
    )

    assets_data = assets_res.data or []
    liab_data = liab_res.data or []

    total_assets_cost = sum(
        a.get("cost_of_acquisition", 0.0) for a in assets_data
    )
    total_assets_market = sum(
        a.get("estimated_market_value", 0.0) for a in assets_data
    )
    total_liabilities = sum(
        l.get("outstanding_amount", 0.0) for l in liab_data
    )

    net_worth_cost = total_assets_cost - total_liabilities
    net_worth_market = total_assets_market - total_liabilities

    n1, n2, n3, n4 = st.columns(4)
    n1.metric("Total Assets (Book Value)", f"₹{total_assets_cost:,.2f}")
    n2.metric("Total Assets (Market Value)", f"₹{total_assets_market:,.2f}")
    n3.metric("Total Liabilities", f"₹{total_liabilities:,.2f}")
    n4.metric("Net Worth (Market Value)", f"₹{net_worth_market:,.2f}")

    if assets_data:
      st.markdown("#### Asset Portfolio Schedule")
      st.dataframe(pd.DataFrame(assets_data)[
          [
              "asset_category",
              "asset_description",
              "cost_of_acquisition",
              "estimated_market_value",
          ]
      ], use_container_width=True)

    if liab_data:
      st.markdown("#### Liabilities Schedule")
      st.dataframe(pd.DataFrame(liab_data)[
          [
              "liability_category",
              "lender_name",
              "outstanding_amount",
          ]
      ], use_container_width=True)

  except Exception as e:
    st.error(f"Error fetching Net Worth statement: {str(e)}")

  st.markdown("---")
  st.subheader("Export Net Worth Statement")

  if st.button("Fetch & Prepare Net Worth Excel Export"):
    try:
      assets_exp = (
          supabase.table("wealth_net_worth_assets")
          .select("*, client_profiles(full_legal_name, pan)")
          .eq("client_id", selected_client_id)
          .eq("financial_year", financial_year)
          .execute()
      )
      liab_exp = (
          supabase.table("wealth_net_worth_liabilities")
          .select("*, client_profiles(full_legal_name, pan)")
          .eq("client_id", selected_client_id)
          .eq("financial_year", financial_year)
          .execute()
      )

      output = io.BytesIO()
      with pd.ExcelWriter(output, engine="openpyxl") as writer:
        if assets_exp.data:
          pd.DataFrame(assets_exp.data).to_excel(
              writer, index=False, sheet_name="Schedule_AL_Assets"
          )
        if liab_exp.data:
          pd.DataFrame(liab_exp.data).to_excel(
              writer, index=False, sheet_name="Schedule_AL_Liabilities"
          )

      excel_data = output.getvalue()
      st.download_button(
          label="📥 Download Module 11 Net Worth Statement (Excel)",
          data=excel_data,
          file_name=f"Module_11_Net_Worth_Statement_{financial_year}.xlsx",
          mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
      )
    except Exception as e:
      st.error(f"Failed to generate Excel file: {str(e)}")


# --- MODULE 12 RENDER ---
def render_module_12():
  st.header(
      "Module 12: Foreign Assets (Schedule FA) & Foreign Tax Credit (Schedule"
      " TR / Form 67)"
  )

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
      "Select Active Client*", list(client_options.keys()), key="m12_client"
  )
  selected_client_id = client_options[selected_client_label]

  st.error(
      "⚠️ **Statutory Risk Alert u/s 43 of Black Money Act, 2015:** Non-disclosure"
      " or inaccurate reporting of foreign assets in Schedule FA attracts a"
      " mandatory penalty of ₹10,000,00 per year."
  )

  tab_fa, tab_tr = st.tabs(
      ["1. Schedule FA (Foreign Assets)", "2. Schedule TR & Form 67 (FTC)"]
  )

  with tab_fa:
    st.subheader("Record Foreign Asset / Account Holding")
    with st.form("schedule_fa_form"):
      col1, col2 = st.columns(2)
      with col1:
        calendar_year = st.text_input("Calendar Year (CY)*", value="2025")
        country_code = st.text_input("Country Code (e.g., USA, GBR, SGP)*")
        country_name = st.text_input("Country Name*")
        asset_category = st.selectbox(
            "Asset Category*",
            [
                "Foreign Depository Account",
                "Foreign Custodial Account",
                "Foreign Equity & Debt Interest (ESOPs/RSUs)",
                "Financial Interest in Foreign Entity",
                "Foreign Immovable Property",
                "Other Foreign Assets / Accounts",
            ],
        )
        institution_name = st.text_input(
            "Institution / Entity / Property Name*"
        )
        account_tin = st.text_input("Account Number / Foreign TIN*")

      with col2:
        peak_balance = st.number_input(
            "Peak Balance during CY (₹)*", min_value=0.0, step=10000.0
        )
        closing_balance = st.number_input(
            "Closing Balance as on Dec 31 (₹)*", min_value=0.0, step=10000.0
        )
        gross_income = st.number_input(
            "Gross Income Derived in CY (₹)", min_value=0.0, step=5000.0
        )
        gross_sales = st.number_input(
            "Gross Sale Proceeds Realized in CY (₹)",
            min_value=0.0,
            step=10000.0,
        )

      sub_fa = st.form_submit_button("Save Foreign Asset Record")

      if sub_fa:
        if not country_code or not institution_name or not account_tin:
          st.error("Please fill in mandatory foreign asset fields.")
        else:
          payload = {
              "client_id": selected_client_id,
              "calendar_year": calendar_year,
              "country_code": country_code.upper(),
              "country_name": country_name,
              "asset_category": asset_category,
              "institution_or_entity_name": institution_name,
              "account_number_or_tin": account_tin,
              "peak_balance_during_cy": peak_balance,
              "closing_balance_as_on_dec_31": closing_balance,
              "gross_income_derived_in_cy": gross_income,
              "gross_sale_proceeds_realized_in_cy": gross_sales,
          }
          try:
            supabase.table("schedule_fa_foreign_assets").insert(
                payload
            ).execute()
            st.success("Foreign asset entry saved to Schedule FA!")
            st.rerun()
          except Exception as e:
            st.error(f"Database error: {str(e)}")

    st.markdown("---")
    st.subheader("Foreign Assets Register (Schedule FA)")
    try:
      fa_res = (
          supabase.table("schedule_fa_foreign_assets")
          .select("*")
          .eq("client_id", selected_client_id)
          .execute()
      )
      if fa_res.data:
        st.dataframe(pd.DataFrame(fa_res.data), use_container_width=True)
      else:
        st.info("No foreign assets recorded for this client.")
    except Exception as e:
      st.error(f"Error fetching Schedule FA data: {str(e)}")

  with tab_tr:
    st.subheader("Record Foreign Tax Credit (FTC) & Form 67")
    with st.form("schedule_tr_form"):
      col3, col4 = st.columns(2)
      with col3:
        ay_tr = st.selectbox(
            "Assessment Year*", ["2026-27", "2025-26", "2024-25"]
        )
        country_code_tr = st.text_input("Foreign Country Code*")
        foreign_tax_id = st.text_input("Foreign Tax ID (TIN)")
        foreign_income = st.number_input(
            "Foreign Income Taxable in India (₹)*", min_value=0.0, step=10000.0
        )

      with col4:
        tax_paid_outside = st.number_input(
            "Tax Paid Outside India (₹)*", min_value=0.0, step=5000.0
        )
        claimed_ftc = st.number_input(
            "Claimed Foreign Tax Credit u/s 90/91 (₹)*",
            min_value=0.0,
            step=5000.0,
        )
        form_67_date = st.date_input("Form 67 Filing Date")
        form_67_ack = st.text_input("Form 67 Acknowledgement Number")

      sub_tr = st.form_submit_button("Save Tax Credit (Schedule TR)")

      if sub_tr:
        if not country_code_tr:
          st.error("Country Code is required.")
        else:
          payload = {
              "client_id": selected_client_id,
              "assessment_year": ay_tr,
              "country_code": country_code_tr.upper(),
              "foreign_tax_id": foreign_tax_id,
              "foreign_income_taxable_in_india": foreign_income,
              "tax_paid_outside_india": tax_paid_outside,
              "claimed_ftc_sec_90_91": claimed_ftc,
              "form_67_filed_date": str(form_67_date) if form_67_date else None,
              "form_67_acknowledgement_number": form_67_ack,
          }
          try:
            supabase.table("schedule_tr_tax_credits").insert(payload).execute()
            st.success("Foreign Tax Credit entry saved!")
            st.rerun()
          except Exception as e:
            st.error(f"Database error: {str(e)}")

    st.markdown("---")
    st.subheader("Foreign Tax Credits Register (Schedule TR)")
    try:
      tr_res = (
          supabase.table("schedule_tr_tax_credits")
          .select("*")
          .eq("client_id", selected_client_id)
          .execute()
      )
      if tr_res.data:
        st.dataframe(pd.DataFrame(tr_res.data), use_container_width=True)
      else:
        st.info("No foreign tax credits recorded for this client.")
    except Exception as e:
      st.error(f"Error fetching Schedule TR data: {str(e)}")


# --- MAIN NAVIGATION ---
def main():
  st.title("💼 Income Tax & Wealth Management Suite")
  (
      tab1,
      tab2,
      tab3,
      tab4,
      tab5,
      tab6,
      tab7,
      tab8,
      tab9,
      tab10,
      tab11,
      tab12,
  ) = st.tabs([
      "Module 1: Profile",
      "Module 2: Questionnaire",
      "Module 3: Vault",
      "Module 4: Parser",
      "Module 5: Validation",
      "Module 6: Bank Ledger",
      "Module 7: Capital Gains",
      "Module 8: SGB & Debt",
      "Module 9: AIS/TIS Reconcile",
      "Module 10: Tax Computation",
      "Module 11: Schedule AL",
      "Module 12: Schedule FA & TR",
  ])

  with tab1:
    render_module_1()
  with tab2:
    render_module_2()
  with tab3:
    render_module_3()
  with tab4:
    render_module_4()
  with tab5:
    render_module_5()
  with tab6:
    render_module_6()
  with tab7:
    render_module_7()
  with tab8:
    render_module_8()
  with tab9:
    render_module_9()
  with tab10:
    render_module_10()
  with tab11:
    render_module_11()
  with tab12:
    render_module_12()


if __name__ == "__main__":
  main()
