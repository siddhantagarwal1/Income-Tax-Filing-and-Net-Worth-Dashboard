import io
import json
import re
import pdfplumber
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


# --- Helper Extraction Routines ---
def _clean_number(val) -> float:
    """Helper to convert string amounts to float strictly."""
    if val is None:
        return 0.0
    clean = re.sub(r"[^\d.-]", "", str(val).replace(",", ""))
    try:
        return float(clean) if clean else 0.0
    except ValueError:
        return 0.0


def parse_bank_statement(pdf: pdfplumber.PDF, raw_text: str) -> dict:
    """Extracts structured line-by-line transaction ledgers and verifies balance reconciliation."""
    ledger = []

    for page in pdf.pages:
        tables = page.extract_tables()
        for table in tables:
            if not table or len(table) < 2:
                continue

            # Identify headers
            header = [str(c).lower().strip() if c else "" for c in table[0]]
            
            date_idx, desc_idx, dr_idx, cr_idx, bal_idx, amt_idx = -1, -1, -1, -1, -1, -1
            for i, h in enumerate(header):
                if "date" in h:
                    date_idx = i
                elif any(k in h for k in ["narration", "particular", "description", "details", "remark"]):
                    desc_idx = i
                elif any(k in h for k in ["debit", "withdrawal", "dr"]):
                    dr_idx = i
                elif any(k in h for k in ["credit", "deposit", "cr"]):
                    cr_idx = i
                elif "balance" in h:
                    bal_idx = i
                elif "amount" in h:
                    amt_idx = i

            for row in table[1:]:
                if not row or all(c is None or str(c).strip() == "" for c in row):
                    continue

                row_str = " ".join([str(c) for c in row if c])
                
                # Verify row contains a valid date
                date_match = re.search(r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b", row_str)
                if not date_match:
                    continue

                txn_date = date_match.group(0)
                desc = str(row[desc_idx]).strip() if desc_idx != -1 and desc_idx < len(row) and row[desc_idx] else row_str

                debit = _clean_number(row[dr_idx]) if dr_idx != -1 and dr_idx < len(row) else 0.0
                credit = _clean_number(row[cr_idx]) if cr_idx != -1 and cr_idx < len(row) else 0.0
                balance = _clean_number(row[bal_idx]) if bal_idx != -1 and bal_idx < len(row) else 0.0

                # Handle combined Amount + Type columns if Debit/Credit columns aren't split
                if dr_idx == -1 and cr_idx == -1 and amt_idx != -1 and amt_idx < len(row):
                    amt_val = _clean_number(row[amt_idx])
                    if "CR" in row_str.upper() or "DEPOSIT" in row_str.upper():
                        credit = amt_val
                    else:
                        debit = amt_val

                if debit == 0.0 and credit == 0.0:
                    continue

                txn_type = "Credit" if credit > 0 else "Debit"
                amount = credit if credit > 0 else debit

                # Tax Categorization Engine
                category = "General Transfer"
                desc_upper = desc.upper()
                if any(term in desc_upper for term in ["INT.PD", "INT CREDIT", "SAVINGS INTEREST", "INTEREST PAID", "INT PROCESS"]):
                    category = "Savings Interest (Sec 80TTA/80TTB)"
                elif "FD INT" in desc_upper or "TERM DEPOSIT INT" in desc_upper:
                    category = "Fixed Deposit Interest"
                elif "DIVIDEND" in desc_upper or "DIV" in desc_upper:
                    category = "Dividend Income (Sec 56(2)(i))"
                elif "SGB" in desc_upper and "INT" in desc_upper:
                    category = "SGB Interest Income"
                elif "REFUND" in desc_upper or "INCOME TAX" in desc_upper:
                    category = "Income Tax Refund (Sec 244A)"

                ledger.append({
                    "date": txn_date,
                    "description": desc,
                    "transaction_type": txn_type,
                    "amount": amount,
                    "debit": debit,
                    "credit": credit,
                    "running_balance": balance,
                    "classified_category": category
                })

    # Summary extraction & Reconciliation logic
    op_match = re.search(r"(?i)(?:opening balance|b/f)\D*([\d,]+\.\d{2})", raw_text)
    cl_match = re.search(r"(?i)(?:closing balance|c/f)\D*([\d,]+\.\d{2})", raw_text)

    opening_bal = _clean_number(op_match.group(1)) if op_match else (ledger[0]["running_balance"] - ledger[0]["credit"] + ledger[0]["debit"] if ledger else 0.0)
    closing_bal = _clean_number(cl_match.group(1)) if cl_match else (ledger[-1]["running_balance"] if ledger else 0.0)

    total_credits = sum(item["credit"] for item in ledger)
    total_debits = sum(item["debit"] for item in ledger)

    calc_closing = round(opening_bal - total_debits + total_credits, 2)
    reconciliation_passed = bool(abs(calc_closing - closing_bal) <= 1.0) if closing_bal > 0 else True

    detected_interest_items = [t for t in ledger if "Interest" in t["classified_category"]]
    total_interest_detected = sum(t["amount"] for t in detected_interest_items)

    return {
        "summary": {
            "opening_balance": opening_bal,
            "total_credits": round(total_credits, 2),
            "total_debits": round(total_debits, 2),
            "extracted_closing_balance": closing_bal,
            "calculated_closing_balance": calc_closing,
            "reconciliation_passed": reconciliation_passed
        },
        "total_interest_detected": total_interest_detected,
        "interest_transactions": detected_interest_items,
        "transaction_ledger": ledger
    }


def parse_ais_tis(raw_text: str) -> dict:
    """Parses AIS/TIS figures."""
    tds = re.findall(r"(?i)(?:tds|tax deducted)\D*([\d,]+\.\d{2})", raw_text)
    interest = re.findall(r"(?i)(?:interest from savings|194a)\D*([\d,]+\.\d{2})", raw_text)
    return {
        "reported_tds_total": sum([_clean_number(x) for x in tds]),
        "reported_interest_total": sum([_clean_number(x) for x in interest]),
    }


def parse_sgb_certificate(raw_text: str) -> dict:
    """Parses SGB details."""
    series = re.search(r"(?i)(?:series|tranche)\D*([A-Z0-9/-]+)", raw_text)
    units = re.search(r"(?i)(?:units|quantity)\D*(\d+)", raw_text)
    amount = re.search(r"(?i)(?:amount|consideration|issue price)\D*([\d,]+\.\d{2})", raw_text)
    inv_amount = _clean_number(amount.group(1)) if amount else 0.0
    return {
        "bond_series": series.group(1) if series else "Unknown",
        "units_held": int(units.group(1)) if units else 0,
        "investment_amount": inv_amount,
        "expected_semi_annual_payout": round((inv_amount * 0.025) / 2, 2)
    }


def parse_ppf_passbook(raw_text: str) -> dict:
    """Parses PPF details."""
    contributions = re.findall(r"(?i)(?:deposit|subscription)\D*([\d,]+\.\d{2})", raw_text)
    interest = re.search(r"(?i)(?:interest credited|int paid)\D*([\d,]+\.\d{2})", raw_text)
    balance = re.search(r"(?i)(?:closing balance|balance c/f)\D*([\d,]+\.\d{2})", raw_text)
    return {
        "total_contributions": sum([_clean_number(c) for c in contributions]),
        "interest_credited": _clean_number(interest.group(1)) if interest else 0.0,
        "closing_balance": _clean_number(balance.group(1)) if balance else 0.0
    }


# --- Unified Parser Engine Router ---
def parse_document_content(
    file_bytes: bytes, file_name: str, category: str, password: str = None
) -> dict:
    cat_upper = category.upper()
    raw_text = ""

    try:
        open_kwargs = {"password": password} if password else {}
        with pdfplumber.open(io.BytesIO(file_bytes), **open_kwargs) as pdf:
            for page in pdf.pages:
                text = page.extract_text()
                if text:
                    raw_text += text + "\n"

            extracted_data = {
                "file_name": file_name,
                "category": category,
                "raw_text_length": len(raw_text),
            }

            if "BANK" in cat_upper:
                extracted_data["parsed_bank_details"] = parse_bank_statement(pdf, raw_text)
            elif "AIS" in cat_upper or "TIS" in cat_upper:
                extracted_data["parsed_ais_tis_details"] = parse_ais_tis(raw_text)
            elif "SGB" in cat_upper or "SOVEREIGN GOLD BOND" in cat_upper:
                extracted_data["parsed_sgb_details"] = parse_sgb_certificate(raw_text)
            elif "PPF" in cat_upper or "PROVIDENT FUND" in cat_upper:
                extracted_data["parsed_ppf_details"] = parse_ppf_passbook(raw_text)
            elif "FORM 16" in cat_upper or "16A" in cat_upper:
                pan = re.search(r"[A-Z]{5}[0-9]{4}[A-Z]{1}", raw_text)
                extracted_data["pan_detected"] = pan.group(0) if pan else None
            else:
                extracted_data["summary_preview"] = raw_text[:500] if raw_text else "No text found."

            return extracted_data

    except Exception as e:
        return {"error": f"Failed to extract document: {str(e)}", "file_name": file_name}


def get_doc_enum_type(category: str) -> str:
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


# --- Module 4 Streamlit Renderer ---
def render_module_4():
    st.markdown(
        """
        <div class="module-header-container">
            <div class="module-title">Module 4: Financial & Tax Data Ingestion Engine</div>
            <div class="module-subtitle">Automated Multi-Document Parsing, Reconciliation & Staging Ledger</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

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
        st.warning("No documents available in Vault for this client. Upload files in Module 3 first.")
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
        with st.spinner(f"Extracting & Reconciling line items from {target_doc['file_name']}..."):
            try:
                try:
                    file_bytes = supabase.storage.from_("client_vault").download(target_doc["file_path"])
                except Exception:
                    file_bytes = supabase.storage.from_("vault_documents").download(target_doc["file_path"])

                file_password = (
                    target_doc.get("file_password")
                    if target_doc.get("is_password_protected")
                    else None
                )

                parsed_json = parse_document_content(
                    file_bytes=file_bytes,
                    file_name=target_doc["file_name"],
                    category=target_doc["category"],
                    password=file_password,
                )

                payload = {
                    "client_id": selected_client_id,
                    "document_id": target_doc["id"],
                    "doc_type": enum_type,
                    "parsed_json": parsed_json,
                    "is_validated": False,
                }

                supabase.table("parsed_document_data").insert(payload).execute()
                st.success(f"Parsed, Reconciled & Staged data successfully for {target_doc['file_name']}!")
                st.rerun()
            except Exception as parse_err:
                st.error(f"Failed to fetch or parse file: {str(parse_err)}")

    st.divider()

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
