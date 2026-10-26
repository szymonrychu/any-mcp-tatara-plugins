# any-mcp-tatara-plugins

The bucket of platform-specific plugins for [any-mcp](https://github.com/szymonrychu/any-mcp),
grouped by topic. any-mcp itself stays generic; tools that only make sense on the claude-code-tatara
platform live here, and any-mcp installs them on demand from a source string.

## Layout

Every plugin is its own Python package under a topic directory, `<topic>/<plugin>/`:

```
<topic>/<plugin>/
    pyproject.toml   # the package, with an [project.entry-points."any_mcp.plugins"] table
    src/<module>/    # the plugin code
    tests/           # the plugin tests, collected by the repo-level test run
```

The repo root is a uv workspace (not a package): every `<topic>/<plugin>` directory holding a
`pyproject.toml` is a member, and `mise run test` runs the repo tests in `tests/` together with every
plugin's `tests/`.

## Development

Python 3.12, uv and the tasks are pinned in `.mise.toml`:

    mise run install   # uv sync of every workspace member and the dev group
    mise run test      # pytest over tests/ and every <topic>/<plugin>/tests/
    mise run lint      # ruff check and ruff format --check
    mise run fmt       # ruff format and ruff check --fix

CI runs `mise run install`, `mise run lint` and `mise run test` on every push and pull request.

## Adding a plugin

1. Pick a topic directory (create it if no existing topic fits) and add `<topic>/<plugin>/`.
2. Write `<topic>/<plugin>/pyproject.toml`: a hatchling package with `requires-python = ">=3.12"`,
   `any-mcp` as an unversioned dependency (the host any-mcp satisfies it), and the entry point any-mcp
   loads the plugin by:

   ```toml
   [project.entry-points."any_mcp.plugins"]
   <name> = "<module>:<PluginClass>"
   ```

3. Put the code in `<topic>/<plugin>/src/<module>/` and the tests in `<topic>/<plugin>/tests/`.
4. Run `mise run install && mise run test && mise run lint`; the repo tests check that the new
   directory is a workspace member with an entry point and collected tests.

## Source notation

any-mcp references a plugin by a source string, a subset of the Terraform module notation:

- Local directory, used in place: a path starting with `./`, `../` or `/`, for example
  `./orchestration/orchestrator` or `/srv/any-mcp-tatara-plugins/<topic>/<plugin>`.
- Git repository, a subdirectory after `//` and a tag after `?ref=`:
  `git::https://github.com/szymonrychu/any-mcp-tatara-plugins.git//<topic>/<plugin>?ref=vX.Y.Z`
- GitHub shorthand for the same:
  `github.com/szymonrychu/any-mcp-tatara-plugins//<topic>/<plugin>?ref=vX.Y.Z`

Install a plugin from the command line:

    any-mcp plugins install ./orchestration/orchestrator
    any-mcp plugins install github.com/szymonrychu/any-mcp-tatara-plugins//<topic>/<plugin>?ref=vX.Y.Z

or name the source in an any-mcp config entry, which installs it on load:

```yaml
plugins:
  - name: <name>
    source: github.com/szymonrychu/any-mcp-tatara-plugins//<topic>/<plugin>?ref=vX.Y.Z
```

## Releases

Releases are `vX.Y.Z` tags on `main`; pin `?ref=` to a release tag. Nothing is published beyond the
tag.
