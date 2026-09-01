"""Write a standalone IoStore container - .utoc / .ucas / a stub .pak.

Enough of the format to publish a handful of already-cooked packages as a mod
container that the game mounts out of `Content/Paks/Mods/`, shadowing the copies
in `pakchunk0`. Nothing here cooks or re-serializes a package: the caller hands
over finished package bytes and the store entry that pakchunk0 already carries
for them.

Two pieces had to be recovered from the shipped containers rather than looked
up (RESEARCH.md 12):

* the TOC's perfect hash - FNV-1 (multiply, then xor) over the 12 chunk-id
  bytes, 64 bits wide, seeded; `chunk_hash(0, id) % SeedCount` picks the seed,
  a negative seed *is* the slot, a positive one hashes to it. Confirmed against
  every chunk of pakchunk0 and of a third-party mod container.
* `FIoContainerHeader` version 2 - package ids, then one 24-byte store entry
  each (export count, bundle count, and two `{count, offset-from-here}` array
  views), with the array data following the fixed block.

Everything is written uncompressed: compression only pays for size, the
container is ~120 KB, and it keeps the writer free of Oodle.
"""
import hashlib, struct
from collections import defaultdict

BLOCK_SIZE = 64 * 1024
TOC_MAGIC = b'-==--==--==--==-'
TOC_VERSION = 5              # what this game ships
HEADER_SIZE = 144
CONTAINER_HEADER_MAGIC = 0x496F436E          # 'IoCn'
CONTAINER_HEADER_VERSION = 2                 # OptionalSegmentPackages
FLAG_COMPRESSED, FLAG_INDEXED = 1, 8
NONE = 0xFFFFFFFF

FNV1_BASIS = 0xCBF29CE484222325
FNV1_PRIME = 0x00000100000001B3
M64 = (1 << 64) - 1


# --------------------------------------------------------------- perfect hash

def chunk_hash(seed, chunk_id):
    """The TOC's chunk-id hash: 64-bit FNV-1, `seed` replacing the basis."""
    h = seed if seed else FNV1_BASIS
    for b in chunk_id:
        h = ((h * FNV1_PRIME) & M64) ^ b
    return h


def lookup(chunk_ids, seeds, chunk_id):
    """The engine's side of it - used to prove a written table resolves."""
    seed = seeds[chunk_hash(0, chunk_id) % len(seeds)]
    if seed == 0:
        return -1
    slot = (-seed - 1) if seed < 0 else chunk_hash(seed, chunk_id) % len(chunk_ids)
    return slot if 0 <= slot < len(chunk_ids) and chunk_ids[slot] == chunk_id else -1


