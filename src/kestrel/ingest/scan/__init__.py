"""Scanner: visibility rules, instruction detection and the document scan that applies them."""

from kestrel.ingest.scan.injection import InstructionMatch, detect_instructions
from kestrel.ingest.scan.scanner import scan_document
from kestrel.ingest.scan.visibility import contrast_ratio, invisibility, is_visible

__all__ = ["InstructionMatch", "contrast_ratio", "detect_instructions", "invisibility", "is_visible", "scan_document"]
