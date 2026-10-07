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

# Add a global resident-page trim hook. MADV_DONTNEED only discards clean
# file-backed pages; it does not invalidate the virtual address or the GGUF.
# The hook is called around every llama_decode() so repeated token generation
# cannot indefinitely accumulate file-backed model pages in RSS.
if "ANDROID_RSS_TRIM_V1" not in s:
    marker = "struct llama_mmap::impl {"
    if marker not in s:
        raise SystemExit("PATCH FAILED: RSS trim impl marker")
    s = s.replace(
        marker,
        """// ANDROID_RSS_TRIM_V1
// Registered mmap implementations let the public decode hook evict clean
// file-backed pages without changing their virtual addresses.
static std::mutex g_android_mmap_mutex;
static std::vector<struct llama_mmap::impl *> g_android_mmaps;

static void android_register_mmap(struct llama_mmap::impl * p) {
    std::lock_guard<std::mutex> lock(g_android_mmap_mutex);
    g_android_mmaps.push_back(p);
}

static void android_unregister_mmap(struct llama_mmap::impl * p) {
    std::lock_guard<std::mutex> lock(g_android_mmap_mutex);
    auto it = std::find(g_android_mmaps.begin(), g_android_mmaps.end(), p);
    if (it != g_android_mmaps.end()) {
        g_android_mmaps.erase(it);
    }
}

struct llama_mmap::impl {""",
        1,
    )
    # We need the complete type before dereferencing it, so the actual trim
    # loop is inserted after the impl definition and before public wrappers.
    needle = "llama_mmap::llama_mmap("
    if needle not in s:
        raise SystemExit("PATCH FAILED: RSS trim public marker")
    trim = r"""// ANDROID_RSS_TRIM_V1
static void android_trim_mmap_pages_impl() {
    std::lock_guard<std::mutex> lock(g_android_mmap_mutex);
    for (struct llama_mmap::impl * p : g_android_mmaps) {
        for (const auto & frag : p->mapped_fragments) {
            const size_t len = frag.second - frag.first;
            if (len == 0) {
                continue;
            }
            (void) madvise((char *) p->addr + frag.first, len, MADV_DONTNEED);
        }
    }
}

void llama_android_trim_mmap_pages() {
    android_trim_mmap_pages_impl();
}

"""
    s = s.replace(needle, trim + needle, 1)

    # Register after successful mappings. The destructor unregisters first.
    ctor_marker = "            mapped_fragments.emplace_back(first, first + len);"
    s = s.replace(ctor_marker, ctor_marker + "
        }

        android_register_mmap(this);

        //", 1)
    # The replacement above intentionally closes the loop, so remove the
    # duplicated original loop closing brace introduced by the insertion.
    s = s.replace(
        "            mapped_fragments.emplace_back(first, first + len);
        }

        android_register_mmap(this);

        //
        }

        // page-aligned",
        "            mapped_fragments.emplace_back(first, first + len);
        }

        android_register_mmap(this);

        // page-aligned",
        1,
    )

    # If the constructor marker manipulation did not match the exact layout,
    # fail rather than silently producing broken C++.
    if "android_register_mmap(this);" not in s:
        raise SystemExit("PATCH FAILED: RSS trim register")

    # Insert unregister at the beginning of impl destructor.
    dtor = """    ~impl() {
"""
    if dtor not in s:
        raise SystemExit("PATCH FAILED: RSS trim destructor")
    s = s.replace(
        dtor,
        """    ~impl() {
        // ANDROID_RSS_TRIM_V1
        android_unregister_mmap(this);
""",
        1,
    )

p.write_text(s)

# Header hook declaration.
replace_once(
    "src/llama-mmap.h",
    """// Prefetch the host pages covering these memory ranges.
void llama_prefetch(llama_memory_ranges mr);
""",
    """// Prefetch the host pages covering these memory ranges.
void llama_prefetch(llama_memory_ranges mr);

// Android: evict clean file-backed model pages while preserving virtual
// addresses. Intended to keep long-running lazy-mmap inference bounded.
void llama_android_trim_mmap_pages();
""",
    "ANDROID_RSS_TRIM_V1_HEADER",
)

# Trim before and after every decode. This bounds accumulation between decode
# calls. The caller should use batch size 1 for the strictest resident budget.
replace_once(
    "src/llama-context.cpp",
    """int32_t llama_decode(
        llama_context * ctx,
          llama_batch   batch) {
    const int ret = ctx->decode(batch);
""",
    """int32_t llama_decode(
        llama_context * ctx,
          llama_batch   batch) {
    // ANDROID_RSS_TRIM_V1: start each decode from a cold file-backed cache.
    llama_android_trim_mmap_pages();

    const int ret = ctx->decode(batch);

    // ANDROID_RSS_TRIM_V1: drop clean model pages before returning to the
    // sampling loop, preventing repeated token generation from accumulating
    // the entire GGUF in resident RAM.
    llama_android_trim_mmap_pages();
""",
    "ANDROID_RSS_TRIM_V1_DECODE",
)

# V5 intentionally preserves a contiguous virtual address range, so the
# existing loader pointer arithmetic is correct. No segmented addr_at API.
for path in ["src/llama-model-loader.cpp"]:
    p = ROOT / path
    s = p.read_text()
    if "ANDROID_CONTIGUOUS_VA_V5_LOADER" not in s:
        s = s.replace(
            "mappings.at(w.idx)->addr_at(w.offs + offs)",
            "mappings.at(w.idx)->addr() + w.offs + offs",
        )
        s = s.replace(
            "mapping->addr_at(weight->offs)",
            "mapping->addr() + weight->offs",
        )
        p.write_text(s)

print("PATCH COMPLETE: Android contiguous VA V5 + RSS trim hook")
