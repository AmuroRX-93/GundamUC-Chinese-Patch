"""Bounded, standard-library BSDIFF40 decoder for per-resource patch segments.
The builder may use bsdiff4; end users do not need that dependency.
"""
import bz2
import hashlib
import struct

MAX_SEGMENT = 64 * 1024 * 1024

def signed(data):
    if len(data) != 8:
        raise ValueError('Truncated BSDIFF integer')
    value = int.from_bytes(data, 'little')
    return -(value & ((1 << 63) - 1)) if value >> 63 else value

def unpack_block(data, limit):
    decoder = bz2.BZ2Decompressor()
    out = decoder.decompress(data, max_length=limit + 1)
    if len(out) > limit or not decoder.eof or decoder.unused_data:
        raise ValueError('Invalid or oversized compressed patch block')
    return out

def apply(old, patch, expected_size, expected_sha256):
    if not 0 <= expected_size <= MAX_SEGMENT or len(old) > MAX_SEGMENT:
        raise ValueError('Patch segment exceeds memory limit')
    if len(patch) < 32 or patch[:8] != b'BSDIFF40':
        raise ValueError('Invalid BSDIFF header')
    control_size, diff_size, size = [signed(patch[i:i+8]) for i in (8, 16, 24)]
    if min(control_size, diff_size) < 0 or size != expected_size or 32 + control_size + diff_size > len(patch):
        raise ValueError('Invalid BSDIFF sizes')
    p = 32 + control_size
    control = unpack_block(patch[32:p], 24 * (size + 1))
    diff = unpack_block(patch[p:p+diff_size], size)
    extra = unpack_block(patch[p+diff_size:], size)
    if len(control) % 24:
        raise ValueError('Truncated control block')
    result = bytearray()
    oldpos = dc = ec = 0
    for cursor in range(0, len(control), 24):
        x, y, seek = [signed(control[i:i+8]) for i in range(cursor, cursor+24, 8)]
        if min(x, y) < 0 or len(result)+x+y > size or dc+x > len(diff) or ec+y > len(extra):
            raise ValueError('Invalid BSDIFF operation')
        block = bytearray(diff[dc:dc+x])
        for i in range(max(0, -oldpos), min(x, len(old)-oldpos)):
            block[i] = (block[i] + old[oldpos+i]) & 255
        result.extend(block)
        result.extend(extra[ec:ec+y])
        oldpos += x + seek
        dc += x
        ec += y
    if len(result) != size or dc != len(diff) or ec != len(extra):
        raise ValueError('Incomplete BSDIFF output')
    if hashlib.sha256(result).hexdigest() != expected_sha256:
        raise ValueError('Reconstructed resource hash mismatch')
    return bytes(result)
