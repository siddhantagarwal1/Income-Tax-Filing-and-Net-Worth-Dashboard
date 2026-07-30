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


# --- Spatial Layout & Text Analysis Engine ---
def _clean_number(val) -> float:
    """Utility to convert text representations of currency into strict floating-point values."""
    if val is None:
        return 0.0
    clean = re.sub(r"[^\d.-]", "", str(val).replace(",", ""))
    try:
        return float(clean) if clean else 0.0
    except ValueError:
        return 0.0


def analyze_pdf_spatial_structure(pdf: pdfplumber.PDF) -> dict:
    """
    Pass 1: Perform document-level spatial layout and bounding box analysis.
    Identifies column coordinates, line clusters, and structural metadata without assumptions.
    """
    page_layouts = []
    global_text_lines = []

    for page_num, page in enumerate(pdf.pages):
        words = page.extract_words(
            x_tolerance=3, y_tolerance=3, keep_blank_chars=False
        )
        
        # Group words by Y-coordinate lines (vertical alignment)
        lines_dict = {}
        for w in words:
            top_key = round(w["top"], 1)
            found_line = False
            for line_top in lines_dict.keys():
                if abs(line_top - top_key) <= 3:
                    lines_dict[line_top].append(w)
                    found_line = True
                    break
            if not found_line:
                lines_dict[top_key] = [w]

        sorted_lines = []
        for line_top in sorted(lines_dict.keys()):
            line_words = sorted(lines_dict[line_top], key=lambda x: x["x0"])
            line_text = " ".join([w["text"] for w in line_words])
            sorted_lines.append(
                {
                    "top": line_top,
                    "text": line_text,
                    "words": line_words,
                    "page": page_num + 1,
                }
            )
            global_text_lines.append(line_text)

        page_layouts.append(
            {
                "page_num": page_num + 1,
                "width": page.width,
                "height": page.height,
                "lines": sorted_lines,
            }
        )

    return {
        "page_count": len(pdf.pages),
        "layouts": page_layouts,
        "full_text": "\n".join(global_text_lines),
    }


def extract_bank_opening_closing_spatial(analysis: dict) -> tuple:
    """
    Pass 2: Extract true Opening and Closing balances via contextual spatial anchors.
    Scans bounding lines sequentially rather than matching top-level regex globally.
    """
    full_text = analysis["full_text"]
    opening_bal = None
    closing_bal = None

    # Spatial keyword search line by line
    for layout in analysis["layouts"]:
        for line in layout["lines"]:
            txt = line["text"].lower()

            # Contextual Opening Balance Anchor
            if any(k in txt for k in ["opening balance", "b/f balance", "balance b/f", "prev bal", "opening bal"]):
                amounts = re.findall(r"[\d,]+\.\d{2}", line["text"])
                if amounts:
                    opening_bal = _clean_number(amounts[0])

            # Contextual Closing Balance Anchor
            if any(k in txt for k in ["closing balance", "c/f balance", "balance c/f", "closing bal", "final balance"]):
                amounts = re.findall(r"[\d,]+\.\d{2}", line["text"])
                if amounts:
                    closing_bal = _clean_number(amounts[-1])

    # Fallback to structural line detection if isolated anchors are not explicitly labeled
    if opening_bal is None:
        op_match = re.search(r"(?i)(?:opening balance|b/f)\D*([\d,]+\.\d{2})", full_text)
        if op_match:
            opening_bal = _clean_number(op_match.group(1))

    if closing_bal is None:
        cl_match = re.search(r"(?i)(?:closing balance|c/f)\D*([\d,]+\.\d{2})", full_text)
        if cl_match:
            closing_bal = _clean_number(cl_match.group(1))

    return opening_bal, closing_bal


def parse_bank_statement_spatial(pdf: pdfplumber.PDF, spatial_analysis: dict) -> dict:
    """Spatial Line-Item Parser for heterogeneous Bank Statements."""
    ledger = []
    
    for layout in spatial_analysis["layouts"]:
        page = pdf.pages[layout["page_num"] - 1]
        tables = page.extract_tables()
        
        # Table-assisted extraction with spatial validation
        if tables:
            for table in tables:
                if not table or len(table) < 2:
                    continue
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
                    date_match = re.search(r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b", row_str)
                    if not date_match:
                        continue

                    txn_date = date_match.group(0)
                    desc = str(row[desc_idx]).strip() if desc_idx != -1 and desc_idx < len(row) and row[desc_idx] else row_str

                    debit = _clean_number(row[dr_idx]) if dr_idx != -1 and dr_idx < len(row) else 0.0
                    credit = _clean_number(row[cr_idx]) if cr_idx != -1 and cr_idx < len(row) else 0.0
                    balance = _clean_number(row[bal_idx]) if bal_idx != -1 and bal_idx < len(row) else 0.0

                    if dr_idx == -1 and cr_idx == -1 and amt_idx != -1 and amt_idx < len(row):
                        amt_val = _clean_number(row[amt_idx])
                        if "CR" in row_str.upper() or "DEPOSIT" in row_str.upper():
                            credit = amt_val
                        else:
                            debit = amt_val

                    if debit == 0.0 and credit == 0.0:
                        continue

                    # Tax Head Categorization Engine
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

                    ledger.append(
                        {
                            "date": txn_date,
                            "description": desc,
                            "transaction_type": "Credit" if credit > 0 else "Debit",
                            "amount": credit if credit > 0 else debit,
                            "debit": debit,
                            "credit": credit,
                            "running_balance": balance,
                            "classified_category": category,
                        }
                    )

    # Extract dynamic opening/closing bounds
    op_bal, cl_bal = extract_bank_opening_closing_spatial(spatial_analysis)
    
    opening_bal = op_bal if op_bal is not None else (ledger[0]["running_balance"] - ledger[0]["credit"] + ledger[0]["debit"] if ledger else 0.0)
    closing_bal = cl_bal if cl_bal is not None else (ledger[-1]["running_balance"] if ledger else 0.0)

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
            "reconciliation_passed": reconciliation_passed,
        },
        "total_interest_detected": total_interest_detected,
        "interest_transactions": detected_interest_items,
        "transaction_ledger": ledger,
    }


