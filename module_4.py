import json
import pandas as pd
import streamlit as st
from supabase import Client, create_client


# --- Supabase Initialization ---
@st.cache_resource
def init_supabase() -> Client:
    url = st.secrets["SUPABASE_URL"]
    key = st.secrets["SUPABASE_KEY"]
    return create_client(url, key)


supabase = init_supabase()


# --- Mock Parsing Engine (Replaces OCR/PDF Extractors for Staging) ---
def parse_document_content(file_name: str, category: str) -> dict:
    """Extracts structured financial and tax line-items based on document category."""
    if category == "Form 16/16A":
        return {
            "employer_name": "ABC Tech Solutions Pvt Ltd",
            "employer_tan": "DELA12345B",
            "gross_salary_sec17_1": 1850000.00,
            "perquisites_sec17_2": 45000.00,
            "exemptions_sec10": {"standard_deduction": 50000.00, "hra": 180000.00},
            "tds_deducted": 245000.00,
        }
    elif category == "Form 26AS":
        return {
            "tds_entries": [
                {"deductor": "ABC Tech Solutions", "section": "192", "amount_paid": 1895000, "tds": 245000},
                {"deductor": "HDFC Bank Ltd", "section": "194A", "amount_paid": 45000, "tds": 4500},
            ]
        }
    elif category == "AIS/TIS Documents":
        return {
            "salary_income": 1895000.00,
            "savings_interest": 12500.00,
            "fd_interest": 45000.00,
            "dividend_income": 18000.00,
            "share_transactions_sale_value": 350000.00,
        }
    elif category == "Bank Statements":
        return {
            "bank_name": "HDFC Bank",
            "account_number_mask": "XX4892",
            "total_credits": 2150000.00,
            "total_debits": 1420000.00,
            "interest_credited": 12500.00,
        }
    elif category == "Sovereign Gold Bond (SGB) Certificates":
        return {
            "issuing_authority": "Reserve Bank of India",
            "investment_amount": 250000.00,
            "units_held": 50,
            "interest_rate_pct": 2.5,
            "annual_interest_payout": 6250.00,
        }
    elif category == "Demat Holdings Reports":
        return {
            "broker_name": "Zerodha",
            "portfolio_value": 1250000.00,
            "equity_holdings_count": 8,
            "mutual_fund_nav_value": 450000.00,
        }
    elif category == "Broker Capital Gains Statements":
        return {
            "broker_name": "Zerodha",
            "stcg_equity_sec111a": 45000.00,
            "ltcg_equity_sec112a": 115000.00,
            "total_turnover": 850000.00,
        }
    else:
        return {
            "extracted_text_summary": f"Generic extraction for {file_name}",
            "records_found": 1,
        }


