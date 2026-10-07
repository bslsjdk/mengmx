#!/usr/bin/env python3
from pathlib import Path

ROOT = Path("llama.cpp")

def patch_file(rel, old, new, tag):
    p = ROOT / rel
    s = p.read_text()
    if tag in s:
        return
    if old not in s:
        raise SystemExit(f"PATCH FAILED: {tag}: source pattern not found in {rel}")
    p.write_text(s.replace(old, new, 1))

# llama-mmap.h: expose offset -> actual virtual address translation.
patch_file(
    "src/llama-mmap.h",
    """    size_t size() const;
    void * addr() const;

    void unmap_fragment(size_t first, size_t last);
""",
    """    size_t size() const;
    void * addr() const;
    void * addr_at(size_t offset) const;
    bool is_segmented() const;

    void unmap_fragment(size_t first, size_t last);
""",
    "ANDROID_SEGMENTED_MMAP_API",
)

# llama-mmap.cpp: independently map 256 MiB file windows. No giant virtual
# reservation is attempted, because Android rejected even the reservation.
p = ROOT / "src/llama-mmap.cpp"
s = p.read_text()
if "ANDROID_SEGMENTED_MMAP_IMPL" not in s:
    old = """        addr = mmap(NULL, file->size(), PROT_READ, flags, fd, 0);
        if (addr == MAP_FAILED) {
            throw std::runtime_error(format("mmap failed: %s", strerror(errno)));
        }

        // page-aligned madvise over [beg, end), clamped to the file
"""
    new = r'''        // ANDROID_SEGMENTED_MMAP_IMPL
        const size_t page_size = (size_t) sysconf(_SC_PAGESIZE);
        const size_t map_chunk = ((size_t) 256 * 1024 * 1024) & ~(page_size - 1);

        struct segment {
            size_t first;
            size_t last;
            void * addr;
        };

        segments.clear();
        segments.reserve((file->size() + map_chunk - 1) / map_chunk);
        segmented = true;

        for (size_t first = 0; first < file->size(); first += map_chunk) {
            const size_t len = std::min(map_chunk, file->size() - first);
            int map_flags = MAP_SHARED;
#ifdef MAP_NORESERVE
            map_flags |= MAP_NORESERVE;
#endif
            void * mapped = mmap(NULL, len, PROT_READ, map_flags, fd, (off_t) first);
            if (mapped == MAP_FAILED) {
                const int saved_errno = errno;
                for (const auto & seg : segments) {
                    munmap(seg.addr, seg.last - seg.first);
                }
                segments.clear();
                segmented = false;
                throw std::runtime_error(format(
                    "segmented mmap failed at %zu..%zu: %s",
                    first, first + len, strerror(saved_errno)));
            }
            segments.push_back({ first, first + len, mapped });
        }

        addr = segments.empty() ? nullptr : segments.front().addr;
        mapped_fragments.clear();
        for (const auto & seg : segments) {
            mapped_fragments.emplace_back(seg.first, seg.last);
        }

        // page-aligned madvise over [beg, end), clamped to the file
'''
    if old not in s:
        raise SystemExit("PATCH FAILED: mmap constructor pattern")
    s=s.replace(old,new,1)

    old_members="""    std::vector<std::pair<size_t, size_t>> mapped_fragments;

    impl(struct llama_file * file, size_t prefetch, bool numa, const llama_mmap::ranges & lazy_ranges) {
"""
    new_members="""    struct segment {
        size_t first;
        size_t last;
        void * addr;
    };

    std::vector<std::pair<size_t, size_t>> mapped_fragments;
    std::vector<segment> segments;
    bool segmented = false;

    impl(struct llama_file * file, size_t prefetch, bool numa, const llama_mmap::ranges & lazy_ranges) {
"""
    if old_members not in s:
        raise SystemExit("PATCH FAILED: mmap impl members")
    s=s.replace(old_members,new_members,1)

    old_unmap="""    void unmap_fragment(size_t first, size_t last) {
        int page_size = sysconf(_SC_PAGESIZE);
"""
    new_unmap=r'''    void * addr_at(size_t offset) const {
        if (!segmented) {
            GGML_ASSERT(offset < size);
            return (char *) addr + offset;
        }
        for (const auto & seg : segments) {
            if (offset >= seg.first && offset < seg.last) {
                return (char *) seg.addr + (offset - seg.first);
            }
        }
        throw std::runtime_error(format("segmented mmap offset %zu is not mapped", offset));
    }

    void unmap_fragment(size_t first, size_t last) {
        if (segmented) {
            const size_t page_size = (size_t) sysconf(_SC_PAGESIZE);
            first &= ~(page_size - 1);
            last = std::min((last + page_size - 1) & ~(page_size - 1), size);
            if (first >= last) {
                return;
            }

            std::vector<segment> keep;
            keep.reserve(segments.size());

            for (const auto & seg : segments) {
                if (seg.last <= first || seg.first >= last) {
                    keep.push_back(seg);
                    continue;
                }

                const size_t cut_first = std::max(first, seg.first);
                const size_t cut_last = std::min(last, seg.last);
                const size_t cut_len = cut_last - cut_first;
                void * cut_addr = (char *) seg.addr + (cut_first - seg.first);

                if (cut_len && munmap(cut_addr, cut_len)) {
                    LLAMA_LOG_WARN("warning: munmap failed: %s
", strerror(errno));
                }

                if (seg.first < cut_first) {
                    keep.push_back({seg.first, cut_first, seg.addr});
                }
                if (cut_last < seg.last) {
                    keep.push_back({
                        cut_last,
                        seg.last,
                        (char *) seg.addr + (cut_last - seg.first)
                    });
                }
            }

            segments = std::move(keep);
            mapped_fragments.clear();
            for (const auto & seg : segments) {
                mapped_fragments.emplace_back(seg.first, seg.last);
            }
            return;
        }

        int page_size = sysconf(_SC_PAGESIZE);
'''
    if old_unmap not in s:
        raise SystemExit("PATCH FAILED: mmap unmap function")
    s=s.replace(old_unmap,new_unmap,1)

    import re
    old_des_re = re.compile(r"    ~impl\\(\\) \\{\\n        for \\(const auto & frag : mapped_fragments\\) \\{\\n            if \\(munmap\\(\\(char \\*\\) addr \\+ frag\\.first, frag\\.second - frag\\.first\\)\\) \\{\\n                LLAMA_LOG_WARN\\(\\"warning: munmap failed: %s\\\\n\\", strerror\\(errno\\)\\);\\n            \\}\\n        \\}\\n    \\}\\n")
    new_des = """    ~impl() {
        if (segmented) {
            for (const auto & seg : segments) {
                if (munmap(seg.addr, seg.last - seg.first)) {
                    LLAMA_LOG_WARN("warning: munmap failed: %s\\n", strerror(errno));
                }
            }
            return;
        }

        for (const auto & frag : mapped_fragments) {
            if (munmap((char *) addr + frag.first, frag.second - frag.first)) {
                LLAMA_LOG_WARN("warning: munmap failed: %s\\n", strerror(errno));
            }
        }
    }
"""
    if not old_des_re.search(s):
        raise SystemExit("PATCH FAILED: mmap destructor")
    s=old_des_re.sub(new_des, s, count=1)