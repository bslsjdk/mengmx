#!/usr/bin/env python3
from pathlib import Path

ROOT = Path("llama.cpp")
p = ROOT / "src/llama-mmap.cpp"
s = p.read_text()

# Always start from the clean v0.6.0 source. The previous experimental patch
# could have damaged the constructor, so do not try to patch an already
# modified source tree.
if "ANDROID_SEGMENTED_MMAP_V1" in s:
    print("PATCH ALREADY APPLIED: ANDROID_SEGMENTED_MMAP_V1")
    raise SystemExit(0)

old = """        addr = mmap(NULL, file->size(), PROT_READ, flags, fd, 0);
        if (addr == MAP_FAILED) {
            throw std::runtime_error(format("mmap failed: %s", strerror(errno)));
        }

        // page-aligned madvise over [beg, end), clamped to the file
"""

if old not in s:
    raise SystemExit("PATCH FAILED: clean v0.6.0 mmap block not found")

new = r'''        // ANDROID_SEGMENTED_MMAP_V1:
        // Android may reject one giant 4-5 GiB file-backed mmap even when the
        // mapping is demand-paged. Reserve the address range with no backing,
        // then map the same file in moderate chunks. This keeps the original
        // addr + file_offset addressing model while avoiding one huge mmap().
        const size_t page_size = (size_t) sysconf(_SC_PAGESIZE);
        const size_t chunk_size = (size_t) 256 * 1024 * 1024;
        const size_t map_chunk = chunk_size & ~(page_size - 1);

        int reserve_flags = MAP_PRIVATE | MAP_ANONYMOUS;
#ifdef MAP_NORESERVE
        reserve_flags |= MAP_NORESERVE;
#endif
        addr = mmap(NULL, file->size(), PROT_NONE, reserve_flags, -1, 0);
        if (addr == MAP_FAILED) {
            throw std::runtime_error(format("segmented mmap reserve failed: %s", strerror(errno)));
        }

        const uintptr_t base = reinterpret_cast<uintptr_t>(addr);
        for (size_t first = 0; first < file->size(); first += map_chunk) {
            const size_t len = std::min(map_chunk, file->size() - first);
            void * target = reinterpret_cast<void *>(base + first);

            int map_flags = MAP_SHARED | MAP_FIXED;
#ifdef MAP_NORESERVE
            map_flags |= MAP_NORESERVE;
#endif
            void * mapped = mmap(target, len, PROT_READ, map_flags, fd, (off_t) first);
            if (mapped == MAP_FAILED) {
                const int saved_errno = errno;
                munmap(addr, file->size());
                addr = MAP_FAILED;
                throw std::runtime_error(format(
                    "segmented mmap failed at %zu..%zu: %s",
                    first, first + len, strerror(saved_errno)));
            }
            mapped_fragments.emplace_back(first, first + len);
        }

        // page-aligned madvise over [beg, end), clamped to the file
'''

s = s.replace(old, new, 1)
p.write_text(s)
print("PATCH COMPLETE: Android segmented mmap V1")
