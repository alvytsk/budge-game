"""§10's «бэкапы по расписанию с учениями по восстановлению».

Two commands, not one. `dump` takes a backup; `drill` proves one can be
restored. The second exists because the first is not evidence: an archive
that `pg_dump` wrote and nobody ever read back is a file, not a backup.

Both live in the application package rather than in a shell script for the
reason ruling I2 gives — the drill folds the restored log through the
domain's own `fold`, so it has to be able to import it.
"""
