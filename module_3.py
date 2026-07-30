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
            <div class="module-subtitle">Centralized Document Management & Password-Protected Audit Repository</div>
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

    # 2. Upload Form with Password Protection Option
    st.markdown("<h3 style='color: #1e3a8a; margin-bottom: 16px;'>Upload Document</h3>", unsafe_allow_html=True)

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
            uploaded_file = st.file_uploader("Choose File*", type=["pdf", "csv", "xlsx", "xls", "png", "jpg", "jpeg", "json"])

        with col2:
            is_protected = st.checkbox("Is this file password protected?", value=False)
            file_password = st.text_input("File Password", type="password", disabled=not is_protected, help="Required if file is password protected.")

        upload_submitted = st.form_submit_button("Upload to Vault")

    if upload_submitted:
        if uploaded_file is None:
            st.error("Please select a file to upload.")
        elif is_protected and not file_password.strip():
            st.error("Please enter the document password since password protection is checked.")
        else:
            try:
                file_bytes = uploaded_file.getvalue()
                file_path = f"{selected_client_id}/{uploaded_file.name}"

                # Upload to Supabase Storage
                supabase.storage.from_("client_vault").upload(
                    path=file_path,
                    file=file_bytes,
                    file_options={"upsert": "true", "content-type": uploaded_file.type},
                )

                # Save Metadata
                vault_payload = {
                    "client_id": selected_client_id,
                    "file_name": uploaded_file.name,
                    "file_path": file_path,
                    "category": selected_category,
                    "file_size_bytes": uploaded_file.size,
                    "mime_type": uploaded_file.type,
                    "is_password_protected": is_protected,
                    "file_password": file_password.strip() if is_protected else None,
                }

                supabase.table("document_vault").insert(vault_payload).execute()
                st.success(f"Successfully uploaded {uploaded_file.name} to {selected_category}!")
                st.rerun()
            except Exception as e:
                st.error(f"Upload failed: {str(e)}")

    st.divider()

    # 3. Client Vault Repository Concise Table & Delete Actions
    st.markdown("<h3 style='color: #1e3a8a; margin-bottom: 16px;'>Client Vault Repository</h3>", unsafe_allow_html=True)

    try:
        docs_res = (
            supabase.table("document_vault")
            .select("id, file_name, file_path, category, file_size_bytes, is_password_protected, file_password, created_at")
            .eq("client_id", selected_client_id)
            .order("created_at", desc=True)
            .execute()
        )
        docs = docs_res.data

        if docs:
            # Prepare Data Frame for Concise Table Display
            table_data = []
            for doc in docs:
                size_kb = f"{round((doc['file_size_bytes'] or 0) / 1024, 2)} KB"
                date_str = doc["created_at"].split("T")[0] if "T" in doc["created_at"] else doc["created_at"]
                protected_status = "🔒 Protected" if doc.get("is_password_protected") else "🔓 None"
                pwd_display = doc.get("file_password") if doc.get("is_password_protected") and doc.get("file_password") else "-"

                table_data.append({
                    "ID": doc["id"],
                    "File Name": doc["file_name"],
                    "Category": doc["category"],
                    "Size": size_kb,
                    "Protection": protected_status,
                    "Password": pwd_display,
                    "Uploaded On": date_str,
                    "file_path": doc["file_path"],
                })

            df_display = pd.DataFrame(table_data)

            # Render Concise Table
            st.dataframe(
                df_display[["File Name", "Category", "Size", "Protection", "Password", "Uploaded On"]],
                use_container_width=True,
                hide_index=True,
            )

            # Deletion Action Selector
            st.markdown("##### **Document Management**")
            col_del_select, col_del_btn = st.columns([3, 1])

            with col_del_select:
                doc_to_delete_name = st.selectbox(
                    "Select Document to Delete",
                    options=[item["File Name"] for item in table_data],
                    key="del_selector",
                )

            with col_del_btn:
                st.write("")  # Spacing
                st.write("")  # Spacing
                if st.button("Delete Selected Document", type="secondary"):
                    target = next(item for item in table_data if item["File Name"] == doc_to_delete_name)
                    try:
                        # Storage deletion
                        supabase.storage.from_("client_vault").remove([target["file_path"]])
                        # Database deletion
                        supabase.table("document_vault").delete().eq("id", target["ID"]).execute()
                        st.success(f"Deleted {target['File Name']}")
                        st.rerun()
                    except Exception as err:
                        st.error(f"Deletion failed: {str(err)}")

            # Export Audit Trail
            st.download_button(
                label="Download Vault Directory (CSV)",
                data=df_display.to_csv(index=False),
                file_name=f"vault_repository_{selected_client_name}.csv",
                mime="text/csv",
            )
        else:
            st.info("No documents uploaded for this client yet.")
    except Exception as e:
        st.error(f"Error loading repository: {str(e)}")


if __name__ == "__main__":
    render_module_3()
