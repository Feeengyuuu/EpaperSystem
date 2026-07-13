"""In-process source-health attestations for rendered DATA images."""

from __future__ import annotations

from enum import Enum


_ATTESTATION_ATTRIBUTE = "_inkypi_source_provenance_attestation"


class SourceProvenance(str, Enum):
    LIVE = "live"
    FRESH_CACHE = "fresh_cache"
    STALE_CACHE = "stale_cache"
    LOCAL_FALLBACK = "local_fallback"


class _TrustedSourceProvenanceAttestation:
    """A typed process-local marker that image metadata cannot reproduce."""

    __slots__ = ("provenance",)

    def __init__(self, provenance: SourceProvenance):
        self.provenance = provenance

    def __repr__(self) -> str:
        return "<trusted-source-provenance>"


def attach_source_provenance(image, provenance, *, detail=""):
    """Bind a process-local source attestation and return ``image``."""

    if not hasattr(image, "info"):
        raise TypeError("image must expose image metadata")
    try:
        provenance = SourceProvenance(provenance)
    except (TypeError, ValueError) as exc:
        raise ValueError("source provenance is invalid") from exc
    del detail
    setattr(
        image,
        _ATTESTATION_ATTRIBUTE,
        _TrustedSourceProvenanceAttestation(provenance),
    )
    return image


def read_source_provenance(image):
    """Read only a marker attached by this process; metadata strings are ignored."""

    attestation = getattr(image, _ATTESTATION_ATTRIBUTE, None)
    if type(attestation) is not _TrustedSourceProvenanceAttestation:
        return None
    return attestation.provenance
