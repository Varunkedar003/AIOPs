import os
from dotenv import load_dotenv

try:
    # On Streamlit Community Cloud, secrets come from the platform's Secrets
    # manager (st.secrets), not a .env file. Bridge them into os.environ so the
    # os.getenv() calls below work unchanged. Local dev with a real .env is
    # unaffected: load_dotenv() below never overwrites a var that's already set,
    # and st.secrets is empty/absent when no .streamlit/secrets.toml exists.
    import streamlit as st
    for _key, _value in st.secrets.items():
        os.environ.setdefault(_key, str(_value))
except Exception:
    pass

load_dotenv()

class Config:
    """Application configuration"""
    
    # Application
    APP_NAME = os.getenv("APP_NAME", "AIOps Commander")
    APP_ENV = os.getenv("APP_ENV", "development")
    APP_DEBUG = os.getenv("APP_DEBUG", "true").lower() == "true"
    
    # Ollama
    OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5-coder:3b")
    # Same model, CrewAI's "ollama/<model>" naming convention - derived, not a separate setting
    CREWAI_OLLAMA_MODEL = f"ollama/{OLLAMA_MODEL}"
    
    # Anthropic Claude
    ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
    ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5")
    
    # Azure
    AZURE_SUBSCRIPTION_ID = os.getenv("AZURE_SUBSCRIPTION_ID", "")
    AZURE_TENANT_ID = os.getenv("AZURE_TENANT_ID", "")
    AZURE_CLIENT_ID = os.getenv("AZURE_CLIENT_ID", "")
    AZURE_CLIENT_SECRET = os.getenv("AZURE_CLIENT_SECRET", "")
    AZURE_RESOURCE_GROUP = os.getenv("AZURE_RESOURCE_GROUP", "")
    
    # AKS
    AKS_CLUSTER_NAME = os.getenv("AKS_CLUSTER_NAME", "")
    AKS_RESOURCE_GROUP = os.getenv("AKS_RESOURCE_GROUP", "")
    
    # GitLab
    GITLAB_URL = os.getenv("GITLAB_URL", "https://gitlab.com")
    GITLAB_TOKEN = os.getenv("GITLAB_TOKEN", "")
    GITLAB_PROJECT_ID = os.getenv("GITLAB_PROJECT_ID", "")
    
    # Azure Monitor
    AZURE_MONITOR_WORKSPACE_ID = os.getenv("AZURE_MONITOR_WORKSPACE_ID", "")
    AZURE_MONITOR_SHARED_KEY = os.getenv("AZURE_MONITOR_SHARED_KEY", "")
    
    # Application Insights
    APPINSIGHTS_CONNECTION_STRING = os.getenv("APPINSIGHTS_CONNECTION_STRING", "")
    
    # Log Analytics
    LOG_ANALYTICS_WORKSPACE_ID = os.getenv("LOG_ANALYTICS_WORKSPACE_ID", "")
    LOG_ANALYTICS_QUERY_KEY = os.getenv("LOG_ANALYTICS_QUERY_KEY", "")
