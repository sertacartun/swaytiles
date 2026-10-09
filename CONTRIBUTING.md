# Contributing

Short and simple. `main` always works; all changes reach it through a branch.

## Branches

Never commit to `main` directly. Make a branch from the latest `main`:

| Prefix      | For                                   | Example                     |
|-------------|---------------------------------------|-----------------------------|
| `feature/`  | something new                         | `feature/scratchpad-layout` |
| `fix/`      | a bug                                 | `fix/flicker-on-output-move`|
| `docs/`     | documentation only                    | `docs/layouts-examples`     |
| `refactor/` | no behavior change                    | `refactor/split-model`      |
| `test/`     | tests only                            | `test/drag-edge-cases`      |

Use lowercase and hyphens, and keep the name short. One branch does one thing.
Delete the branch after it is merged.

## Commits

One line, in plain words, saying what changes for the user:

    A window dragged with the mouse stays where it is dropped

No `fix:` or `feat:` prefix is needed. The branch name already says the kind.

## Pull requests

- Target `main`, keep it small.
- Fill in the template: what changed, why, how it was solved, and the checks.
- Merge when the tests are green.
