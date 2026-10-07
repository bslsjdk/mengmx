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

# Add a registry for clean file-backed page eviction. Use void* in the global
# registry so the private nested impl type never appears in a free-function
# declaration. Member code performs the cast and accesses the private fields.
if "ANDROID_RSS_TRIM_V3" not in s:
    marker = "struct llama_mmap::impl {"
    if marker not in s:
        raise SystemExit("PATCH FAILED: RSS trim impl marker")

    prefix = r"""// ANDROID_RSS_TRIM_V3
#include <atomic>
#include <chrono>
#include <mutex>
#include <thread>
static std::mutex g_android_mmap_mutex;
static std::vector<void *> g_android_mmaps;
static std::atomic<bool> g_android_trim_running{false};

static void android_trim_worker() {
    while (g_android_trim_running.load(std::memory_order_relaxed)) {
        std::this_thread::sleep_for(std::chrono::milliseconds(5));
        if (g_android_trim_running.load(std::memory_order_relaxed)) {
            llama_mmap::trim_all();
        }
    }
}

static void android_start_trim_worker() {
    bool expected = false;
    if (g_android_trim_running.compare_exchange_strong(
            expected, true, std::memory_order_acq_rel)) {
        std::thread(android_trim_worker).detach();
    }
}

static void android_register_mmap(void * p) {
    std::lock_guard<std::mutex> lock(g_android_mmap_mutex);
    g_android_mmaps.push_back(p);
    android_start_trim_worker();
}

static void android_unregister_mmap(void * p) {
    std::lock_guard<std::mutex> lock(g_android_mmap_mutex);
    auto it = std::find(g_android_mmaps.begin(), g_android_mmaps.end(), p);
    if (it != g_android_mmaps.end()) {
        g_android_mmaps.erase(it);
    }
    if (g_android_mmaps.empty()) {
        g_android_trim_running.store(false, std::memory_order_release);
    }
}

"""
    s = s.replace(marker, prefix + marker, 1)

    # Register after all file fragments have been installed.
    needle = """            mapped_fragments.emplace_back(first, first + len);
        }

        // page-aligned madvise over [beg, end), clamped to the file
"""
    repl = """            mapped_fragments.emplace_back(first, first + len);
        }

        android_register_mmap(this);

        // page-aligned madvise over [beg, end), clamped to the file
"""
    if needle not in s:
        raise SystemExit("PATCH FAILED: RSS trim register")

    s = s.replace(needle, repl, 1)

    # Unregister before the destructor starts unmapping fragments.
    needle = """    ~impl() {
"""
    if needle not in s:
        raise SystemExit("PATCH FAILED: RSS trim destructor")
    s = s.replace(
        needle,
        """    ~impl() {
        android_unregister_mmap(this);
""",
        1,
    )

    # The static member can name/access the private impl type and its fields.
    needle = "llama_mmap::llama_mmap("
    if needle not in s:
        raise SystemExit("PATCH FAILED: RSS trim member marker")
    trim = r"""void llama_mmap::trim_all() {
    std::lock_guard<std::mutex> lock(g_android_mmap_mutex);

    for (void * raw : g_android_mmaps) {
        auto * p = static_cast<struct llama_mmap::impl *>(raw);
        for (const auto & frag : p->mapped_fragments) {
            const size_t len = frag.second - frag.first;
            if (len != 0) {
                (void) madvise((char *) p->addr + frag.first, len, MADV_DONTNEED);
            }
        }
    }
}

"""
    s = s.replace(needle, trim + needle, 1)

    p.write_text(s)

# Header: public static trim hook.
replace_once(
    "src/llama-mmap.h",
    """    void unmap_fragment(size_t first, size_t last);

    static const bool SUPPORTED;
""",
    """    void unmap_fragment(size_t first, size_t last);

    // Android lazy-mmap resident-page trim. Preserves virtual addresses.
    static void trim_all();

    static const bool SUPPORTED;
""",
    "ANDROID_RSS_TRIM_V2_HEADER",
)

# Trim before and after every decode. Batch size 1 gives the tightest bound;
# this prevents clean model pages from accumulating across generated tokens.
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
    // ANDROID_RSS_TRIM_V2: cold-start each decode.
    llama_mmap::trim_all();

    const int ret = ctx->decode(batch);

    // ANDROID_RSS_TRIM_V2: release clean file-backed model pages after decode.
    llama_mmap::trim_all();
""",
    "ANDROID_RSS_TRIM_V2_DECODE",
)

# V5 deliberately preserves one contiguous VA base per shard.
p = ROOT / "src/llama-model-loader.cpp"
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

print("PATCH COMPLETE: Android contiguous VA V5 + resident-page trim V3 background")
