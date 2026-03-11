import streamlit as st
import pandas as pd
import json
import base64
import re
import time
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials

# The scope defines what permission we are asking for
SCOPES = ['https://www.googleapis.com/auth/gmail.send']

# 🚨 CHANGE THIS TO YOUR ACTUAL STREAMLIT LINK! Must end with a slash /
REDIRECT_URI = "https://team-emailer-dzaxgjqptyvytoenfpappnm.streamlit.app/"

st.set_page_config(page_title="Team Campaign Sender", layout="centered")
st.title("🚀 Smart Team Campaign Emailer (Web Version)")

# Create a temporary memory bank for the user's login session
if 'creds_json' not in st.session_state:
    st.session_state['creds_json'] = None

def get_flow():
    # If it's running on the cloud, use the secret vault
    if 'gcp_secret' in st.secrets:
        creds_dict = json.loads(st.secrets["gcp_secret"])
        return Flow.from_client_config(
            creds_dict,
            scopes=SCOPES,
            redirect_uri=REDIRECT_URI
        )
    # If running on your local computer, use the file
    return Flow.from_client_secrets_file(
        'credentials.json',
        scopes=SCOPES,
        redirect_uri=REDIRECT_URI
    )

# --- WEB AUTHENTICATION CATCHER ---
if 'code' in st.query_params:
    try:
        flow = get_flow()
        # Pass the saved state so the flow can verify the callback
        flow.fetch_token(
            code=st.query_params['code'],
            # Disable PKCE verifier check — not needed for web server (confidential) clients
        )
        creds = flow.credentials
        
        st.session_state['creds_json'] = creds.to_json()
        st.query_params.clear()
        st.rerun()
    except Exception as e:
        st.error(f"Authentication failed: {e}")

# --- LOGIN SCREEN ---
if not st.session_state['creds_json']:
    st.write("### 1. Account Setup")
    st.info("Because this is a web application, you will securely log in through Google.")
    
    flow = get_flow()
    # Disable PKCE (code_challenge) — it is for native/mobile apps only.
    # Web server flows with a client_secret must NOT use PKCE, otherwise
    # Google returns "invalid_grant: Missing code verifier" on the callback.
    auth_url, _ = flow.authorization_url(
        prompt='consent',
        access_type='offline',
        include_granted_scopes='false',
    )
    # Strip the code_challenge params that google-auth-oauthlib may have added
    parsed = urlparse(auth_url)
    params = parse_qs(parsed.query, keep_blank_values=True)
    params.pop('code_challenge', None)
    params.pop('code_challenge_method', None)
    clean_params = {k: v[0] for k, v in params.items()}
    auth_url = urlunparse(parsed._replace(query=urlencode(clean_params)))
    
    st.markdown(f"### [🔐 Click Here to Authorize with Google]({auth_url})")
    st.stop()

# --- MAIN APP (Only visible after login) ---
creds = Credentials.from_authorized_user_info(json.loads(st.session_state['creds_json']))
service = build('gmail', 'v1', credentials=creds)

st.success("✅ Gmail Account Successfully Connected via Web Auth!")
if st.button("Log Out"):
    st.session_state['creds_json'] = None
    st.rerun()

def clean_and_verify_emails(raw_emails):
    cleaned = []
    email_regex = re.compile(r'^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-]+$')
    
    for email in raw_emails:
        email = str(email).strip().lower()
        email = email.replace('.con', '.com').replace('.co,', '.com').replace(',', '.')
        if email.endswith('.'):
            email = email[:-1]
            
        if email_regex.match(email):
            cleaned.append(email)
            
    return list(set(cleaned))

def make_links_clickable(text):
    url_pattern = re.compile(r'(https?://\S+)')
    html_text = url_pattern.sub(r'<a href="\1">\1</a>', text)
    return html_text.replace('\n', '<br>')

st.write("### 2. Compose Message")
sender_email = st.text_input("Your Exact Authorized Gmail Address")
subject = st.text_input("Email Subject")
body = st.text_area("Email Body (URLs starting with http:// or https:// will become clickable)", height=150)

st.write("### 3. Attach Creatives")
uploaded_attachments = st.file_uploader("Upload Images or Videos (Max 25MB total)", type=["png", "jpg", "jpeg", "mp4", "gif", "pdf"], accept_multiple_files=True)

attachment_data = []
if uploaded_attachments:
    for file in uploaded_attachments:
        attachment_data.append({"name": file.name, "data": file.read()})

st.write("### 4. Upload Contacts")
uploaded_file = st.file_uploader("Upload CSV, Excel, or TXT file (Must have 'email' column)", type=["csv", "xlsx", "txt"])

if uploaded_file is not None and sender_email and subject and body:
    if uploaded_file.name.endswith('.csv'):
        df = pd.read_csv(uploaded_file)
    elif uploaded_file.name.endswith('.xlsx'):
        df = pd.read_excel(uploaded_file)
    else:
        content = uploaded_file.read().decode("utf-8")
        raw_list = [line.strip() for line in content.splitlines() if "@" in line]
        df = pd.DataFrame(raw_list, columns=['email'])

    if 'email' in df.columns:
        raw_emails = df['email'].dropna().tolist()
        final_emails = clean_and_verify_emails(raw_emails)
        
        st.success(f"🧹 Cleaned! Found **{len(final_emails)}** valid emails.")
        
        batch_size = 500
        batches = [final_emails[i:i + batch_size] for i in range(0, len(final_emails), batch_size)]
        
        st.write(f"### 5. Send Batches (Sizes of {batch_size})")
        
        try:
            for index, batch in enumerate(batches):
                with st.expander(f"📦 Batch {index + 1} ({len(batch)} emails)", expanded=True):
                    
                    if st.button(f"🚀 Send Batch {index + 1}", key=f"btn_send_{index}"):
                        message = MIMEMultipart()
                        message['to'] = sender_email 
                        message['from'] = sender_email
                        message['subject'] = subject
                        message['bcc'] = ", ".join(batch) 
                        
                        html_body = make_links_clickable(body)
                        message.attach(MIMEText(html_body, 'html'))
                        
                        for f in attachment_data:
                            part = MIMEBase('application', 'octet-stream')
                            part.set_payload(f["data"])
                            encoders.encode_base64(part)
                            part.add_header('Content-Disposition', f'attachment; filename="{f["name"]}"')
                            message.attach(part)
                        
                        raw_message = base64.urlsafe_b64encode(message.as_bytes()).decode()
                        service.users().messages().send(userId='me', body={'raw': raw_message}).execute()
                        
                        st.balloons()
                        st.success(f"Batch {index + 1} sent successfully!")
                        time.sleep(2)
                        
        except Exception as e:
            st.error(f"Error connecting to Gmail: {e}")
            
    else:
        st.error("Error: Could not find a column named 'email' in your file.")