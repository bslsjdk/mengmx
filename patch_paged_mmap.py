#!/usr/bin/env python3
from pathlib import Path
import re

ROOT = Path("llama.cpp")
p = ROOT / "src/llama-mmap.cpp"
s = p.read_text()

if "ANDROID_PAGED_MMAP_V2" in s:
    print("PATCH ALREADY APPLIED: ANDROID_PAGED_MMAP_V2")
    raise SystemExit(0)

start = s.find("        int flags = MAP_SHARED;")
end_marker = "        mapped_fragments.emplace_back(0, file->size());"
end = s.find(end_marker, start)

if start < 0 or end < 0:
    raise SystemExit("PATCH FAILED: v0.6.0 mmap constructor block not found")

end += len(end_marker)

replacement = r'''        // ANDROID_PAGED_MMAP_V2:
        // A 4-5 GiB single mmap can be rejected by Android even when the
        // pages are lazy. Reserve the virtual address range without backing
        // it, then map only the requested tensor ranges into that address
        // space. This preserves addr()+file_offset pointer arithmetic while
        // avoiding one giant file-backed mmap.
        if (!lazy_ranges.empty()) {
            const size_t page_size = (size_t) sysconf(_SC_PAGESIZE);
            const size_t page_mask = page_size - 1;

            auto align_down = [&](size_t x) { return x & ~page_mask; };
            auto align_up = [&](size_t x) {
                return (x + page_mask) & ~page_mask;
            };

            llama_mmap::ranges ranges;
            ranges.reserve(lazy_ranges.size());
            for (const auto & r : lazy_ranges) {
                const size_t first = align_down(r.first);
                const size_t last  = std::min(align_up(r.second), file->size());
                if (first < last) {
                    ranges.emplace_back(first, last);
                }
            }

            std::sort(ranges.begin(), ranges.end());

            llama_mmap::ranges merged;
            for (const auto & r : ranges) {
                if (merged.empty() || r.first > merged.back().second) {
                    merged.push_back(r);
                } else {
                    merged.back().second = std::max(merged.back().second, r.second);
                }
            }

            int reserve_flags = MAP_PRIVATE | MAP_ANONYMOUS;
#ifdef MAP_NORESERVE
            reserve_flags |= MAP_NORESERVE;
#endif
            addr = mmap(NULL, file->size(), PROT_NONE, reserve_flags, -1, 0);
            if (addr == MAP_FAILED) {
                throw std::runtime_error(format("paged mmap reserve failed: %s", strerror(errno)));
            }

            const uintptr_t base = reinterpret_cast<uintptr_t>(addr);

            for (const auto & r : merged) {
                const size_t len = r.second - r.first;
                void * target = reinterpret_cast<void *>(base + r.first);

                int map_flags = MAP_SHARED | MAP_FIXED;
#ifdef MAP_NORESERVE
                map_flags |= MAP_NORESERVE;
#endif
                void * mapped = mmap(target, len, PROT_READ, map_flags, fd, (off_t) r.first);
                if (mapped == MAP_FAILED) {
                    const int saved_errno = errno;
                    munmap(addr, file->size());
                    addr = MAP_FAILED;
                    throw std::runtime_error(format(
                        "paged mmap fragment failed at %zu..%zu: %s",
                        r.first, r.second, strerror(saved_errno)));
                }

                mapped_fragments.push_back(r);
            }

            for (const auto & r : merged) {
                if (posix_madvise((char *) addr + r.first, r.second - r.first,
                                  POSIX_MADV_RANDOM)) {
                    LLAMA_LOG_WARN("warning: posix_madvise(.., POSIX_MADV_RANDOM) failed: %s
",
                                   strerror(errno));
                }
            }

            LLAMA_LOG_INFO("Android paged mmap: file=%zu MiB, mapped=%zu fragments
",
                           file->size() / (1024 * 1024), merged.size());
        } else {
            int flags = MAP_SHARED;
#ifdef MAP_NORESERVE
            flags |= MAP_NORESERVE;
#endif
            if (numa) { prefetch = 0; }
#ifdef __linux__
            if (posix_fadvise(fd, 0, 0, POSIX_FADV_SEQUENTIAL)) {
                LLAMA_LOG_WARN("warning: posix_fadvise(.., POSIX_FADV_SEQUENTIAL) failed: %s
",
                        strerror(errno));
            }
            if (prefetch) { flags |= MAP_POPULATE; }
#endif
            addr = mmap(NULL, file->size(), PROT_READ, flags, fd, 0);
            if (addr == MAP_FAILED) {
                throw std::runtime_error(format("mmap failed: %s", strerror(errno)));
            }
            mapped_fragments.emplace_back(0, file->size());
        }'''

s = s[:start] + replacement + s[end:]
p.write_text(s)
print("PATCH COMPLETE: Android paged mmap V2")
