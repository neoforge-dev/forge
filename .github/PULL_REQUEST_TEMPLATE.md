## Description
<!-- Describe the changes in this PR -->

## Type of Change
- [ ] Bug fix
- [ ] New feature
- [ ] Breaking change
- [ ] Documentation update

## Testing
<!-- Describe the testing approach -->

## Checklist
- [ ] Code changes follow style guidelines
- [ ] All tests pass (`npm run test` or `uv run pytest`)
- [ ] Documentation is updated
- [ ] No console warnings or errors

---

## IMPORTANT: Command Center Frontend Changes

**If you modified ANY files in `harness/command_center/`:**

- [ ] Did you bump the service worker cache version?
  - In `harness/command_center/public/sw.js`, line 4:
  - Change `const CACHE_VERSION = 'v1'` to `const CACHE_VERSION = 'v2'` (or next version)
  - This ensures users get the latest code instead of stale cached versions
  - **Missing this causes the dashboard to appear broken with old code**

- [ ] Pre-commit hooks ran successfully?
  - TypeScript type checking: `npm run type-check`
  - ESLint validation: `npm run lint`
  - These run automatically but you can verify manually

---

## Related Issues
<!-- Link related issues here -->
Closes #

## Screenshots (if applicable)
<!-- Add screenshots for UI changes -->
