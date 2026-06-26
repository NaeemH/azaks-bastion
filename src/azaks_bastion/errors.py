"""Typed errors raised by the package."""

from __future__ import annotations


class AzaksBastionError(Exception):
    """Base class for all package errors."""


class BastionAccessError(AzaksBastionError):
    """Raised when `az network bastion ...` cannot be invoked or returns an error."""


class TargetNotFoundError(AzaksBastionError):
    """Raised when a tunnel target VM name cannot be resolved to an ARM id."""


class TunnelError(AzaksBastionError):
    """Raised when the Bastion tunnel process exits non-zero."""
