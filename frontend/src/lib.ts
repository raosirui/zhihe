// Compatibility entrypoint. API/session transport and formatting helpers live
// in separate shared modules; legacy imports can continue using `src/lib`.
export * from './shared/api'
export * from './shared/lib'
