import streamlit as st
import pandas as pd
from supabase import create_client, Client


# --- Database Initialization ---
@st.cache_resource
def init_supabase() -> Client:
    url = st.secrets["SUPABASE_URL"]
    key = st.secrets["SUPABASE_KEY"]
    return create_client(url, key)


supabase = init_supabase()


# --- Comprehensive Income Tax Statutory Logic Engine ---
def evaluate_statutory_applicability(
    res_status: str,
    directorship: bool,
    unlisted_shares: bool,
    foreign_assets: bool,
    signatory_foreign: bool,
    has_business: bool,
    is_presumptive: bool,
    needs_audit: bool,
    has_cg: bool,
    has_crypto: bool,
    agri_income: float,
    total_income_50l: bool,
    has_losses: bool,
) -> tuple[str, list[str], str]:
    """Evaluates mandatory ITR form, required heads/schedules, and filing reasons under IT Act, 1961."""
    schedules = ["Income from Other Sources (Schedule OS)"]
    reasons = []

    # Check for Business Income / Audit triggers (ITR-3 / ITR-4)
    requires_itr3 = False
    requires_itr2_or_above = False

    if needs_audit:
        requires_itr3 = True
        reasons.append("Tax Audit required u/s 44AB")
        schedules.append("Business/Profession (Schedule BP & Audit)")
    elif has_business and not is_presumptive:
        requires_itr3 = True
        reasons.append("Regular Business/Professional Income (Non-Presumptive)")
        schedules.append("Business/Profession (Schedule BP)")
    elif has_business and is_presumptive:
        schedules.append("Presumptive Business Income (Schedule BP u/s 44AD/44ADA)")

    # Capital Gains & VDA / Crypto
    if has_cg:
        requires_itr2_or_above = True
        reasons.append("Capital Gains income realized")
        schedules.append("Capital Gains (Schedule CG)")

    if has_crypto:
        requires_itr2_or_above = True
        reasons.append("Virtual Digital Assets (VDA / Crypto u/s 115BBH)")
        schedules.append("Virtual Digital Assets (Schedule VDA)")

    # Foreign Assets & Accounts
    if foreign_assets or signatory_foreign:
        requires_itr2_or_above = True
        reasons.append("Foreign Assets / Foreign Authority disclosure required")
        schedules.append("Foreign Assets & Income (Schedule FA / FSI)")

    # Residential Status & Equity / Directorship
    if res_status in ["RNOR", "NR"]:
        requires_itr2_or_above = True
        reasons.append(f"Residential Status is {res_status}")

    if directorship:
        requires_itr2_or_above = True
        reasons.append("Held Directorship in a company")

    if unlisted_shares:
        requires_itr2_or_above = True
        reasons.append("Held Unlisted Equity Shares")

    # Agricultural Income > 5,000
    if agri_income > 5000:
        requires_itr2_or_above = True
        reasons.append("Exempt Agricultural Income exceeds ₹5,000")
        schedules.append("Exempt Income (Schedule EI)")

    # Total Income > 50 Lakhs
    if total_income_50l:
        requires_itr2_or_above = True
        reasons.append("Total Income exceeds ₹50 Lakhs (Schedule AL triggered)")
        schedules.append("Assets and Liabilities (Schedule AL)")

    # Loss Carry Forward
    if has_losses:
        requires_itr2_or_above = True
        reasons.append("Brought forward or carried forward losses present")
        schedules.append("Loss Offsets (Schedule CFL / CYLA)")

    # Form Selection Decision Matrix
    if requires_itr3:
        recommended_itr = "ITR-3"
        reasons_text = "Mandatory ITR-3 due to: " + "; ".join(reasons)
    elif has_business and is_presumptive and not requires_itr2_or_above:
        recommended_itr = "ITR-4 (Sugam)"
        reasons_text = "Eligible for Presumptive Filing u/s 44AD/44ADA (ITR-4)"
    elif has_business and is_presumptive and requires_itr2_or_above:
        recommended_itr = "ITR-3"
        reasons_text = "Presumptive business, but forced to ITR-3 due to: " + "; ".join(reasons)
    elif requires_itr2_or_above:
        recommended_itr = "ITR-2"
        reasons_text = "Disqualified from ITR-1 (Sahaj) due to: " + "; ".join(reasons)
    else:
        recommended_itr = "ITR-1 (Sahaj)"
        reasons_text = "Eligible for simplified ITR-1 (Subject to income & salary limits)"

    return recommended_itr, list(set(schedules)), reasons_text


