#!/usr/bin/env python3
from pathlib import Path

ROOT = Path("llama.cpp")

def replace(path, old, new, label):
    p = ROOT / path
    s = p.read_text()
    if old not in s:
        raise SystemExit(f"PATCH FAILED: {label} not found in {p}")
    p.write_text(s.replace(old, new, 1))
    print("patched", p, label)

# 1) expose sparse fragment mapping.
replace("src/llama-mmap.h",
"""    void unmap_fragment(size_t first, size_t last);
""",
"""    // Map only the requested file range. This is used by Android lazy loading
    // so a multi-GB GGUF shard never needs one contiguous mmap reservation.
    void * map_fragment(size_t first, size_t last);
    void unmap_fragment(size_t first, size_t last);
""",
"mmap header")

# 2) Replace the POSIX mmap implementation with a sparse-fragment-capable one.
p = ROOT / "src/llama-mmap.cpp"
s = p.read_text()
old = """struct llama_mmap::impl {
#ifdef _POSIX_MAPPED_FILES
    std::vector<std::pair<size_t, size_t>> mapped_fragments;

    impl(struct llama_file * file, size_t prefetch, bool numa, const llama_mmap::ranges & lazy_ranges) {
        size = file->size();
        int fd = file->file_id();
        int flags = MAP_SHARED;
        if (numa) { prefetch = 0; }
#ifdef __linux__
        if (posix_fadvise(fd, 0, 0, POSIX_FADV_SEQUENTIAL)) {
            LLAMA_LOG_WARN("warning: posix_fadvise(.., POSIX_FADV_SEQUENTIAL) failed: %s\\n",
                    strerror(errno));
        }
        // MAP_POPULATE would fault in the lazy ranges too
        if (prefetch && lazy_ranges.empty()) { flags |= MAP_POPULATE; }
#endif
        addr = mmap(NULL, file->size(), PROT_READ, flags, fd, 0);
        if (addr == MAP_FAILED) {
            throw std::runtime_error(format("mmap failed: %s", strerror(errno)));
        }

        // page-aligned madvise over [beg, end), clamped to the file
        auto advise = [&](size_t beg, size_t end, int advice, const char * name) {
            const size_t page_size = sysconf(_SC_PAGESIZE);
            beg = beg & ~(page_size - 1);
            end = std::min((end + page_size - 1) & ~(page_size - 1), file->size());
            if (beg >= end) {
                return;
            }
            if (posix_madvise((char *) addr + beg, end - beg, advice)) {
                LLAMA_LOG_WARN("warning: posix_madvise(.., %s) failed: %s\\n", name, strerror(errno));
            }
        };

        if (prefetch > 0) {
            for (const auto & range : ranges_complement(lazy_ranges, std::min(file->size(), prefetch))) {
                advise(range.first, range.second, POSIX_MADV_WILLNEED, "POSIX_MADV_WILLNEED");
            }
        }
        for (const auto & range : lazy_ranges) {
            advise(range.first, range.second, POSIX_MADV_RANDOM, "POSIX_MADV_RANDOM");
        }
        if (numa) {
            if (posix_madvise(addr, file->size(), POSIX_MADV_RANDOM)) {
                LLAMA_LOG_WARN("warning: posix_madvise(.., POSIX_MADV_RANDOM) failed: %s\\n", strerror(errno));
            }
        }

        mapped_fragments.emplace_back(0, file->size());
    }

    static void align_range(size_t * first, size_t * last, size_t page_size) {
"""
new = """struct llama_mmap::impl {
#ifdef _POSIX_MAPPED_FILES
    struct mapped_fragment {
        size_t first;
        size_t last;
        void * addr;
    };

    std::vector<mapped_fragment> mapped_fragments;
    int fd = -1;
    size_t page_size = 4096;

    impl(struct llama_file * file, size_t prefetch, bool numa, const llama_mmap::ranges & lazy_ranges) {
        size = file->size();
        fd = file->file_id();
        page_size = (size_t) sysconf(_SC_PAGESIZE);
        if (page_size == 0) {
            page_size = 4096;
        }

        // Android can reject a single multi-GB mmap even though individual
        // tensor-sized mappings are perfectly usable. When lazy_ranges is
        // non-empty, keep the address space sparse and map fragments on demand.
        if (!lazy_ranges.empty()) {
            LLAMA_LOG_INFO("llama_mmap: Android sparse lazy mapping enabled for %.2f GiB\\n",
                    size / (1024.0 * 1024.0 * 1024.0));
            addr = nullptr;
            return;
        }

        int flags = MAP_SHARED;
        if (numa) { prefetch = 0; }
#ifdef __linux__
        if (posix_fadvise(fd, 0, 0, POSIX_FADV_SEQUENTIAL)) {
            LLAMA_LOG_WARN("warning: posix_fadvise(.., POSIX_FADV_SEQUENTIAL) failed: %s\\n",
                    strerror(errno));
        }
        if (prefetch) { flags |= MAP_POPULATE; }
#endif
        addr = mmap(NULL, file->size(), PROT_READ, flags, fd, 0);
        if (addr == MAP_FAILED) {
            throw std::runtime_error(format("mmap failed: %s", strerror(errno)));
        }
        mapped_fragments.push_back({0, file->size(), addr});
    }

    void * map_fragment(size_t first, size_t last) {
        if (last <= first) {
            return nullptr;
        }

        size_t beg = first & ~(page_size - 1);
        size_t end = std::min((last + page_size - 1) & ~(page_size - 1), size);
        if (beg >= end) {
            return nullptr;
        }

        for (const auto & f : mapped_fragments) {
            if (f.first <= beg && f.last >= end) {
                return (uint8_t *) f.addr + (first - f.first);
            }
        }

        void * p = mmap(nullptr, end - beg, PROT_READ, MAP_SHARED, fd, beg);
        if (p == MAP_FAILED) {
            throw std::runtime_error(format("sparse mmap failed at %zu..%zu: %s",
                    beg, end, strerror(errno)));
        }

#ifdef __linux__
        (void) posix_madvise(p, end - beg, POSIX_MADV_RANDOM);
#endif
        mapped_fragments.push_back({beg, end, p});
        return (uint8_t *) p + (first - beg);
    }

    static void align_range(size_t * first, size_t * last, size_t page_size) {
"""
replace("src/llama-mmap.cpp", old, new, "POSIX mmap constructor")

