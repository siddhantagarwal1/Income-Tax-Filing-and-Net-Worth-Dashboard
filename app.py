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


# --- MAIN NAVIGATION ---
def main():
  st.title("💼 Income Tax & Wealth Management Suite")
  tab1, tab2, tab3, tab4, tab5, tab6, tab7, tab8, tab9 = st.tabs([
      "Module 1: Client Profile",
      "Module 2: Statutory Questionnaire",
      "Module 3: Document Vault",
      "Module 4: Parsing Engine",
      "Module 5: Validation & Mapping",
      "Module 6: Bank Ledger",
      "Module 7: Capital Gains",
      "Module 8: SGB & Debt Portfolio",
      "Module 9: AIS/TIS Reconciliation",
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


if __name__ == "__main__":
  main()
