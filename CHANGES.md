# Changes: Streamlit Community Cloud Deployment Prep

Local git repo initialized and committed. No application logic changed, no Azure resources created, nothing pushed anywhere.

## Files added

- **`.gitignore`** - excludes `venv/`, `.env`, `__pycache__/`, `GeneratedDocs/`, stray `*.tmp.*` editor artifacts already present in `dashboard/components/g6_explorer/`, and `.claude/`.
- **`.streamlit/secrets.toml.example`** - documents every required secret key (placeholders only, safe to commit).

## Files modified

- **`config.py`** - added a small guarded block that loads `st.secrets` into `os.environ` before `load_dotenv()`, so secrets injected by Streamlit Community Cloud's Secrets manager reach the same `os.getenv()` calls the app already uses. Local `.env`-based dev is unaffected (verified).

## Validated locally

1. **Dev mode** (real `.env`): all 8 pages return HTTP 200, no errors.
2. **Production-like mode** (isolated copy, `.env` removed, only a dummy `.streamlit/secrets.toml` present): all 8 pages return HTTP 200, no exceptions, and `Config` values were confirmed to load correctly from `secrets.toml`.

## Reported, not fixed

The 5 CrewAI domain-investigation agents (`agents/crew/*`) are hard-wired to a local Ollama endpoint (`agents/crew/base.py:_build_llm()`) and will report `status="error"` on any host without a reachable Ollama server. The app does not crash - the Claude Sonnet final synthesis (the AI Copilot's actual answer) works normally either way, since it consumes raw evidence directly rather than depending on the CrewAI domain reports.

## What's left (manual steps)

1. Review `.env.example` yourself before pushing (not read-accessible to me).
2. Create a GitHub repo and push:
   ```
   git remote add origin <your-repo-url>
   git push -u origin master
   ```
3. On https://share.streamlit.io: New app -> select repo/branch `master`/main file `app.py`.
4. Advanced settings -> Python 3.12.
5. Settings -> Secrets -> paste `.streamlit/secrets.toml.example` with real values (leave `OLLAMA_BASE_URL`/`OLLAMA_MODEL` unset unless you have a reachable Ollama endpoint).
6. Deploy.