# Replace the POSIX unmap implementation so it handles independent addresses.
old2 = """    void unmap_fragment(size_t first, size_t last) {
        int page_size = sysconf(_SC_PAGESIZE);
        align_range(&first, &last, page_size);
        size_t len = last - first;

        if (len == 0) {
            return;
        }

        GGML_ASSERT(first % page_size == 0);
        GGML_ASSERT(last % page_size == 0);
        GGML_ASSERT(last > first);

        void * next_page_start = (uint8_t *) addr + first;

        if (munmap(next_page_start, len)) {
            LLAMA_LOG_WARN("warning: munmap failed: %s\\n", strerror(errno));
        }

        std::vector<std::pair<size_t, size_t>> new_mapped_fragments;
        for (const auto & frag : mapped_fragments) {
            if (frag.first < first && frag.second > last) {
                new_mapped_fragments.emplace_back(frag.first, first);
                new_mapped_fragments.emplace_back(last, frag.second);
            } else if (frag.first < first && frag.second > first) {
                new_mapped_fragments.emplace_back(frag.first, first);
            } else if (frag.first < last && frag.second > last) {
                new_mapped_fragments.emplace_back(last, frag.second);
            } else if (frag.first >= first && frag.second <= last) {
            } else {
                new_mapped_fragments.push_back(frag);
            }
        }
        mapped_fragments = std::move(new_mapped_fragments);
    }

    ~impl() {
        for (const auto & frag : mapped_fragments) {
            if (munmap((char *) addr + frag.first, frag.second - frag.first)) {
                LLAMA_LOG_WARN("warning: munmap failed: %s\\n", strerror(errno));
            }
        }
    }
"""
new2 = """    void unmap_fragment(size_t first, size_t last) {
        align_range(&first, &last, page_size);
        if (last <= first) {
            return;
        }

        std::vector<mapped_fragment> keep;
        keep.reserve(mapped_fragments.size());

        for (const auto & frag : mapped_fragments) {
            if (frag.first >= first && frag.last <= last) {
                if (munmap(frag.addr, frag.last - frag.first)) {
                    LLAMA_LOG_WARN("warning: munmap failed: %s\\n", strerror(errno));
                }
                continue;
            }
            keep.push_back(frag);
        }
        mapped_fragments = std::move(keep);
    }

    ~impl() {
        for (const auto & frag : mapped_fragments) {
            if (munmap(frag.addr, frag.last - frag.first)) {
                LLAMA_LOG_WARN("warning: munmap failed: %s\\n", strerror(errno));
            }
        }
    }
"""
replace("src/llama-mmap.cpp", old2, new2, "POSIX sparse unmap")

# Add public wrappers.
replace("src/llama-mmap.cpp",
"""void llama_mmap::unmap_fragment(size_t first, size_t last) {
    pimpl->unmap_fragment(first, last);
}
""",
"""void * llama_mmap::map_fragment(size_t first, size_t last) {
    return pimpl->map_fragment(first, last);
}

void llama_mmap::unmap_fragment(size_t first, size_t last) {
    pimpl->unmap_fragment(first, last);
}
""",
"mmap public wrappers")

# 3) For lazy tensors, map only that tensor instead of requiring one whole-shard mmap.
replace("src/llama-model-loader.cpp",
"""            uint8_t * data = (uint8_t *) mapping->addr() + weight->offs;

            if (check_tensors) {
""",
"""            uint8_t * data = nullptr;
            if (lazy.has(cur)) {
                data = (uint8_t *) mapping->map_fragment(weight->offs, weight->offs + n_size);
            } else {
                data = (uint8_t *) mapping->addr() + weight->offs;
            }

            if (check_tensors) {
""",
"lazy tensor mapping")

# 4) lazy mode should remain usable even when the normal whole-file mmap is disabled.
replace("src/llama-model-loader.cpp",
"""    if (!llama_mmap::SUPPORTED) {
        LLAMA_LOG_WARN("%s: mmap is not available, so tensor %s (size = %zu MiB) is loaded into RAM in full\\n",
                __func__, name.c_str(), ggml_nbytes(t)/1024/1024);
        return false;
    }
""",
"""    if (!llama_mmap::SUPPORTED) {
        LLAMA_LOG_WARN("%s: mmap is not available, so tensor %s (size = %zu MiB) is loaded into RAM in full\\n",
                __func__, name.c_str(), ggml_nbytes(t)/1024/1024);
        return false;
    }
""",
"lazy mmap capability check")

print("PATCH COMPLETE")
