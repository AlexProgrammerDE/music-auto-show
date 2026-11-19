# Contribute to music-auto-show

Contributions can fix behavior, improve documentation, or add focused tests.

## Before you start

Read [the support guide](SUPPORT.md) for questions and issue routing.
Search existing issues and pull requests. Discuss larger API, architecture, or dependency changes before implementation.

Work from `main` and target that branch in your pull request.
Keep each change focused. Avoid unrelated formatting and dependency updates.

## Prepare a checkout

Use Rust and the Bun version in `.bun-version`. Install native audio/USB prerequisites from [README](README.md).

```bash
bun install --frozen-lockfile
bun install --cwd frontend --frozen-lockfile
```

Run the commands below from the repository root unless a command names another directory.
On Windows, use `gradlew.bat` in place of `./gradlew` for Gradle commands.

## Repository layout

- `src/`: Rust audio, analysis, effects, DMX, and API implementation.
- `frontend/`: React SPA.
- `proto/`: shared protobuf contract.
- `fixtures/`: fixture definitions.
- `docs/`: architecture and migration notes.

## Verify your change

```bash
bun run check
cargo fmt --all -- --check
```

Read [AGENTS.md](AGENTS.md). Start with `cargo run -- --simulate` for local work. Real audio/DMX changes also need a hardware check. Keep the real-time path bounded and preserve fallback behavior. Change protobuf definitions first, then run `bun run proto:generate`. Do not edit generated frontend files or preset UI components. Do not commit model checkpoints. A release build uses `bun run build`.

Run the relevant checks before review. State the command and result in the pull request.
If a check cannot run, explain the missing dependency or service. Do not claim it passed.
Keep generated artifacts consistent with their source and review their diff.

## Fuzzing

The `fuzz/` crate holds cargo-fuzz targets for the grandMA2 fixture importer, the show configuration loaders, and the BeatNet+ checkpoint integrity checks.
It is a separate Cargo workspace, so `cargo build`, Clippy, and cargo-deny of the main project ignore it.
The [Fuzz workflow](.github/workflows/fuzz.yml) runs each target for 60 seconds when a parser changes and for 10 minutes every week.

Install cargo-fuzz 0.13.2 and `nightly-2026-07-09`.
Newer nightlies, including `nightly-2026-11-01`, fail to compile `zbus`.
Run the commands below from `fuzz/`:

```bash
# List the targets
cargo +nightly-2026-07-09 fuzz list

# Run one target for 5 minutes, starting from its seed files
mkdir -p corpus/config_json
cargo +nightly-2026-07-09 fuzz run config_json corpus/config_json seeds/config_json -- -max_total_time=300 -rss_limit_mb=2048 -timeout=10

# Reproduce and minimize a failing input
cargo +nightly-2026-07-09 fuzz run config_json artifacts/config_json/<file>
cargo +nightly-2026-07-09 fuzz tmin config_json artifacts/config_json/<file>
```

Keep `fuzz/seeds/<target>/` small and use only synthetic data. Never add a real BeatNet+ checkpoint.
When a target finds a bug, fix the parser and add a unit test that uses the minimized input.

## Style and documentation

Follow the existing code conventions and repository formatter. Keep commit hooks enabled.
Add focused tests for changed logic when practical. Avoid tests that only assert source strings.
Update documentation when commands, APIs, configuration, or expected behavior change.
Keep examples small and reproducible. Preserve exact identifiers, commands, and error messages.

## Open a pull request

Explain the problem and resulting behavior. Link related issues without a placeholder issue number.
State simulation and hardware results separately. Identify API, fixture, or configuration compatibility changes.
Include commands and results. State any runtime checks that remain necessary.
Respond to review with a correction or concrete evidence.

Use Conventional Commits: `type(scope): description`, for example `docs(contributing): explain local validation`.
Use a meaningful scope, or omit it. Keep the subject concise and imperative.
Add a body when the reason or compatibility impact is not obvious.

For vulnerabilities, follow [the security reporting instructions](SECURITY.md).
Remove credentials and private data from examples, logs, and screenshots.

## Git hooks

`bun install` installs Lefthook for this checkout and preserves the existing preparation steps.
Run `bun run pre-commit` to run the commit checks manually.
Before each commit, Lefthook runs Oxlint, Oxfmt, and rustfmt for the staged files that match each check.
The commit message hook checks the first line against the Conventional Commit rules in [AGENTS.md](AGENTS.md).

Before a push, Lefthook runs the checks that match the changed files.
It checks protobuf formatting, lints protobuf definitions, and generates frontend types when the relevant files change.
It also runs the frontend checks, Clippy, and Rust tests when their inputs change.
Install cargo-deny 0.20.2 for pushes that change `Cargo.toml`, `Cargo.lock`, or `deny.toml`.
Run `cargo deny --locked check` to check Rust dependencies locally.
