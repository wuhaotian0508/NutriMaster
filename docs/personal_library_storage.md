# Personal-library storage safety

Each user's index is stored as immutable generations below
`<personal_lib>/<user>/index/generations/`. A generation contains `chunks.pkl`,
`embeddings.npy`, `embedding_norms.npy`, `manifest.json`, and a final
`COMMITTED.json` marker. `CURRENT` is the only visibility pointer.

Writers build and fsync a private transaction directory, validate it by reading
the complete snapshot, rename it into the generations directory, and only then
atomically replace `CURRENT`. Therefore SIGKILL, OOM, or a caught write failure
before the pointer switch leaves the prior generation active. The `CURRENT`
marker also records the prior generation so startup can fall back if the newest
generation later fails validation. Dense arrays and norms remain memory-mapped,
and generation construction still writes and validates them in bounded blocks.

Old flat files are read only when `CURRENT` does not exist, which preserves
upgrade compatibility. The first subsequent successful mutation publishes the
generation layout; once `CURRENT` exists, flat files and incomplete transaction
directories are never considered loadable state.
