import streamlit as st
import pandas as pd
from supabase import create_client, Client


# --- Supabase Initialization ---
@st.cache_resource
def init_supabase() -> Client:
    url = st.secrets["SUPABASE_URL"]
    key = st.secrets["SUPABASE_KEY"]
    return create_client(url, key)


supabase = init_supabase()


# --- Module 3 Renderer ---
def render_module_3():
    st.markdown(
        """
        <div class="module-header-container">
            <div class="module-title">Module 3: Document Vault & Repository</div>
            <div class="module-subtitle">Centralized Document Management & Audit Repository</div>
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
        st.info("No client profiles found. Please onboard clients in Module 1 first.")
        return

    client_dict = {f"{c['full_name_pan']} ({c['pan']})": c["id"] for c in clients}

    st.markdown("<h4 style='color: #1d4ed8; margin-bottom: 8px;'>Target Client Context</h4>", unsafe_allow_html=True)
    selected_client_name = st.selectbox("Select Active Client*", list(client_dict.keys()))
    selected_client_id = client_dict[selected_client_name]

    st.divider()

    # 2. Upload Compliance & Financial Documents
    st.markdown("<h3 style='color: #1e3a8a; margin-bottom: 16px;'>Upload Documents</h3>", unsafe_allow_html=True)

    category_options = [
        "Bank Statements",
        "AIS/TIS Documents",
        "Previous Year ITRs",
        "Sovereign Gold Bond (SGB) Certificates",
        "Demat Holdings Reports",
        "Broker Capital Gains Statements",
        "Form 26AS",
        "Form 16/16A",
        "Miscellaneous Documents",
    ]

    with st.form("document_upload_form", clear_on_submit=True):
        col1, col2 = st.columns(2)

        with col1:
            selected_category = st.selectbox("Document Category*", category_options)

        with col2:
            uploaded_file = st.file_uploader("Choose File*", type=["pdf", "csv", "xlsx", "xls", "png", "jpg", "jpeg", "json"])

        upload_submitted = st.form_submit_button("Upload to Vault")

    if upload_submitted:
        if uploaded_file is None:
            st.error("Please select a file to upload.")
        else:
            try:
                # File path in Supabase Storage: client_id/filename
                file_bytes = uploaded_file.getvalue()
                file_path = f"{selected_client_id}/{uploaded_file.name}"

                # Upload file to Storage Bucket
                supabase.storage.from_("client_vault").upload(
                    path=file_path,
                    file=file_bytes,
                    file_options={"upsert": "true", "content-type": uploaded_file.type},
                )

                # Insert record into database table
                vault_payload = {
                    "client_id": selected_client_id,
                    "file_name": uploaded_file.name,
                    "file_path": file_path,
                    "category": selected_category,
                    "file_size_bytes": uploaded_file.size,
                    "mime_type": uploaded_file.type,
                }

                supabase.table("document_vault").insert(vault_payload).execute()
                st.success(f"Successfully uploaded {uploaded_file.name} to {selected_category}!")
            except Exception as e:
                st.error(f"Upload failed: {str(e)}")

    st.divider()

    # 3. Client Vault Repository Table & Actions
    st.markdown("<h3 style='color: #1e3a8a; margin-bottom: 16px;'>Client Vault Repository</h3>", unsafe_allow_html=True)

    try:
        docs_res = (
            supabase.table("document_vault")
            .select("id, file_name, file_path, category, file_size_bytes, created_at")
            .eq("client_id", selected_client_id)
            .order("created_at", desc=True)
            .execute()
        )
        docs = docs_res.data

        if docs:
            # Display summary list with inline deletion
            for doc in docs:
                col_info, col_del = st.columns([5, 1])

                file_size_kb = round((doc["file_size_bytes"] or 0) / 1024, 2)
                upload_date = doc["created_at"].split("T")[0] if "T" in doc["created_at"] else doc["created_at"]

                with col_info:
                    st.markdown(
                        f"""
                        <div style="background-color: #1c2541; padding: 12px 16px; border-radius: 8px; border: 1px solid #3a506b; margin-bottom: 8px;">
                            <strong style="color: #93c5fd;">{doc['file_name']}</strong> 
                            <span style="color: #9ca3af; font-size: 13px;"> | {doc['category']} | {file_size_kb} KB | {upload_date}</span>
                        </div>
                        """,
                        unsafe_allow_html=True,
                    )

                with col_del:
                    if st.button("Delete", key=f"del_{doc['id']}"):
                        try:
                            # Delete from storage
                            supabase.storage.from_("client_vault").remove([doc["file_path"]])
                            # Delete from database
                            supabase.table("document_vault").delete().eq("id", doc["id"]).execute()
                            st.success(f"Deleted {doc['file_name']}")
                            st.rerun()
                        except Exception as delete_err:
                            st.error(f"Delete failed: {str(delete_err)}")

            # Repository Export Option
            st.markdown("---")
            df_export = pd.DataFrame(docs)
            df_export["client_name"] = selected_client_name
            export_cols = ["client_name", "file_name", "category", "file_size_bytes", "created_at"]
            
            st.download_button(
                label="Download Vault Registry (CSV)",
                data=df_export[export_cols].to_csv(index=False),
                file_name=f"vault_registry_{selected_client_name}.csv",
                mime="text/csv",
            )
        else:
            st.info("No documents uploaded for this client yet.")
    except Exception as e:
        st.error(f"Error loading repository: {str(e)}")


if __name__ == "__main__":
    render_module_3()
