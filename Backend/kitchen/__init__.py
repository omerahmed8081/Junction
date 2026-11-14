"""The restaurant's side: the agent, the policy engine, the ledger and the trace.

Owns its own database schema and its own signing key. Reaches Aimo only by HTTP
calls to the gateway, exactly as any third-party agent would.
"""
