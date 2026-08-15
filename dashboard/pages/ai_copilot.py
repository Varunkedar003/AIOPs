import streamlit as st

from dashboard.chat import render_chat


def render_ai_copilot() -> None:
    """AI Operations Copilot: chat with the AI investigation assistant.

    Root Cause and Suggested Actions below are read from the most recent completed
    investigation (Claude's structured final report) - there's no placeholder text here;
    both sections simply have nothing to show until a question has been answered.
    """
    st.markdown("## AI Operations Copilot")
    st.caption("Chat with the AI investigation assistant about your infrastructure.")

    render_chat()

    investigation = st.session_state.get("agent_state", {}).get("investigation") or {}
    final_report = investigation.get("final_report") or {}

    with st.expander("Root Cause"):
        root_cause = (final_report.get("root_cause") or "").strip()
        if root_cause and root_cause.lower() not in ("not applicable", "n/a"):
            st.markdown(f"**{root_cause}**")
            if final_report.get("root_cause_explanation"):
                st.markdown(final_report["root_cause_explanation"])
            confidence = final_report.get("root_cause_confidence")
            if confidence is not None:
                st.caption(f"Confidence: {confidence:.0%}")
        else:
            st.markdown("_No root cause identified yet - ask an investigation question above._")

    with st.expander("Suggested Actions"):
        resolution_plan = final_report.get("resolution_plan") or []
        verification_steps = final_report.get("verification_steps") or []
        if not resolution_plan and not verification_steps:
            st.markdown("_No suggested actions yet - ask an investigation question above._")
        else:
            if resolution_plan:
                st.markdown("**Resolution steps:**")
                for i, step in enumerate(resolution_plan, 1):
                    st.markdown(f"{i}. {step.get('step')}")
            if verification_steps:
                st.markdown("**Verification steps:**")
                for i, step in enumerate(verification_steps, 1):
                    st.markdown(f"{i}. {step}")
