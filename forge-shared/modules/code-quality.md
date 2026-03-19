# Code Quality & Standards

## Documentation
- Log outcomes in nearest `docs/progress.md`
- Update `docs/PLAN.md` when completing sprints
- Keep `CLAUDE.md` current with focus areas

## Code Quality
- Run tests before committing
- No TODOs without issue references
- Prefer editing existing files over creating new ones
- Keep solutions simple - no over-engineering

## Dependencies
- **Python**: Use `uv add` (never pip)
- **Node**: Use `npm` or `pnpm`
- Pin versions in production
- Audit regularly with `/deps`

## Error Handling
- Use structured `AppError` with error codes
- Log with context (request_id, user_id)
- Never expose stack traces in production
- Graceful degradation for non-critical services

## Performance
- Async-first for I/O operations
- Connection pooling for databases
- Rate limiting on all public endpoints
- Cache where appropriate (Redis)
