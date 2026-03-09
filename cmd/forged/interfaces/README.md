# FORGE v3 Interfaces

## Interface-First Development

ALL new features MUST:
1. Define interface FIRST
2. Get interface reviewed
3. Implement against interface
4. Verify with compile-time check (e.g. `var _ interfaces.MyInterface = (*MyImpl)(nil)`)

## Current Interfaces

- **ContextManager** — Context operations (envelopes, bootstrap)
- **RoyalJelly** — Hook system (event triggers)
- **PatrolSystem** — Patrol scheduling and context-aware patrols
- **HTTPHandler** — HTTP route registration
- **Hook** — Single hook for RoyalJelly events

(Additional interfaces: see `contracts.go`, `dark_factory.go`.)

## Adding New Interfaces

1. Create file in this directory (or use `make new-interface name=InterfaceName`)
2. Define interface with methods
3. Add compile-time verification in implementation: `var _ interfaces.MyInterface = (*MyImpl)(nil)`
4. Update this README
