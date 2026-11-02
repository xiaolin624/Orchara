"""Shared real-stack test support for the Orchara product tests.

The package is new in F00B/T022. ``tests/f01/conftest.py`` still carries its own
copy of the same primitives; the cross-ticket plan explicitly allows landing the
shared package first and refactoring F01 into a thin wrapper in the final review
ticket, so this ticket does not risk the green F01 suite.

Nothing here is mocked: helper modules create throwaway PostgreSQL databases,
build real Go binaries, start real HTTP(S) listeners and talk to them over
sockets.
"""