# --- Module UI Renderer ---
def render_module_2():
    st.markdown(
        """
        <div class="module-header-container">
            <div class="module-title">Module 2: Income Tax Statutory Questionnaire</div>
            <div class="module-subtitle">Rule-based Statutory Evaluation Engine (Income Tax Act, 1961)</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # Active Client Selector Block
    try:
        clients_res = supabase.table("client_profiles").select("id, full_name_pan, pan").execute()
        clients = clients_res.data
    except Exception as e:
        st.error(f"Error fetching clients: {str(e)}")
        return

    if not clients:
        st.info("No client profiles found. Please onboard clients in Module 1 first.")
        return

    client_dict = {f"{c['full_name_pan']} ({c['pan']})": c['id'] for c in clients}
    
    st.markdown("<h4 style='color: #1d4ed8; margin-bottom: 8px;'>Target Client Context</h4>", unsafe_allow_html=True)
    selected_client_name = st.selectbox("Select Active Client*", list(client_dict.keys()))
    selected_client_id = client_dict[selected_client_name]

    # Fetch Existing Client Record
    existing_data = None
    try:
        q_res = (
            supabase.table("statutory_questionnaire")
            .select("*")
            .eq("client_id", selected_client_id)
            .execute()
        )
        if q_res.data:
            existing_data = q_res.data[0]
    except Exception:
        pass

    # SECTION 1: Statutory Input Card
    st.markdown("---")
    st.markdown("<h3 style='color: #1e3a8a; margin-bottom: 16px;'>1. Statutory Criteria Questionnaire</h3>", unsafe_allow_html=True)

    with st.form("statutory_questionnaire_form"):
        # Group A: Residential & Directorship Criteria
        st.markdown("##### **A. Residential & Governance Status**")
        res_opts = ["ROR", "RNOR", "NR"]
        default_res = res_opts.index(existing_data["residential_status"]) if existing_data else 0

        res_status = st.radio(
            "Residential Status u/s 6*",
            res_opts,
            index=default_res,
            horizontal=True,
            help="ROR: Resident & Ordinarily Resident | RNOR: Resident Not Ordinarily Resident | NR: Non-Resident",
        )

        col1, col2 = st.columns(2)
        with col1:
            directorship = st.checkbox(
                "Held Directorship in Indian/Foreign Company?",
                value=existing_data["is_directorship_held"] if existing_data else False,
            )
            unlisted = st.checkbox(
                "Held Unlisted Equity Shares during FY?",
                value=existing_data["is_unlisted_shares_held"] if existing_data else False,
            )
        with col2:
            fa_assets = st.checkbox(
                "Holds Foreign Assets / Offshore Accounts (Schedule FA)?",
                value=existing_data["has_foreign_assets_fa"] if existing_data else False,
            )
            signatory = st.checkbox(
                "Signatory Authority in any Foreign Account?",
                value=existing_data["is_signatory_foreign_account"] if existing_data else False,
            )

        st.markdown("---")
        # Group B: Income Heads, Business & Asset Criteria
        st.markdown("##### **B. Income Streams & Asset Thresholds**")

        col3, col4 = st.columns(2)
        with col3:
            has_business = st.checkbox(
                "Has Business or Professional Income?",
                value=existing_data["has_business_profession_income"] if existing_data else False,
            )
            is_presumptive = st.checkbox(
                "Filing under Presumptive Taxation (u/s 44AD / 44ADA)?",
                value=existing_data["is_presumptive_44ad_44ada"] if existing_data else False,
            )
            needs_audit = st.checkbox(
                "Tax Audit Mandatory u/s 44AB?",
                value=existing_data["is_tax_audit_required_44ab"] if existing_data else False,
            )
            has_cg = st.checkbox(
                "Realized Capital Gains (Stocks/Property/MFs)?",
                value=existing_data["has_capital_gains"] if existing_data else False,
            )

        with col4:
            has_crypto = st.checkbox(
                "Income from Crypto / Virtual Digital Assets (VDA)?",
                value=existing_data["has_crypto_vda_income"] if existing_data else False,
            )
            total_50l = st.checkbox(
                "Total Income Exceeds ₹50 Lakhs (Triggers Schedule AL)?",
                value=existing_data["total_income_exceeds_50l"] if existing_data else False,
            )
            has_losses = st.checkbox(
                "Has Brought Forward or Carry Forward Losses?",
                value=existing_data["has_brought_forward_losses"] if existing_data else False,
            )
            agri_inc = st.number_input(
                "Exempt Agricultural Income (₹)",
                min_value=0.0,
                value=float(existing_data["agricultural_income"]) if existing_data else 0.0,
                step=1000.0,
            )

        submitted = st.form_submit_button("Run Statutory Logic & Save Assessment")

    # Processing Input & Saving to Supabase
    if submitted:
        rec_itr, mandatory_schedules, reasoning = evaluate_statutory_applicability(
            res_status,
            directorship,
            unlisted,
            fa_assets,
            signatory,
            has_business,
            is_presumptive,
            needs_audit,
            has_cg,
            has_crypto,
            agri_inc,
            total_50l,
            has_losses,
        )

        payload = {
            "client_id": selected_client_id,
            "residential_status": res_status,
            "is_directorship_held": directorship,
            "is_unlisted_shares_held": unlisted,
            "has_foreign_assets_fa": fa_assets,
            "is_signatory_foreign_account": signatory,
            "has_business_profession_income": has_business,
            "is_presumptive_44ad_44ada": is_presumptive,
            "is_tax_audit_required_44ab": needs_audit,
            "has_capital_gains": has_cg,
            "has_crypto_vda_income": has_crypto,
            "agricultural_income": agri_inc,
            "total_income_exceeds_50l": total_50l,
            "has_brought_forward_losses": has_losses,
            "recommended_itr_form": rec_itr,
            "mandatory_heads": mandatory_schedules,
            "filing_applicability_reason": reasoning,
        }

        try:
            supabase.table("statutory_questionnaire").upsert(
                payload, on_conflict="client_id"
            ).execute()
            st.success("Statutory Assessment saved and logic evaluated successfully!")
        except Exception as e:
            st.error(f"Database Error: {str(e)}")

    # SECTION 2: Automated Evaluation Report Output
    st.markdown("---")
    st.markdown("<h3 style='color: #1e3a8a; margin-bottom: 16px;'>2. Automated Statutory Evaluation Report</h3>", unsafe_allow_html=True)

    try:
        latest_res = (
            supabase.table("statutory_questionnaire")
            .select("*")
            .eq("client_id", selected_client_id)
            .execute()
        )
        if latest_res.data:
            eval_data = latest_res.data[0]

            col_a, col_b = st.columns([1, 1])

            with col_a:
                st.markdown(
                    f"""
                    <div style="background-color: #eff6ff; border: 1px solid #bfdbfe; border-radius: 10px; padding: 18px; margin-bottom: 16px;">
                        <span style="color: #1d4ed8; font-weight: 700; font-size: 13px; text-transform: uppercase;">Recommended ITR Form</span>
                        <h2 style="color: #1e3a8a; margin: 4px 0 0 0; font-weight: 800;">{eval_data['recommended_itr_form']}</h2>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )
                st.markdown("**Statutory Evaluation Audit Trail:**")
                st.info(eval_data["filing_applicability_reason"])

            with col_b:
                st.markdown("**Mandatory ITR Schedules & Disclosures:**")
                sched_df = pd.DataFrame(
                    {"Required Schedule / Head": eval_data["mandatory_heads"]}
                )
                st.dataframe(sched_df, use_container_width=True, hide_index=True)

            # CSV Export
            eval_export_df = pd.DataFrame([eval_data])
            st.download_button(
                label="Download Full Statutory Report (CSV)",
                data=eval_export_export_df := eval_export_df.to_csv(index=False),
                file_name=f"statutory_report_{selected_client_name}.csv",
                mime="text/csv",
            )
        else:
            st.info("No evaluation record found for this client. Complete and submit the questionnaire above.")
    except Exception as e:
        st.error(f"Error fetching evaluation report: {str(e)}")


if __name__ == "__main__":
    render_module_2()
