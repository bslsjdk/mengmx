#!/usr/bin/env python3
from pathlib import Path

ROOT = Path("llama.cpp")

def replace_once(rel, old, new, tag):
    p = ROOT / rel
    s = p.read_text()
    if tag in s:
        return
    if old not in s:
        raise SystemExit(f"PATCH FAILED: {tag}")
    p.write_text(s.replace(old, new, 1))

# V5: keep one contiguous virtual address range per GGUF shard, but back it
# with 256 MiB file mappings. The address space is reserved with PROT_NONE
# anonymous MAP_NORESERVE, so the 4-5 GiB virtual range does not mean 4-5 GiB
# of resident RAM. File pages are faulted in on demand.
replace_once(
    "src/llama-mmap.h",
    """    size_t size() const;
    void * addr() const;

    void unmap_fragment(size_t first, size_t last);
""",
    """    size_t size() const;
    void * addr() const;

    void unmap_fragment(size_t first, size_t last);
""",
    "ANDROID_CONTIGUOUS_VA_V5_API",
)

p = ROOT / "src/llama-mmap.cpp"
s = p.read_text()

old = """        addr = mmap(NULL, file->size(), PROT_READ, flags, fd, 0);
        if (addr == MAP_FAILED) {
            throw std::runtime_error(format("mmap failed: %s", strerror(errno)));
        }

        // page-aligned madvise over [beg, end), clamped to the file
"""
new = r"""        // ANDROID_CONTIGUOUS_VA_V5
        // Reserve one contiguous virtual range, then replace it with file-backed
        // mappings in chunks. This preserves the single-base-pointer invariant
        // expected by ggml while avoiding one giant file mmap on Android/FUSE.
        const size_t page_size = (size_t) sysconf(_SC_PAGESIZE);
        const size_t map_chunk = ((size_t) 256 * 1024 * 1024) & ~(page_size - 1);

        int reserve_flags = MAP_PRIVATE | MAP_ANONYMOUS;
#ifdef MAP_NORESERVE
        reserve_flags |= MAP_NORESERVE;
#endif
        void * reserved = mmap(NULL, file->size(), PROT_NONE, reserve_flags, -1, 0);
        if (reserved == MAP_FAILED) {
            throw std::runtime_error(format(
                "contiguous virtual address reservation failed for %zu bytes: %s",
                file->size(), strerror(errno)));
        }

        addr = reserved;
        reserved_base = reserved;
        reserved_size = file->size();

        for (size_t first = 0; first < file->size(); first += map_chunk) {
            const size_t len = std::min(map_chunk, file->size() - first);
            int map_flags = MAP_SHARED | MAP_FIXED;
#ifdef MAP_NORESERVE
            map_flags |= MAP_NORESERVE;
#endif
            void * mapped = mmap(
                (char *) reserved + first,
                len,
                PROT_READ,
                map_flags,
                fd,
                (off_t) first);

            if (mapped == MAP_FAILED || mapped != (char *) reserved + first) {
                const int saved_errno = errno;
                munmap(reserved, file->size());
                addr = nullptr;
                reserved_base = nullptr;
                reserved_size = 0;
                throw std::runtime_error(format(
                    "contiguous file mapping failed at %zu..%zu: %s",
                    first, first + len, strerror(saved_errno)));
            }

            mapped_fragments.emplace_back(first, first + len);
        }

        // page-aligned madvise over [beg, end), clamped to the file
"""
if "ANDROID_CONTIGUOUS_VA_V5" not in s:
    if old not in s:
        raise SystemExit("PATCH FAILED: mmap V5 constructor")
    s = s.replace(old, new, 1)

# No extra state is required for cleanup: mapped_fragments contains the
# file-backed pieces occupying the reserved contiguous virtual range.
if "ANDROID_CONTIGUOUS_VA_V5_MEMBERS" not in s:
    marker = "    std::vector<std::pair<size_t, size_t>> mapped_fragments;\n"
    if marker not in s:
        raise SystemExit("PATCH FAILED: mmap V5 members")
    s = s.replace(
        marker,
        marker + "    // ANDROID_CONTIGUOUS_VA_V5_MEMBERS\n",
        1,
    )

# The original advise lambda already uses one contiguous addr, which is now
# exactly what V5 guarantees. Keep it unchanged.

old_unmap = """    void unmap_fragment(size_t first, size_t last) {
        int page_size = sysconf(_SC_PAGESIZE);
"""
new_unmap = r"""    void unmap_fragment(size_t first, size_t last) {
        int page_size = sysconf(_SC_PAGESIZE);
"""
# Keep the original implementation. Its address arithmetic is valid because
# V5 deliberately preserves a contiguous virtual address range.

# The upstream destructor already unmaps every mapped fragment. Because
# V5 keeps every fragment inside one contiguous VA reservation, that cleanup
# remains correct.\n\np.write_text(s)

# Restore the loader to normal contiguous-address semantics. V5 intentionally
# provides one contiguous virtual address range, so no addr_at indirection is
# required and the existing ggml host-buffer path can remain intact.
for rel, bad, good, tag in [
    (
        "src/llama-model-loader.cpp",
        "data = (const uint8_t *) mappings.at(w.idx)->addr_at(w.offs + offs);",
        "data = (const uint8_t *) mappings.at(w.idx)->addr() + w.offs + offs;",
        "ANDROID_CONTIGUOUS_VA_V5_LOADER1",
    ),
    (
        "src/llama-model-loader.cpp",
        "uint8_t * data = (uint8_t *) mapping->addr_at(weight->offs);",
        "uint8_t * data = (uint8_t *) mapping->addr() + weight->offs;",
        "ANDROID_CONTIGUOUS_VA_V5_LOADER2",
    ),
]:
    replace_once(rel, bad, good, tag)

print("PATCH COMPLETE: Android contiguous virtual-address lazy mmap V5")
