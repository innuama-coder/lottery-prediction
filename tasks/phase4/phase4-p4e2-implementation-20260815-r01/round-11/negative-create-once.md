# Create-once negative checks

After successful closure, the exact r12 build command was repeated. It exited 1
with:

```text
FileExistsError: release identity already exists; formal releases are create-once
```

The exact r12 finalizer command was repeated. It exited 1 with:

```text
FileExistsError: D15 final files already exist; release identity is immutable
```

The finalized 178-file inventory was unchanged after both negative checks.
Deletion and tamper behavior for final closure, create-once locks, identities,
lineage, probabilities, and order is additionally covered by A02 and the 28
independent replay mutations.
