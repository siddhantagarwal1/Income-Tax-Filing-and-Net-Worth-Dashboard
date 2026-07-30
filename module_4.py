import json
import pandas as pd
import streamlit as st
from google import genai
from google.genai import types
from supabase import Client, create_client


# --- Supabase Initialization ---
@st.cache_resource
def init_supabase() -> Client:
    url = st.secrets["SUPABASE_URL"]
    key = st.secrets["SUPABASE_KEY"]
    return create_client(url, key)


supabase = init_supabase()


# --- Gemini Client Initialization ---
@st.cache_resource
def init_gemini_client() -> genai.Client:
    api_key = st.secrets.get("GEMINI_API_KEY")
    if not api_key:
        st.error("Missing GEMINI_API_KEY in Streamlit Secrets.")
        st.stop()
    return genai.Client(api_key=api_key)


gemini_client = init_gemini_client()


# --- Real Document Parser via Gemini 2.5 Flash ---
def parse_document_content(file_bytes: bytes, file_name: str, category: str) -> dict:
    """Uses Gemini 2.5 Flash to accurately extract structured tax and financial data."""
    mime_type = "application/pdf"
    if file_name.lower().endswith(".jpg") or file_name.lower().endswith(".jpeg"):
        mime_type = "image/jpeg"
    elif file_name.lower().endswith(".png"):
        mime_type = "image/png"

    prompt = f"""
    You are an expert Indian Income Tax & Financial Audit Extraction Engine.
    Analyze this uploaded document ({file_name}) belonging to category: "{category}".
    Extract ALL actionable numeric line items, monetary values, dates, holder details, and statutory fields.
    
    Format requirements:
    1. Output strictly valid JSON without any markdown code block wrappers (no ```json).
    2. Ensure key names are standardized in snake_case.
    3. Convert monetary amounts to clear float values (e.g., 250000.00).
    4. For Sovereign Gold Bonds (SGB), extract: issuing_authority, investment_amount, units_held, interest_rate_pct, issue_date, folio_number.
    5. For AIS/TIS / Previous Year ITR / Form 16 / Bank Statements, extract all schedule-level totals and line items precisely.
    """

    try:
        response = gemini_client.models.generate_content(
            model="gemini-2.0-flash-lite",
            contents=[
                types.Part.from_bytes(data=file_bytes, mime_type=mime_type),
                prompt,
            ],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.1,
            ),
        )
        return json.loads(response.text)
    except Exception as e:
        return {
            "error": f"LLM Extraction failed: {str(e)}",
            "file_name": file_name,
            "category": category,
        }


def get_doc_enum_type(category: str) -> str:
    """Maps document categories to target Supabase ENUM types."""
    cat_upper = category.upper()
    if "FORM 16" in cat_upper or "16A" in cat_upper:
        return "FORM_16"
    elif "26AS" in cat_upper:
        return "FORM_26AS"
    elif "SGB" in cat_upper or "SOVEREIGN GOLD BOND" in cat_upper:
        return "SGB_CERTIFICATE"
    elif "BANK" in cat_upper:
        return "BANK_STATEMENT"
    elif "BROKER" in cat_upper or "CAPITAL GAINS" in cat_upper:
        return "CAPITAL_GAINS_STATEMENT"
    elif "DEMAT" in cat_upper:
        return "DEMAT_HOLDINGS"
    elif "PREVIOUS YEAR" in cat_upper or "ITR" in cat_upper:
        return "PREVIOUS_YEAR_ITR"
    elif "AIS" in cat_upper or "TIS" in cat_upper:
        return "AIS_TIS"
    return "MISCELLANEOUS"


# --- Module 4 Renderer ---
def render_module_4():
    st.markdown(
        """
        <div class="module-header-container">
            <div class="module-title">Module 4: Financial & Tax Data Ingestion Engine</div>
            <div class="module-subtitle">Automated AI Parsing, Extraction & Staging Repository</div>
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
            .select("id, file_name, file_path, category, is_password_protected, file_password")
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
    enum_type = get_doc_enum_type(target_doc["category"])

    with col2:
        st.write("")
        st.write("")
        parse_btn = st.button("Parse Document", type="primary", key="m4_parse_btn")

    if parse_btn:
        with st.spinner(f"Extracting real line items from {target_doc['file_name']} via Gemini..."):
            try:
                # Fetch original file bytes directly from Supabase Storage bucket
                try:
                    file_bytes = supabase.storage.from_("client_vault").download(target_doc["file_path"])
                except Exception:
                    file_bytes = supabase.storage.from_("vault_documents").download(target_doc["file_path"])
                
                parsed_json = parse_document_content(
                    file_bytes=file_bytes,
                    file_name=target_doc["file_name"],
                    category=target_doc["category"],
                )

                payload = {
                    "client_id": selected_client_id,
                    "document_id": target_doc["id"],
                    "doc_type": enum_type,
                    "parsed_json": parsed_json,
                    "is_validated": False,
                }

                supabase.table("parsed_document_data").insert(payload).execute()
                st.success(f"Parsed & Staged data successfully for {target_doc['file_name']}!")
                st.rerun()
            except Exception as parse_err:
                st.error(f"Failed to fetch or parse file: {str(parse_err)}")

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
                            if st.button("Mark as Validated", key=f"val_{record['id']}"):
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
