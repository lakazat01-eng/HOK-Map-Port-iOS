# HOK Map Port iOS

A local analysis and conversion workspace for comparing Honor of Kings Android and iOS map resources.

## Project status

- [x] Repository initialized
- [ ] Collect Android map archive
- [ ] Collect matching iOS map archive
- [ ] Inventory files and identify corresponding assets
- [ ] Determine platform-specific formats and dependencies
- [ ] Implement safe, reversible conversion steps
- [ ] Validate in-game

## Workflow

1. Keep original Android and iOS archives unchanged in local storage.
2. Run the inventory script against extracted copies.
3. Compare paths, sizes, hashes, file signatures, and likely Unity asset metadata.
4. Review the report before any conversion.
5. Write converted output to a separate directory; never overwrite originals.

## Data policy

Do not commit game archives, extracted proprietary assets, account data, or other copyrighted game resources. Commit scripts, documentation, and small diagnostic text reports only.

## Requirements

The initial inventory tool uses Python 3 standard library only and is intended to support Python 3.9+.
