import sys
import streamlit as st

st.set_page_config(page_title="Test", page_icon="🛡️")
st.title("Deployment test")
st.write(f"Python {sys.version}")

errors = []

try:
    from uw_app.app_screener import screen, screen_batch, ScreenResult
    st.success("uw_app.app_screener OK")
except Exception as e:
    errors.append(f"app_screener: {e}")

try:
    from uw_app import findings_store
    st.success("findings_store OK")
except Exception as e:
    errors.append(f"findings_store: {e}")

try:
    from uw_app.ui_helpers import SCREENER_CSS
    st.success("ui_helpers OK")
except Exception as e:
    errors.append(f"ui_helpers: {e}")

if errors:
    for err in errors:
        st.error(err)
else:
    st.balloons()
    st.success("All imports passed — ready to restore full app")
