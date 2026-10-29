"""Codex (ChatGPT login) accounts: store several, switch between them, read usage.

The Claude side of cswaps never imports this package, so upstream merges of the
Claude code stay clean. The idea is the same: one stored copy of each login,
and the live login file replaced on a switch.
"""
