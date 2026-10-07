#!/usr/bin/env python3
from pathlib import Path
import re

ROOT = Path("llama.cpp")

p = ROOT / "src/llama-mmap.cpp"
s = p.read_text()

needle = "        int flags = MAP_SHARED;\n        if (numa) { prefetch = 0; }"
replacement = """        int flags = MAP_SHARED;
#ifdef MAP_NORESERVE
        // Android/Termux: do not require swap/commit for the complete GGUF
        // virtual mapping. Pages are still faulted in on demand by mmap.
        if (!lazy_ranges.empty()) {
            flags |= MAP_NORESERVE;
        }
#endif
        if (numa) { prefetch = 0; }"""

if needle not in s:
    raise SystemExit("PATCH FAILED: mmap flags block not found in v0.6.0")
s = s.replace(needle, replacement, 1)

marker = '        addr = mmap(NULL, file->size(), PROT_READ, flags, fd, 0);'
if marker not in s:
    raise SystemExit("PATCH FAILED: mmap call not found in v0.6.0")

p.write_text(s)
print("PATCH COMPLETE: Android lazy mmap now uses MAP_NORESERVE")
