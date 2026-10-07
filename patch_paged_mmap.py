#!/usr/bin/env python3
from pathlib import Path

ROOT = Path("llama.cpp")

def replace_once(path, old, new, tag):
    p = ROOT / path
    s = p.read_text()
    if tag in s:
        return
    if old not in s:
        raise SystemExit(f"PATCH FAILED: {tag}")
    p.write_text(s.replace(old, new, 1))

# V5: one contiguous virtual address range per GGUF shard, populated by
# 256 MiB file-backed mappings. MAP_NORESERVE keeps the VA reservation from
# implying an equal amount of resident RAM.
p = ROOT / "src/llama-mmap.cpp"
s = p.read_text()

if "ANDROID_CONTIGUOUS_VA_V5" not in s:
    old = """        addr = mmap(NULL, file->size(), PROT_READ, flags, fd, 0);
        if (addr == MAP_FAILED) {
            throw std::runtime_error(format("mmap failed: %s", strerror(errno)));
        }

        // page-aligned madvise over [beg, end), clamped to the file
"""
    new = r"""        // ANDROID_CONTIGUOUS_VA_V5
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

        for (size_t first = 0; first < file->size(); first += map_chunk) {
            const size_t len = std::min(map_chunk, file->size() - first);
            int map_flags = MAP_SHARED | MAP_FIXED;
#ifdef MAP_NORESERVE
            map_flags |= MAP_NORESERVE;
#endif
            void * mapped = mmap(
                (char *) reserved + first, len, PROT_READ,
                map_flags, fd, (off_t) first);

            if (mapped == MAP_FAILED || mapped != (char *) reserved + first) {
                const int saved_errno = errno;
                munmap(reserved, file->size());
                addr = nullptr;
                throw std::runtime_error(format(
                    "contiguous file mapping failed at %zu..%zu: %s",
                    first, first + len, strerror(saved_errno)));
            }

            mapped_fragments.emplace_back(first, first + len);
        }

        // page-aligned madvise over [beg, end), clamped to the file
"""
    if old not in s:
        raise SystemExit("PATCH FAILED: mmap V5 constructor")
    s = s.replace(old, new, 1)

# Restore the public mmap API to the upstream v0.6.0 form.
p.write_text(s)

# V5 intentionally preserves a contiguous virtual address range, so the
# existing loader pointer arithmetic is correct. No segmented addr_at API.
for path, marker in [
    ("src/llama-model-loader.cpp", "ANDROID_CONTIGUOUS_VA_V5_LOADER"),
]:
    p = ROOT / path
    s = p.read_text()
    if marker not in s:
        # Remove only the V4 addr_at substitutions if present.
        s = s.replace(
            "mappings.at(w.idx)->addr_at(w.offs + offs)",
            "mappings.at(w.idx)->addr() + w.offs + offs",
        )
        s = s.replace(
            "mapping->addr_at(weight->offs)",
            "mapping->addr() + weight->offs",
        )
        p.write_text(s)

print("PATCH COMPLETE: Android contiguous virtual-address lazy mmap V5")
