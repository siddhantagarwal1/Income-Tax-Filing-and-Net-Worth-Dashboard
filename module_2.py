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


# --- Core Statutory Logic Engine (Income Tax Act, 1961) ---
def evaluate_statutory_applicability(
    res_status: str,
    directorship: bool,
    unlisted_shares: bool,
    foreign_assets: bool,
    signatory_foreign: bool,
) -> tuple[str, list[str], str]:
    """Evaluates mandatory ITR form, required heads, and filing reasons based on tax provisions."""
    heads = ["Income from Other Sources (Schedule OS)"]
    reasons = []

    # Check for ITR-2 / ITR-3 applicability drivers
    requires_itr2_or_above = False

    if res_status in ["RNOR", "NR"]:
        requires_itr2_or_above = True
        reasons.append(f"Residential Status is {res_status}")

    if directorship:
        requires_itr2_or_above = True
        reasons.append("Held directorship in a company during the FY")

    if unlisted_shares:
        requires_itr2_or_above = True
        reasons.append("Held unlisted equity shares at any time during the FY")

    if foreign_assets or signatory_foreign:
        requires_itr2_or_above = True
        reasons.append("Mandatory Foreign Asset disclosure (Schedule FA)")
        heads.append("Foreign Assets & Foreign Source Income (Schedule FA/FSI)")

    if requires_itr2_or_above:
        recommended_itr = "ITR-2 / ITR-3"
        reasons_text = "ITR-1 (Sahaj) barred due to: " + "; ".join(reasons)
    else:
        recommended_itr = "ITR-1"
        reasons_text = "Eligible for simplified ITR-1 (Subject to income limits)"

    return recommended_itr, heads, reasons_text


# --- UI Renderer Function for Module 2 ---
def render_module_2():
    st.markdown(
        """
        <div class="module-header-container">
            <div class="module-title">Module 2: Income Tax Statutory Questionnaire</div>
            <div class="module-subtitle">Rule-based Evaluation Engine for Tax Applicability & ITR Form Selection</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # 1. Active Client Selector
    try:
        clients_res = supabase.table("client_profiles").select("id, full_name_pan, pan").execute()
        clients = clients_res.data
    except Exception as e:
        st.error(f"Error fetching clients: {str(e)}")
        return

    if not clients:
        st.warning("No client profiles found. Please complete Module 1 onboarding first.")
        return

    client_dict = {f"{c['full_name_pan']} ({c['pan']})": c['id'] for c in clients}
    selected_client_name = st.selectbox("Select Active Client*", list(client_dict.keys()))
    selected_client_id = client_dict[selected_client_name]

    # Load existing client questionnaire data if available
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

    # 2. Statutory Questionnaire Form
    with st.form("statutory_questionnaire_form"):
        st.markdown("### Statutory Criteria Evaluation")

        res_status_opts = ["ROR", "RNOR", "NR"]
        default_res_idx = (
            res_status_opts.index(existing_data["residential_status"]) if existing_data else 0
        )

        residential_status = st.radio(
            "Residential Status u/s 6*",
            res_status_opts,
            index=default_res_idx,
            help="ROR: Resident & Ordinarily Resident | RNOR: Resident but Not Ordinarily Resident | NR: Non-Resident",
            horizontal=True,
        )

        col1, col2 = st.columns(2)
        with col1:
            is_directorship = st.checkbox(
                "Held Directorship in Indian/Foreign Company during the FY?",
                value=existing_data["is_directorship_held"] if existing_data else False,
            )
            is_unlisted = st.checkbox(
                "Held Unlisted Equity Shares at any time during the FY?",
                value=existing_data["is_unlisted_shares_held"] if existing_data else False,
            )

        with col2:
            has_fa = st.checkbox(
                "Hold Foreign Assets / Accounts / Financial Interests (Schedule FA)?",
                value=existing_data["has_foreign_assets_fa"] if existing_data else False,
            )
            is_signatory = st.checkbox(
                "Have Signatory Authority in any foreign account?",
                value=existing_data["is_signatory_foreign_account"] if existing_data else False,
            )

        submitted = st.form_submit_button("Run Logic & Save Assessment")

    # 3. Processing & Persistence
    if submitted:
        rec_itr, mandatory_heads, reason_text = evaluate_statutory_applicability(
            residential_status, is_directorship, is_unlisted, has_fa, is_signatory
        )

        payload = {
            "client_id": selected_client_id,
            "residential_status": residential_status,
            "is_directorship_held": is_directorship,
            "is_unlisted_shares_held": is_unlisted,
            "has_foreign_assets_fa": has_fa,
            "is_signatory_foreign_account": is_signatory,
            "recommended_itr_form": rec_itr,
            "mandatory_heads": mandatory_heads,
            "filing_applicability_reason": reason_text,
        }

        try:
            supabase.table("statutory_questionnaire").upsert(
                payload, on_conflict="client_id"
            ).execute()
            st.success("Statutory Assessment evaluated and saved successfully!")
        except Exception as e:
            st.error(f"Database Error: {str(e)}")

    # 4. Evaluation Engine Output Cards & Export
    try:
        latest_res = (
            supabase.table("statutory_questionnaire")
            .select("*")
            .eq("client_id", selected_client_id)
            .execute()
        )
        if latest_res.data:
            eval_data = latest_res.data[0]

            st.divider()
            st.subheader("Automated Statutory Evaluation Report")

            col_a, col_b = st.columns(2)
            with col_a:
                st.info(f"**Recommended ITR Form:** {eval_data['recommended_itr_form']}")
                st.write(f"**Filing Applicability Note:** {eval_data['filing_applicability_reason']}")

            with col_b:
                st.markdown("**Mandatory Schedules / Income Heads:**")
                for h in eval_data["mandatory_heads"]:
                    st.markdown(f"- {h}")

            # Export Assessment
            eval_df = pd.DataFrame([eval_data])
            st.download_button(
                label="Download Assessment Logic Report (CSV)",
                data=eval_df.to_csv(index=False),
                file_name=f"statutory_assessment_{selected_client_name}.csv",
                mime="text/csv",
            )
    except Exception as e:
        st.error(f"Error loading report: {str(e)}")


if __name__ == "__main__":
    render_module_2()
