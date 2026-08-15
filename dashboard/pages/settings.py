import streamlit as st

from config import Config


def render_settings() -> None:
    st.markdown("## Settings")
    st.caption("Application configuration (read-only in Phase 1).")

    st.markdown("#### Application")
    st.markdown(f"- **Name:** {Config.APP_NAME}")
    st.markdown(f"- **Environment:** {Config.APP_ENV}")
    st.markdown(f"- **Debug Mode:** {Config.APP_DEBUG}")

    st.markdown("#### AI / LLM")
    st.markdown(f"- **Ollama Base URL:** {Config.OLLAMA_BASE_URL}")
    st.markdown(f"- **Ollama Model:** {Config.OLLAMA_MODEL}")
    st.markdown(f"- **Anthropic Model:** {Config.ANTHROPIC_MODEL}")
    st.markdown(f"- **Anthropic API Key Configured:** {'Yes' if Config.ANTHROPIC_API_KEY else 'No'}")

    st.markdown("#### Azure")
    st.markdown(f"- **Subscription ID Configured:** {'Yes' if Config.AZURE_SUBSCRIPTION_ID else 'No'}")
    st.markdown(f"- **Tenant ID Configured:** {'Yes' if Config.AZURE_TENANT_ID else 'No'}")
    st.markdown(f"- **Resource Group:** {Config.AZURE_RESOURCE_GROUP or 'Not set'}")

    st.markdown("#### GitLab")
    st.markdown(f"- **GitLab URL:** {Config.GITLAB_URL}")
    st.markdown(f"- **Token Configured:** {'Yes' if Config.GITLAB_TOKEN else 'No'}")
