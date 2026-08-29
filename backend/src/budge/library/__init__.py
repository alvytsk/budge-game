"""§5.3's content library: two ordinary tables, one writer, two ports.

Deliberately not event-sourced. The library outlives every match, and its
link to the log is one-way — `AttackDeclared` records the image identifiers
it drew, in the order it drew them, so an edit here cannot reach back into
a duel that has already been played.
"""
