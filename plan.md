1. **Create the integration bridge module:**
   - I will use `write_file` to create `ultra_modules/technical_proposal_outline_gen_integration_bridge.py`.
   - Implement a function `bridge_generate_proposal_outline` that accepts arbitrary or legacy payloads (e.g. from older system versions).
   - It will import `generate_proposal_outline` from `technical_proposal_outline_gen_core_engine`.
   - The integration bridge will dynamically map the input payload (which might be deeply nested, JSON strings, or contain differently structured arrays) to the standard list of dictionaries expected by the core engine.
   - Missing fields will be filled with sensible defaults. Dirty data like text strings instead of numbers, invalid values, etc., will be auto-healed before passing to the core engine.
2. **Verify the module:**
   - Use `read_file` to ensure `ultra_modules/technical_proposal_outline_gen_integration_bridge.py` is correctly created and contains the expected code.
3. **Create the unit tests:**
   - I will use `write_file` to create `tests/ultra_modules/test_technical_proposal_outline_gen_integration_bridge.py`.
   - I will create a test class inheriting from `unittest.TestCase`.
   - Test cases will cover:
     - `test_normal_payload`: Valid data standard schema.
     - `test_old_schema_mapping`: Silently mapping alternative or deeply nested legacy schema fields to the new schema without configurations.
     - `test_dirty_data_self_healing`: Handling invalid datatypes (JSON strings instead of objects, unparseable strings, missing required fields).
     - `test_missing_parameter_default`: Empty or null input where defaults must be applied.
     - `test_extreme_boundary_conditions`: Passing unexpected types like integers or booleans instead of lists, extremely large values, etc.
   - Ensure 100% branch coverage inside the integration bridge module.
4. **Verify the tests:**
   - Use `read_file` to ensure `tests/ultra_modules/test_technical_proposal_outline_gen_integration_bridge.py` is correctly created.
5. **Run tests & coverage:**
   - Use `run_in_bash_session` to execute `PYTHONPATH=. coverage run --branch -m unittest tests/ultra_modules/test_technical_proposal_outline_gen_integration_bridge.py`.
   - Run `coverage report -m ultra_modules/technical_proposal_outline_gen_integration_bridge.py` to ensure 100% branch coverage.
6. **Complete pre-commit steps to ensure proper testing, verification, review, and reflection are done.**
7. **Commit and push code:**
   - Use `run_in_bash_session` to run `git checkout -b feature/integration-bridge`, `git add .`, `git commit -m 'feat: Add technical proposal outline gen integration bridge'`, `git push -u origin HEAD`, and `gh pr create --title "feat: Add technical proposal outline gen integration bridge" --body "Implementation of integration bridge and tests for technical proposal outline generation."`.
