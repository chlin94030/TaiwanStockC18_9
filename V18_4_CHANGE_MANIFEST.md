# V18.4 Deployable File Manifest

Place files from the inner directory at the GitHub project root. Keep your Streamlit secrets outside this repository.
This is a source package: excludes local sqlite cache, archived PIT observations, tokens, compiled files, hidden test state.
Full zip is self-contained for a fresh Streamlit project; replace zip requires the existing V18.0 base source files.

## Modified and new files
- .github/workflows/qa_v17.yml
- README.md
- RELEASE_V18_4.md
- TEST_OUTPUT_V18_4.txt
- VALIDATION_STATUS_V18_4.md
- app.py
- audit_v18_pit_oos.py
- market_data.py
- pit_data_manager.py
- radar_service.py
- recommendation_engine.py
- run_full_qa.py
- strategy_config.py
- test_v184_integrated.py
- universe_archive.py
- v18_confidence_engine.py
- v18_multifactor.py
- v18_rank_stability.py
- v18_ui.py
