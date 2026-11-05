1. **Create the `one_page_go_nogo_card_cli_one_click.py` module in `ultra_modules`.**
   - Implement the `GoNoGoCardGenerator` class containing the core logic as pure functions.
   - It should accept a bid document (e.g., plain text or mock data structure) and output a one-page Go/No-Go decision card.
   - The decision card should contain fields like project name, key dates, estimated value, requirements, and a final AI recommendation (Go, No-Go, or Caution).
   - Follow the "Complex Backend, Minimalist Frontend" principle: zero configuration, default parameters, dirty data self-healing, and auto-fallback.
   - The CLI adapter should be simple to use, outputting in both JSON and Mock Excel formats.

2. **Create the `test_one_page_go_nogo_card_cli_one_click.py` in `tests/ultra_modules`.**
   - Write at least 5 test cases using `unittest`.
   - Cover normal cases, dirty data self-healing, missing parameters (default fallback), and extreme boundaries.
   - Ensure 100% branch coverage (or as close as possible) for the core logic.

3. **Verify the implementation by running tests.**
   - Execute `python3 -m unittest discover -s tests/ultra_modules` locally.
   - Iterate on the code until all tests pass.

4. **Complete pre-commit steps.**
   - Run `pre_commit_instructions` and follow the provided instructions to ensure proper testing, verification, review, and reflection are done.

5. **Commit and Submit.**
   - Once all tests pass and pre-commit checks are done, create a descriptive commit message.
   - Push to a branch and submit the PR.
