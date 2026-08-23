"""§5.3's `media`: immutable bytes, addressed by their own sha256 (§7.6).

Separate from `library/` on purpose. The library is rows in PostgreSQL that
an operator edits; media is bytes in an object store that nobody edits,
ever. They meet at one column, `images.media_sha256`, and §5.3 lists them
apart for the same reason.
"""
