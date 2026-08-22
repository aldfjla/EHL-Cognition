"""Persistence: SQLite via SQLModel.

The store holds what must survive an API restart — runs, agents, scenarios,
messages, findings, reports. Live event fan-out is the bus's job, not this
package's; the two are separate on purpose so a dashboard reconnect reads
durable state and then resumes the stream.
"""