def build_perfect_hash(chunk_ids):
    """-> (slot order, seeds). `order[slot]` indexes into `chunk_ids`.

    The usual CHD construction: bucket by the unseeded hash, place the crowded
    buckets first with a seed that spreads them over free slots, then drop the
    single-chunk buckets into whatever is left and record the slot directly.
    """
    n = len(chunk_ids)
    for seed_count in range(max(1, n // 2), n * 4 + 2):
        order, seeds = _try_perfect_hash(chunk_ids, seed_count)
        if order is not None:
            for i, cid in enumerate(chunk_ids):          # prove it, cheaply
                if lookup([chunk_ids[o] for o in order], seeds, cid) < 0:
                    raise AssertionError('perfect hash does not resolve %s' % cid.hex())
            return order, seeds
    raise AssertionError('could not build a perfect hash for %d chunks' % n)


def _try_perfect_hash(chunk_ids, seed_count):
    n = len(chunk_ids)
    buckets = defaultdict(list)
    for i, cid in enumerate(chunk_ids):
        buckets[chunk_hash(0, cid) % seed_count].append(i)

    seeds = [0] * seed_count
    order = [None] * n
    free = [True] * n
    crowded = sorted((b for b in buckets.items() if len(b[1]) > 1),
                     key=lambda b: -len(b[1]))
    for bucket_index, members in crowded:
        for seed in range(1, 100000):
            slots = [chunk_hash(seed, chunk_ids[i]) % n for i in members]
            if len(set(slots)) == len(slots) and all(free[s] for s in slots):
                for i, s in zip(members, slots):
                    order[s], free[s] = i, False
                seeds[bucket_index] = seed
                break
        else:
            return None, None
    for bucket_index, members in buckets.items():
        if len(members) == 1:
            slot = free.index(True)
            order[slot], free[slot] = members[0], False
            seeds[bucket_index] = -slot - 1
    return order, seeds


# ------------------------------------------------------------ container header

class StoreEntry(object):
    """`FFilePackageStoreEntry` - what the loader needs to know about a package."""

    __slots__ = ('exports', 'bundles', 'imports', 'shader_hashes')

    def __init__(self, exports, bundles, imports, shader_hashes):
        self.exports = exports
        self.bundles = bundles
        self.imports = imports                  # [package id]
        self.shader_hashes = shader_hashes      # [20-byte FSHAHash]


def parse_container_header(data):
    """-> (container id, {package id: StoreEntry}) from a shipped container."""
    magic, version = struct.unpack_from('<II', data, 0)
    if magic != CONTAINER_HEADER_MAGIC:
        raise ValueError('not a container header (magic %08x)' % magic)
    if version != CONTAINER_HEADER_VERSION:
        raise ValueError('container header version %d, expected %d'
                         % (version, CONTAINER_HEADER_VERSION))
    container_id = struct.unpack_from('<Q', data, 8)[0]
    p = 16
    count = struct.unpack_from('<I', data, p)[0]; p += 4
    package_ids = struct.unpack_from('<%dQ' % count, data, p); p += 8 * count
    size = struct.unpack_from('<I', data, p)[0]; p += 4
    base = p
    entries = {}
    for k, pid in enumerate(package_ids):
        at = base + 24 * k
        exports, bundles = struct.unpack_from('<ii', data, at)
        imports = _read_array(data, at + 8, 'Q', 8)
        shaders = _read_array(data, at + 16, None, 20)
        entries[pid] = StoreEntry(exports, bundles, imports, shaders)
    if base + size > len(data):
        raise ValueError('store entries run past the end of the header')
    return container_id, entries


def _read_array(data, at, fmt, stride):
    """A `TFilePackageStoreEntryCArrayView`: count, then offset from itself."""
    count, offset = struct.unpack_from('<ii', data, at)
    start = at + offset                         # measured from the view itself
    if fmt is None:
        return [data[start + i * stride:start + (i + 1) * stride] for i in range(count)]
    return list(struct.unpack_from('<%d%s' % (count, fmt), data, start))


def build_container_header(container_id, entries):
    """entries: {package id: StoreEntry} -> the chunk bytes."""
    package_ids = sorted(entries)               # the loader binary-searches these
    fixed = bytearray(24 * len(package_ids))
    trailing = bytearray()
    for k, pid in enumerate(package_ids):
        e = entries[pid]
        at = 24 * k
        struct.pack_into('<ii', fixed, at, e.exports, e.bundles)
        for offset_at, items, pack in ((at + 8, e.imports, lambda v: struct.pack('<Q', v)),
                                       (at + 16, e.shader_hashes, lambda v: v)):
            # offset is measured from the field itself, and the data sits after
            # the fixed block - which is what makes it a forward offset here.
            data_at = len(fixed) + len(trailing)
            struct.pack_into('<ii', fixed, offset_at, len(items),
                             data_at - offset_at if items else 0)
            for item in items:
                trailing += pack(item)

    store = bytes(fixed) + bytes(trailing)
    out = struct.pack('<IIQ', CONTAINER_HEADER_MAGIC, CONTAINER_HEADER_VERSION,
                      container_id)
    out += struct.pack('<I', len(package_ids))
    out += struct.pack('<%dQ' % len(package_ids), *package_ids)
    out += struct.pack('<I', len(store)) + store
    # optional-segment package ids, their store entries, the redirect name map
    # and the localized-package table - all empty here, as in a mod container.
    out += struct.pack('<II', 0, 0) + struct.pack('<II', 0, 0) + struct.pack('<I', 0)
    return out


def container_id_for(name):
    """A container needs an id of its own; the name is the only input we have."""
    digest = hashlib.sha256(name.encode('utf-8')).digest()
    return struct.unpack('<Q', digest[:8])[0] & 0x7FFFFFFFFFFFFFFF


def package_data_chunk_id(package_id, index=0):
    return struct.pack('<QHBB', package_id, index, 0, 1)


def container_header_chunk_id(container_id):
    return struct.pack('<QHBB', container_id, 0, 0, 6)


# ------------------------------------------------------------ directory index

def build_directory_index(mount_point, files):
    """files: [(path below the mount point, chunk slot)] -> index bytes."""
    strings, string_index = [], {}

    def intern(s):
        if s not in string_index:
            string_index[s] = len(strings)
            strings.append(s)
        return string_index[s]

    # directory nodes: [name, first child, next sibling, first file]
    dirs = [[NONE, NONE, NONE, NONE]]
    children = {0: {}}
    file_nodes = []

    for path, slot in files:
        parts = path.split('/')
        node = 0
        for part in parts[:-1]:
            if part not in children[node]:
                new = len(dirs)
                dirs.append([intern(part), NONE, dirs[node][1], NONE])
                dirs[node][1] = new              # newest child first, as UE does
                children[node][part] = new
                children[new] = {}
            node = children[node][part]
        file_nodes.append([intern(parts[-1]), dirs[node][3], slot])
        dirs[node][3] = len(file_nodes) - 1

    out = _fstring(mount_point)
    out += struct.pack('<I', len(dirs))
    for d in dirs:
        out += struct.pack('<4I', *d)
    out += struct.pack('<I', len(file_nodes))
    for f in file_nodes:
        out += struct.pack('<3I', *f)
    out += struct.pack('<I', len(strings))
    for s in strings:
        out += _fstring(s)
    return out


def _fstring(s):
    b = s.encode('utf-8') + b'\0'
    return struct.pack('<i', len(b)) + b


# ----------------------------------------------------------------- the writer

def write_container(base_path, mount_point, chunks, container_id,
                    compression_method='Oodle'):
    """Write `<base_path>.utoc` / `.ucas` / `.pak`.

    chunks: [(12-byte chunk id, data, path below the mount point or None)],
    in whatever order; the perfect hash decides where each one lands.
    """
    try:
        from blake3 import blake3
    except ImportError:
        from blake3_pure import blake3

    chunk_ids = [c[0] for c in chunks]
    order, seeds = build_perfect_hash(chunk_ids)
    slot_of = {chunk_ids[c]: s for s, c in enumerate(order)}

    ucas = bytearray()
    blocks = []                                  # (offset, size, method)
    entries = [None] * len(chunks)               # (virtual offset, length)
    virtual = 0
    for slot in range(len(chunks)):
        _, data, _ = chunks[order[slot]]
        entries[slot] = (virtual, len(data))
        for at in range(0, max(len(data), 1), BLOCK_SIZE):
            piece = data[at:at + BLOCK_SIZE]
            ucas += b'\0' * (-len(ucas) % 16)     # the engine reads aligned
            blocks.append((len(ucas), len(piece), 0))
            ucas += piece
            virtual += BLOCK_SIZE

    indexed = [(path, slot_of[cid]) for cid, _, path in chunks if path]
    directory = build_directory_index(mount_point, sorted(indexed))

    header = bytearray(HEADER_SIZE)
    header[0:16] = TOC_MAGIC
    header[0x10] = TOC_VERSION
    struct.pack_into('<9I', header, 0x14, HEADER_SIZE, len(chunks), len(blocks),
                     12, 1, 32, BLOCK_SIZE, len(directory), 1)
    struct.pack_into('<Q', header, 0x38, container_id)
    header[0x50] = FLAG_COMPRESSED | FLAG_INDEXED
    struct.pack_into('<I', header, 0x54, len(seeds))
    struct.pack_into('<Q', header, 0x58, 0xFFFFFFFFFFFFFFFF)   # one partition
    struct.pack_into('<I', header, 0x60, 0)                    # none unhashed

    out = bytes(header)
    out += b''.join(chunk_ids[order[s]] for s in range(len(chunks)))
    out += b''.join(off.to_bytes(5, 'big') + length.to_bytes(5, 'big')
                    for off, length in entries)
    out += struct.pack('<%di' % len(seeds), *seeds)
    out += b''.join(off.to_bytes(5, 'little') + size.to_bytes(3, 'little')
                    + size.to_bytes(3, 'little') + bytes([method])
                    for off, size, method in blocks)
    out += compression_method.encode('ascii').ljust(32, b'\0')
    out += directory
    for slot in range(len(chunks)):
        data = chunks[order[slot]][1]
        out += blake3(data).digest(length=32)[:20] + b'\0' * 12 + b'\0'

    with open(base_path + '.ucas', 'wb') as f:
        f.write(ucas)
    with open(base_path + '.utoc', 'wb') as f:
        f.write(out)
    write_stub_pak(base_path + '.pak')
    return len(ucas) + len(out)


def write_stub_pak(path, mount_point='/'):
    """An empty .pak next to the container.

    The engine finds IoStore containers through pak mounting, so a mod needs a
    .pak even when every byte it ships lives in the .ucas. This is the smallest
    valid one: an index with no files at all.
    """
    path_hash_index = b'\0' * 8
    full_directory_index = b'\0' * 4

    index = _fstring(mount_point) + struct.pack('<i', 0)      # no entries
    index += struct.pack('<Q', 0)                             # path hash seed
    head = len(index) + 4 + 16 + 20 + 4 + 16 + 20 + 4 + 4     # where sub-indexes go
    index += struct.pack('<iqq', 1, head, len(path_hash_index))
    index += hashlib.sha1(path_hash_index).digest()
    index += struct.pack('<iqq', 1, head + len(path_hash_index),
                         len(full_directory_index))
    index += hashlib.sha1(full_directory_index).digest()
    index += struct.pack('<ii', 0, 0)          # encoded entries, files

    footer = b'\0' * 16 + b'\0'                # encryption guid, not encrypted
    footer += struct.pack('<IIqq', 0x5A6F12E1, 11, 0, len(index))
    footer += hashlib.sha1(index).digest()
    footer += b'\0' * (32 * 5)                 # compression method names

    with open(path, 'wb') as f:
        f.write(index + path_hash_index + full_directory_index + footer)


# ------------------------------------------------------------------ self-test

def _check(utoc_path):
    """Read a shipped container back through the format this module writes.

    Both halves of the format that had to be recovered rather than looked up
    are checkable against real files: the perfect hash must resolve every chunk
    the container holds, and the container header must rebuild byte for byte
    from what we parsed out of it.
    """
    import os, sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from iostore import Toc

    toc = Toc(utoc_path)
    ids = [toc.chunkids[i * 12:(i + 1) * 12] for i in range(toc.entries)]
    with open(utoc_path, 'rb') as f:
        f.seek(HEADER_SIZE + toc.entries * 22)
        seeds = list(struct.unpack('<%di' % toc.seeds_count, f.read(toc.seeds_count * 4)))
    missed = [i for i, cid in enumerate(ids) if lookup(ids, seeds, cid) != i]
    print('%s: %d chunks, perfect hash resolves %d' %
          (os.path.basename(utoc_path), len(ids), len(ids) - len(missed)))
    if missed:
        raise AssertionError('%d chunks do not resolve' % len(missed))

    for i in range(toc.entries):
        if toc.chunk_type(i) == 6:
            data = toc.read(i)
            container_id, entries = parse_container_header(data)
            same = build_container_header(container_id, entries) == data
            print('   container header: %d packages, rebuild %s'
                  % (len(entries), 'identical' if same else 'DIFFERS'))
            if not same:
                raise AssertionError('container header did not round-trip')
            break


if __name__ == '__main__':
    import sys
    if len(sys.argv) < 2:
        print('usage: python container.py <container>.utoc [...]')
        raise SystemExit(2)
    for path in sys.argv[1:]:
        _check(path)