# --- Module 4 Renderer ---
def render_module_4():
    st.markdown(
        """
        <div class="module-header-container">
            <div class="module-title">Module 4: Financial & Tax Data Ingestion Engine</div>
            <div class="module-subtitle">Automated Parsing, Feature Extraction & Staging Repository</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # 1. Active Client Selector
    try:
        clients_res = (
            supabase.table("client_profiles")
            .select("id, full_name_pan, pan")
            .execute()
        )
        clients = clients_res.data
    except Exception as e:
        st.error(f"Error fetching clients: {str(e)}")
        return

    if not clients:
        st.info("No client profiles found. Please onboard clients in Module 1 first.")
        return

    client_dict = {f"{c['full_name_pan']} ({c['pan']})": c["id"] for c in clients}

    st.markdown(
        "<h4 style='color: #1d4ed8; margin-bottom: 8px;'>Target Client Context</h4>",
        unsafe_allow_html=True,
    )
    selected_client_name = st.selectbox(
        "Select Active Client*", list(client_dict.keys()), key="m4_client_select"
    )
    selected_client_id = client_dict[selected_client_name]

    st.divider()

    # 2. Vault Document Selector for Ingestion
    st.markdown(
        "<h3 style='color: #1e3a8a; margin-bottom: 16px;'>1. Trigger Document Parsing Engine</h3>",
        unsafe_allow_html=True,
    )

    try:
        vault_res = (
            supabase.table("document_vault")
            .select("id, file_name, category, is_password_protected, file_password")
            .eq("client_id", selected_client_id)
            .execute()
        )
        vault_files = vault_res.data
    except Exception as e:
        st.error(f"Error fetching vault documents: {str(e)}")
        return

    if not vault_files:
        st.warning("No documents available in Vault for this client. Please upload files in Module 3 first.")
        return

    doc_map = {
        f"{doc['file_name']} [{doc['category']}]": doc for doc in vault_files
    }

    col1, col2 = st.columns([3, 1])

    with col1:
        selected_doc_label = st.selectbox(
            "Select Uploaded Document to Ingest & Parse*",
            list(doc_map.keys()),
            key="m4_doc_select",
        )

    target_doc = doc_map[selected_doc_label]

    # Map Category to Enums
    category_enum_map = {
        "Form 16/16A": "FORM_16",
        "Form 26AS": "FORM_26AS",
        "AIS/TIS Documents": "AIS_TIS",
        "Bank Statements": "BANK_STATEMENT",
        "Broker Capital Gains Statements": "CAPITAL_GAINS_STATEMENT",
        "Sovereign Gold Bond (SGB) Certificates": "SGB_CERTIFICATE",
        "Demat Holdings Reports": "DEMAT_HOLDINGS",
        "Previous Year ITRs": "PREVIOUS_YEAR_ITR",
    }
    enum_type = category_enum_map.get(target_doc["category"], "AIS_TIS")

    with col2:
        st.write("")
        st.write("")
        parse_btn = st.button("Parse Document", type="primary", key="m4_parse_btn")

    if parse_btn:
        with st.spinner(f"Extracting line items from {target_doc['file_name']}..."):
            parsed_json = parse_document_content(
                target_doc["file_name"], target_doc["category"]
            )

            payload = {
                "client_id": selected_client_id,
                "document_id": target_doc["id"],
                "doc_type": enum_type,
                "parsed_json": parsed_json,
                "is_validated": False,
            }

            try:
                supabase.table("parsed_document_data").insert(payload).execute()
                st.success(
                    f"Parsed & Staged data successfully for {target_doc['file_name']}!"
                )
                st.rerun()
            except Exception as parse_err:
                st.error(f"Failed to save parsed data: {str(parse_err)}")

    st.divider()

    # 3. Parsed Data Staging Viewer
    st.markdown(
        "<h3 style='color: #1e3a8a; margin-bottom: 16px;'>2. Parsed Data Staging Repository</h3>",
        unsafe_allow_html=True,
    )

    try:
        parsed_res = (
            supabase.table("parsed_document_data")
            .select("id, document_id, doc_type, parsed_json, is_validated, created_at")
            .eq("client_id", selected_client_id)
            .order("created_at", desc=True)
            .execute()
        )
        parsed_records = parsed_res.data

        if parsed_records:
            st.markdown("##### **Extracted Line Items (Staged)**")

            for record in parsed_records:
                status_badge = "🟢 Validated" if record["is_validated"] else "🟠 Pending Validation"
                doc_type_label = record["doc_type"]
                created_date = (
                    record["created_at"].split("T")[0]
                    if "T" in record["created_at"]
                    else record["created_at"]
                )

                with st.expander(
                    f"{doc_type_label} | Created: {created_date} | Status: {status_badge}"
                ):
                    st.json(record["parsed_json"])

                    c1, c2 = st.columns([1, 1])
                    with c1:
                        if not record["is_validated"]:
                            if st.button(
                                "Mark as Validated", key=f"val_{record['id']}"
                            ):
                                supabase.table("parsed_document_data").update(
                                    {"is_validated": True}
                                ).eq("id", record["id"]).execute()
                                st.success("Marked as Validated!")
                                st.rerun()

                    with c2:
                        if st.button("Delete Entry", key=f"del_parsed_{record['id']}"):
                            supabase.table("parsed_document_data").delete().eq(
                                "id", record["id"]
                            ).execute()
                            st.success("Deleted staged record!")
                            st.rerun()
        else:
            st.info("No parsed data found for this client. Select a document above to run ingestion.")

    except Exception as e:
        st.error(f"Error loading parsed records: {str(e)}")


if __name__ == "__main__":
    render_module_4()