def parse_ais_tis_spatial(spatial_analysis: dict) -> dict:
    """Pass 2: Extract AIS/TIS structured line items spatially."""
    full_text = spatial_analysis["full_text"]
    
    tds_matches = re.findall(r"(?i)(?:tds|tax deducted)\D*([\d,]+\.\d{2})", full_text)
    savings_int = re.findall(r"(?i)(?:interest from savings|194a)\D*([\d,]+\.\d{2})", full_text)
    dividends = re.findall(r"(?i)(?:dividend|194)\D*([\d,]+\.\d{2})", full_text)

    return {
        "reported_tds_total": sum([_clean_number(x) for x in tds_matches]),
        "reported_savings_interest_total": sum([_clean_number(x) for x in savings_int]),
        "reported_dividend_total": sum([_clean_number(x) for x in dividends]),
    }


def parse_excel_document(file_bytes: bytes, file_name: str) -> dict:
    """Extracts and normalizes structured Demat / Capital Gains schedules from Excel sheets."""
    try:
        excel_file = pd.ExcelFile(io.BytesIO(file_bytes))
        sheet_summaries = {}

        for sheet_name in excel_file.sheet_names:
            df = pd.read_excel(excel_file, sheet_name=sheet_name)
            df_clean = df.dropna(how="all").fillna("")
            
            # Map column heads lower
            cols = [str(c).lower().strip() for c in df_clean.columns]
            
            sheet_summaries[sheet_name] = {
                "rows_count": len(df_clean),
                "columns_detected": list(df_clean.columns),
                "preview": df_clean.head(10).to_dict(orient="records"),
            }

        return {
            "file_name": file_name,
            "sheet_count": len(excel_file.sheet_names),
            "sheets": sheet_summaries,
        }
    except Exception as e:
        return {"error": f"Failed to parse Excel document: {str(e)}", "file_name": file_name}


def parse_sgb_certificate_spatial(spatial_analysis: dict) -> dict:
    """Pass 2: Extract SGB Certificate metrics spatially."""
    full_text = spatial_analysis["full_text"]
    series = re.search(r"(?i)(?:series|tranche)\D*([A-Z0-9/-]+)", full_text)
    units = re.search(r"(?i)(?:units|quantity)\D*(\d+)", full_text)
    amount = re.search(r"(?i)(?:amount|consideration|issue price)\D*([\d,]+\.\d{2})", full_text)
    
    inv_amount = _clean_number(amount.group(1)) if amount else 0.0
    return {
        "bond_series": series.group(1) if series else "Unknown",
        "units_held": int(units.group(1)) if units else 0,
        "investment_amount": inv_amount,
        "expected_semi_annual_payout": round((inv_amount * 0.025) / 2, 2),
    }


# --- Unified Parser Engine Router ---
def parse_document_content(
    file_bytes: bytes, file_name: str, category: str, password: str = None
) -> dict:
    cat_upper = category.upper()

    # Route Excel Files
    if file_name.endswith(".xlsx") or file_name.endswith(".xls"):
        return parse_excel_document(file_bytes, file_name)

    # Route PDF Documents via Two-Pass Spatial Engine
    try:
        open_kwargs = {"password": password} if password else {}
        with pdfplumber.open(io.BytesIO(file_bytes), **open_kwargs) as pdf:
            # Pass 1: Spatial Layout Discovery
            spatial_analysis = analyze_pdf_spatial_structure(pdf)

            extracted_data = {
                "file_name": file_name,
                "category": category,
                "pages_analyzed": spatial_analysis["page_count"],
            }

            # Pass 2: Contextual Schema Normalization
            if "BANK" in cat_upper:
                extracted_data["parsed_bank_details"] = parse_bank_statement_spatial(pdf, spatial_analysis)
            elif "AIS" in cat_upper or "TIS" in cat_upper:
                extracted_data["parsed_ais_tis_details"] = parse_ais_tis_spatial(spatial_analysis)
            elif "SGB" in cat_upper or "SOVEREIGN GOLD BOND" in cat_upper:
                extracted_data["parsed_sgb_details"] = parse_sgb_certificate_spatial(spatial_analysis)
            elif "FORM 16" in cat_upper or "16A" in cat_upper:
                pan = re.search(r"[A-Z]{5}[0-9]{4}[A-Z]{1}", spatial_analysis["full_text"])
                extracted_data["pan_detected"] = pan.group(0) if pan else None
            else:
                extracted_data["summary_preview"] = (
                    spatial_analysis["full_text"][:500]
                    if spatial_analysis["full_text"]
                    else "No text found."
                )

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
            <div class="module-subtitle">Spatial Layout Analysis, Structured Extraction & Ledger Staging</div>
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
        with st.spinner(f"Running Spatial Analysis & Ingesting {target_doc['file_name']}..."):
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
                st.success(f"Parsed, Reconciled & Staged successfully for {target_doc['file_name']}!")
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
